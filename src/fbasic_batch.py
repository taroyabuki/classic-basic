"""F-BASIC file load and batch helpers.

This module drives supported MAME drivers through generated Lua autoboot
scripts. FM-7 / FM-77AV batch runs read the subsystem console RAM directly.
Other drivers still fall back to screen capture and OCR.
"""
from __future__ import annotations

import argparse
import difflib
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path


_INPUT_START_FRAME = 240
_INPUT_GAP_FRAMES = 60
_PRE_RUN_SETTLE_FRAMES = 180
_DEFAULT_POST_RUN_SETTLE_FRAMES = 1800
_OCR_OUTPUT_Y_START_RATIO = 0.0
_OCR_OUTPUT_Y_STOP_RATIO = 1.0
_OCR_WHITELIST = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:;+-*/()[]"!?@=_ '
_KEEP_TEMP = os.environ.get("CLASSIC_BASIC_KEEP_TEMP", "") not in {"", "0", "false", "False"}
_FORCE_HEADFUL = os.environ.get("CLASSIC_BASIC_FM_BATCH_HEADLESS", "") in {"0", "false", "False"}
_BATCH_MARKER = "CBATCHBEGIN"
_FM7_BATCH_POLL_INTERVAL = 0.1
_FM7_DEFAULT_LOAD_SETTLE_SECONDS = 0.35
_FM7_DEFAULT_RUN_SETTLE_SECONDS = 0.25
_FM7_CONSOLE_BASE = 0xC000
_FM7_CONSOLE_COLUMNS = 40
_FM7_CONSOLE_ROWS = 25
_SOURCE_LISTING_KEYWORDS = (
    "PRINT",
    "FOR",
    "NEXT",
    "IF",
    "THEN",
    "GOTO",
    "GOSUB",
    "RETURN",
    "POKE",
    "PEEK",
    "VARPTR",
    "ATN",
    "ABS",
    "SQR",
    "INT",
    "MID$",
    "MKD$",
    "RUN",
    "END",
    "REM",
)


class UnsupportedProgramInputError(RuntimeError):
    """Raised when batch execution appears to block on BASIC program input."""


def _parent_death_preexec_fn() -> Callable[[], None] | None:
    if not sys.platform.startswith("linux"):
        return None

    def _set_parent_death_signal() -> None:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))

    return _set_parent_death_signal


def parse_timeout_spec(spec: str | None) -> float | None:
    if spec is None:
        return None
    text = spec.strip()
    if text in {"", "0"}:
        return None

    match = re.fullmatch(r"([0-9]+(?:\.[0-9]*)?|[0-9]*\.[0-9]+)\s*([smhdSMHD]?)", text)
    if match is None:
        raise ValueError(f"invalid timeout spec: {spec}")

    value = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    factor = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[unit]
    return value * factor


def post_run_settle_frames(driver: str) -> int:
    raw_value = os.environ.get("CLASSIC_BASIC_FM_BATCH_SETTLE_FRAMES", "")
    if not raw_value.strip():
        if driver in {"fm7", "fm77av"}:
            return 600
        return _DEFAULT_POST_RUN_SETTLE_FRAMES
    value = int(raw_value)
    if value < 0:
        raise ValueError("CLASSIC_BASIC_FM_BATCH_SETTLE_FRAMES must be non-negative")
    return value


def pre_run_settle_frames(driver: str) -> int:
    raw_value = os.environ.get("CLASSIC_BASIC_FM_BATCH_PRE_RUN_SETTLE_FRAMES", "")
    if raw_value.strip():
        value = int(raw_value)
        if value < 0:
            raise ValueError("CLASSIC_BASIC_FM_BATCH_PRE_RUN_SETTLE_FRAMES must be non-negative")
        return value
    if driver in {"fm7", "fm77av"}:
        return 180
    return _PRE_RUN_SETTLE_FRAMES


def input_gap_frames(driver: str) -> int:
    raw_value = os.environ.get("CLASSIC_BASIC_FM_BATCH_INPUT_GAP_FRAMES", "")
    if raw_value.strip():
        value = int(raw_value)
        if value <= 0:
            raise ValueError("CLASSIC_BASIC_FM_BATCH_INPUT_GAP_FRAMES must be positive")
        return value
    if driver in {"fm7", "fm77av"}:
        return 60
    return _INPUT_GAP_FRAMES


def run_delay_frames(driver: str) -> int:
    if driver in {"fm7", "fm77av"}:
        return 720
    return input_gap_frames(driver)


def normalize_program_lines(program_path: Path) -> list[str]:
    data = program_path.read_bytes()
    text = data.decode("ascii")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [_normalize_program_line(line.rstrip()) for line in text.split("\n") if line.strip()]
    if not lines:
        raise ValueError(f"program is empty: {program_path}")
    for line in lines:
        if re.match(r"^\d+\s", line) is None:
            raise ValueError(
                f"F-BASIC file injection currently requires line-numbered source; got: {line!r}"
            )
    return lines


def _normalize_program_line(line: str) -> str:
    out: list[str] = []
    in_string = False
    index = 0
    while index < len(line):
        char = line[index]
        if char == '"':
            in_string = not in_string
            out.append(char)
            index += 1
            continue
        if not in_string and line[index : index + 3].upper() == "REM":
            prev = line[index - 1] if index > 0 else ""
            next_char = line[index + 3] if index + 3 < len(line) else ""
            if not (prev.isalnum() or prev in "$#") and not (next_char.isalnum() or next_char in "$#"):
                out.append(line[index:])
                break
        out.append(char)
        index += 1
    return "".join(out)


def _render_pixels_to_image(pixel_path: Path, image_path: Path) -> None:
    from PIL import Image

    rows = pixel_path.read_text(encoding="ascii").splitlines()
    if not rows:
        raise RuntimeError(f"captured pixel dump is empty: {pixel_path}")

    width = len(rows[0])
    height = len(rows)
    image = Image.new("L", (width, height), 0)
    pixels = image.load()
    for y, row in enumerate(rows):
        for x, cell in enumerate(row):
            if cell == "1":
                pixels[x, y] = 255

    image = image.resize((image.width, image.height * 2), Image.Resampling.NEAREST)
    image.save(image_path)


def _pixel_dump_has_signal(pixel_path: Path) -> bool:
    if not pixel_path.exists():
        return False
    with pixel_path.open("r", encoding="ascii", errors="replace") as handle:
        for line in handle:
            if "1" in line:
                return True
    return False


def _ocr_lines(image_path: Path, *, whitelist: str | None) -> list[str]:
    from PIL import Image, ImageFilter, ImageOps

    image = Image.open(image_path).convert("L")
    crop = image.crop(
        (
            0,
            int(image.height * _OCR_OUTPUT_Y_START_RATIO),
            image.width,
            int(image.height * _OCR_OUTPUT_Y_STOP_RATIO),
        )
    )
    crop = ImageOps.autocontrast(crop)
    crop = crop.filter(ImageFilter.MedianFilter(3))
    crop_path = image_path.with_suffix(".crop.png")
    crop.save(crop_path)

    cmd = ["tesseract", str(crop_path), "stdout", "--psm", "6"]
    if whitelist:
        cmd += ["-c", f"tessedit_char_whitelist={whitelist}"]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _capture_xwd_to_png(window_id: str, image_path: Path, *, env: dict[str, str]) -> None:
    xwd_path = image_path.with_suffix(".xwd")
    subprocess.run(
        ["xwd", "-id", window_id, "-silent", "-out", str(xwd_path)],
        check=True,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with image_path.open("wb") as png_file:
        xwdtopnm = subprocess.Popen(
            ["xwdtopnm", str(xwd_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        try:
            subprocess.run(
                ["pnmtopng"],
                stdin=xwdtopnm.stdout,
                stdout=png_file,
                stderr=subprocess.DEVNULL,
                check=True,
                env=env,
            )
        finally:
            if xwdtopnm.stdout is not None:
                xwdtopnm.stdout.close()
            xwdtopnm.wait()


def _normalize_ocr_text(text: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def is_run_line(text: str) -> bool:
    return re.fullmatch(r"R[UO0]N[S]?", _normalize_ocr_text(text)) is not None


def is_ready_line(text: str) -> bool:
    return _normalize_ocr_text(text) in {"READY", "READU", "REAOY", "READY0", "R"}


def is_batch_marker_line(text: str) -> bool:
    return _BATCH_MARKER in _normalize_ocr_text(text)


def is_input_prompt_line(text: str) -> bool:
    stripped = text.strip().upper()
    if stripped in {"?", "IN?"}:
        return True
    normalized = _normalize_ocr_text(text)
    if normalized in {"INPUT", "INPUT?", "INPUTA", "INPUTA$", "INPUTB", "INPUTB$"}:
        return True
    if normalized.startswith("INPUT") and len(normalized) > len("INPUT"):
        return True
    return False


def is_source_listing_line(text: str) -> bool:
    match = re.match(r"^\s*\d+\s*(.*)$", text)
    if match is None:
        return False
    body = match.group(1).lstrip()
    if not body:
        return False
    if not (body[0].isalpha() or body[0] in {"?", "'", ":"}):
        return False
    body = body.upper()
    if any(keyword in body for keyword in _SOURCE_LISTING_KEYWORDS):
        return True
    if any(token in body for token in ("=", "+", "-", "*", "/", "(", ")", '"', "<", ">")):
        return True
    if re.search(r"\b[A-Z]\b", body) is not None:
        return True
    return False


def is_startup_banner_line(text: str) -> bool:
    stripped = text.strip()
    upper = stripped.upper()
    if not stripped:
        return False
    if upper.startswith("FUJITSU F-BASIC VERSION"):
        return True
    if upper.startswith("NEC N-88 BASIC VERSION"):
        return True
    if upper.startswith("COPYRIGHT"):
        return True
    if upper == "ALL RIGHT RESERVED":
        return True
    return re.fullmatch(r"\d+\s+BYTES\s+FREE", upper) is not None


def _extract_output_lines_from_candidate(lines: list[str]) -> list[str]:
    saw_marker_or_run = False
    start = 0
    for index, line in enumerate(lines):
        if is_batch_marker_line(line):
            start = index + 1
            saw_marker_or_run = True
    if start != 0:
        for index in range(start, len(lines)):
            if is_run_line(lines[index]):
                start = index + 1
    else:
        for index, line in enumerate(lines):
            if is_run_line(line):
                start = index + 1
                saw_marker_or_run = True
    if start == 0 and saw_marker_or_run:
        for index, line in enumerate(lines):
            if is_ready_line(line):
                start = index + 1

    if not saw_marker_or_run:
        ready_index = next((index for index, line in enumerate(lines) if is_ready_line(line)), len(lines))
        output_before_ready: list[str] = []
        for line in lines[:ready_index]:
            if (
                not line
                or is_batch_marker_line(line)
                or is_run_line(line)
                or is_ready_line(line)
                or is_startup_banner_line(line)
                or is_source_listing_line(line)
                or re.fullmatch(r"[_\W]+", line) is not None
            ):
                continue
            output_before_ready.append(line)
        if output_before_ready:
            return output_before_ready

    end = len(lines)
    for index in range(start, len(lines)):
        if is_ready_line(lines[index]):
            end = index
            break

    output: list[str] = []
    for line in lines[start:end]:
        if (
            not line
            or is_batch_marker_line(line)
            or is_run_line(line)
            or is_ready_line(line)
            or is_startup_banner_line(line)
            or is_source_listing_line(line)
            or re.fullmatch(r"[_\W]+", line) is not None
        ):
            continue
        output.append(line)
    return output


def extract_output_lines(lines_plain: list[str], lines_filtered: list[str]) -> list[str]:
    candidates: list[list[str]] = []
    for candidate in (lines_filtered, lines_plain):
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        output = _extract_output_lines_from_candidate(candidate)
        if output:
            return output

    return []


def _extract_string_literals(program_lines: list[str]) -> list[str]:
    literals: list[str] = []
    for line in program_lines:
        for match in re.finditer(r'"([^"]+)"', line):
            literal = match.group(1).strip()
            if literal and literal not in literals:
                literals.append(literal)
    return literals


def _repair_output_lines(output_lines: list[str], literals: list[str]) -> list[str]:
    if not output_lines or not literals:
        return output_lines
    repaired: list[str] = []
    for line in output_lines:
        stripped = line.strip()
        best_literal = stripped
        best_ratio = 0.0
        for literal in literals:
            ratio = difflib.SequenceMatcher(None, stripped.upper(), literal.upper()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_literal = literal
        if best_ratio >= 0.6:
            repaired.append(best_literal)
        else:
            repaired.append(stripped)
    return repaired


def _lua_quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fm7_snapshot_contains_trigger(snapshot: list[str]) -> bool:
    for line in snapshot:
        normalized = _normalize_ocr_text(line)
        if normalized == "RUN" or _BATCH_MARKER in normalized:
            return True
    return False


def _fm7_snapshot_contains_run(snapshot: list[str]) -> bool:
    return any(_normalize_ocr_text(line) == "RUN" for line in snapshot)


def _fm7_snapshot_has_ready_after_run(snapshot: list[str]) -> bool:
    saw_run = False
    for line in snapshot:
        normalized = _normalize_ocr_text(line)
        if normalized == "RUN":
            saw_run = True
        elif saw_run and normalized == "READY":
            return True
    return False


class _FM7BatchProgress:
    def __init__(self) -> None:
        self.saw_trigger = False
        self.saw_run = False
        self.saw_ready_after_run = False

    def observe(self, snapshot: list[str]) -> bool:
        if _fm7_snapshot_contains_trigger(snapshot):
            self.saw_trigger = True
        if _fm7_snapshot_contains_run(snapshot):
            self.saw_run = True
        if _fm7_snapshot_has_ready_after_run(snapshot):
            self.saw_ready_after_run = True
        return bool(snapshot) and self.saw_trigger and self.saw_run and self.saw_ready_after_run


def _fm7_default_settle_seconds(driver: str, *, phase: str) -> float:
    if driver not in {"fm7", "fm77av"}:
        return 0.0

    if phase == "load":
        raw_value = os.environ.get("CLASSIC_BASIC_FM_BATCH_PRE_RUN_SETTLE_FRAMES", "")
        if raw_value.strip():
            value = int(raw_value)
            if value < 0:
                raise ValueError("CLASSIC_BASIC_FM_BATCH_PRE_RUN_SETTLE_FRAMES must be non-negative")
            return value / 60.0
        return _FM7_DEFAULT_LOAD_SETTLE_SECONDS

    raw_value = os.environ.get("CLASSIC_BASIC_FM_BATCH_SETTLE_FRAMES", "")
    if raw_value.strip():
        value = int(raw_value)
        if value < 0:
            raise ValueError("CLASSIC_BASIC_FM_BATCH_SETTLE_FRAMES must be non-negative")
        return value / 60.0
    return _FM7_DEFAULT_RUN_SETTLE_SECONDS


def _sleep_until_poll(
    *,
    deadline: float | None,
    poll_interval: float,
    command: list[str],
    timeout_seconds: float | None,
) -> None:
    if deadline is None:
        time.sleep(poll_interval)
        return
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, timeout_seconds if timeout_seconds is not None else 0.0)
    time.sleep(min(poll_interval, remaining))


def _append_lines_to_input_queue(input_path: Path, lines: list[str]) -> None:
    with input_path.open("a", encoding="ascii") as queue_handle:
        for line in lines:
            queue_handle.write(f"{line}\n")
        queue_handle.flush()


def _terminate_launched_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    pid = getattr(proc, "pid", None)
    terminated = False
    if isinstance(pid, int):
        try:
            os.killpg(pid, signal.SIGTERM)
            terminated = True
        except OSError:
            terminated = False
    if not terminated:
        proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        killed = False
        if isinstance(pid, int):
            try:
                os.killpg(pid, signal.SIGKILL)
                killed = True
            except OSError:
                killed = False
        if not killed:
            proc.kill()
        proc.wait(timeout=5.0)


def _wait_for_fm7_snapshot(
    *,
    proc: subprocess.Popen[bytes],
    output_path: Path,
    progress: _FM7BatchProgress,
    settle_seconds: float,
    command: list[str],
    timeout_seconds: float | None,
    deadline: float | None,
    exit_error: str,
    emit_output_line: Callable[[str], None] | None = None,
) -> list[str]:
    previous_snapshot: list[str] | None = None
    stable_since: float | None = None
    previous_output_lines: list[str] | None = None

    while True:
        snapshot = _read_text_capture_lines(output_path)
        ready = progress.observe(snapshot)
        output_lines = extract_output_lines(snapshot, snapshot) if progress.saw_trigger and progress.saw_run else []
        if any(is_input_prompt_line(line) for line in output_lines):
            raise UnsupportedProgramInputError("program input is not supported in --run mode")
        if emit_output_line is not None and output_lines:
            diff_lines = _emit_console_diff(previous_output_lines or [], output_lines)
            for line in diff_lines:
                emit_output_line(line)
            previous_output_lines = list(output_lines)

        if ready:
            if snapshot != previous_snapshot:
                previous_snapshot = list(snapshot)
                stable_since = time.monotonic()
            elif stable_since is None:
                stable_since = time.monotonic()

            if stable_since is not None and time.monotonic() - stable_since >= settle_seconds:
                return snapshot
        else:
            previous_snapshot = list(snapshot) if snapshot else None
            stable_since = None

        if proc.poll() is not None:
            if ready:
                return snapshot
            raise RuntimeError(exit_error)

        _sleep_until_poll(
            deadline=deadline,
            poll_interval=_FM7_BATCH_POLL_INTERVAL,
            command=command,
            timeout_seconds=timeout_seconds,
        )


def _run_fm7_batch_capture(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    disk_path: Path | None,
    extra_mame_args: list[str],
    program_lines: list[str],
    temp_dir: Path,
    timeout_seconds: float | None,
) -> list[str]:
    command = [mame_command, driver]
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    progress = _FM7BatchProgress()
    input_path = temp_dir / "input.txt"
    output_path = temp_dir / "screen.txt"
    lua_path = temp_dir / "interactive.lua"
    queued_lines = [*program_lines, f'PRINT "{_BATCH_MARKER}"', "RUN"]
    input_path.write_text("".join(f"{line}\n" for line in queued_lines), encoding="ascii")
    output_path.write_text("", encoding="ascii")
    _build_fm7_interactive_lua(lua_path, input_path=input_path, output_path=output_path)

    proc = launch_mame(
        mame_command=mame_command,
        rompath=rompath,
        driver=driver,
        disk_path=disk_path,
        lua_path=lua_path,
        extra_mame_args=extra_mame_args,
        headless=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        return _wait_for_fm7_snapshot(
            proc=proc,
            output_path=output_path,
            progress=progress,
            settle_seconds=_fm7_default_settle_seconds(driver, phase="run"),
            command=command,
            timeout_seconds=timeout_seconds,
            deadline=deadline,
            exit_error="unable to detect FM7 batch execution in console RAM",
            emit_output_line=lambda line: print(line, flush=True),
        )
    finally:
        _terminate_launched_process(proc)


def _build_capture_lua(
    lua_path: Path,
    *,
    driver: str,
    program_line_count: int,
    pixel_output_path: Path | None,
    send_marker: bool,
    send_run: bool,
    capture_frame: int | None,
    run_frame: int | None,
    marker_frame: int | None,
) -> None:
    dismiss_warning_block = ""
    if send_marker or send_run:
        dismiss_warning_block = """
  if frame >= 30 and frame <= 210 and ((frame - 30) % 30) == 0 then
    keyboard:post(" ")
  end
"""
    warning_clear_seconds = 6
    input_start_seconds = max(6, _INPUT_START_FRAME // 60)
    input_gap_seconds = max(1, input_gap_frames(driver) // 60)
    marker_delay_seconds = max(3, pre_run_settle_frames(driver) // 60)
    run_delay_seconds = max(3, run_delay_frames(driver) // 60)
    capture_delay_seconds = max(3, post_run_settle_frames(driver) // 60)
    marker_second = input_start_seconds + input_gap_seconds * program_line_count + marker_delay_seconds
    run_second = marker_second + run_delay_seconds
    capture_second = run_second + capture_delay_seconds
    capture_block = ""
    if pixel_output_path is not None and capture_frame is not None:
        capture_block = f"""
  if elapsed >= {capture_delay_seconds} then
    local f = assert(io.open("{pixel_output_path}", "w"))
    local w, h = screen.width, screen.height
    for y = 0, h - 1 do
      local row = {{}}
      for x = 0, w - 1 do
        row[#row + 1] = screen:pixel(x, y) == 0xff000000 and "0" or "1"
      end
      f:write(table.concat(row), "\\n")
    end
    f:close()
    manager.machine:exit()
    return
  end
"""
    text = f"""\
local program_path = os.getenv("FBASIC_PROGRAM_PATH")
local keyboard = manager.machine.natkeyboard
local screen = manager.machine.screens[":screen"]
local lines = {{}}
for line in io.lines(program_path) do
  table.insert(lines, line)
end
local started_at = os.time()
local next_index = 1
local next_input_second = {input_start_seconds}
local posted_marker = false
local posted_run = false
local pending_enter_count = 0
local pending_text = nil
local last_warning_second = -1
local last_input_second = -1
local last_marker_second = -1
local last_run_second = -1
emu.register_frame_done(function()
  local elapsed = os.time() - started_at
  if pending_text ~= nil then
    keyboard:post(pending_text)
    pending_text = nil
    pending_enter_count = 2
    return
  end
  if pending_enter_count > 0 then
    keyboard:post("\\r")
    pending_enter_count = pending_enter_count - 1
    return
  end
  if elapsed < {warning_clear_seconds} then
    if elapsed ~= last_warning_second then
      keyboard:post(" ")
      last_warning_second = elapsed
    end
    return
  end
  if next_index <= #lines and elapsed >= next_input_second and elapsed ~= last_input_second then
    pending_text = lines[next_index]
    next_index = next_index + 1
    next_input_second = elapsed + {input_gap_seconds}
    last_input_second = elapsed
    return
  end
  if elapsed >= {marker_second} and not posted_marker and elapsed ~= last_marker_second then
    pending_text = 'PRINT "{_BATCH_MARKER}"'
    posted_marker = true
    last_marker_second = elapsed
    return
  end
  if elapsed >= {run_second} and not posted_run and elapsed ~= last_run_second then
    pending_text = "RUN"
    posted_run = true
    last_run_second = elapsed
    return
  end
{capture_block}
end)
"""
    lua_path.write_text(text, encoding="ascii")


def _build_fm7_interactive_lua(
    lua_path: Path,
    *,
    input_path: Path,
    output_path: Path | None = None,
    exit_path: Path | None = None,
) -> None:
    quoted_input_path = _lua_quote(str(input_path))
    warning_clear_frame = _INPUT_START_FRAME
    exit_path_line = "local exit_path = nil"
    output_path_line = "local output_path = nil"
    output_path_block = ""
    capture_block = ""
    if exit_path is not None:
        quoted_exit_path = _lua_quote(str(exit_path))
        exit_path_line = f"local exit_path = {quoted_exit_path}"
    if output_path is not None:
        quoted_output_path = _lua_quote(str(output_path))
        output_path_line = f"local output_path = {quoted_output_path}"
        output_path_block = (
            "local sub_program = manager.machine.devices[\":sub\"].spaces[\"program\"]\n"
            "local previous_snapshot = nil\n"
        )
        capture_block = f"""
local function trim_trailing_spaces(text)
  return (string.gsub(text, "%s+$", ""))
end

local function read_console_lines()
  local visible = {{}}
  for row = 0, {_FM7_CONSOLE_ROWS - 1} do
    local chars = {{}}
    local base = {_FM7_CONSOLE_BASE} + (row * {_FM7_CONSOLE_COLUMNS})
    for col = 0, {_FM7_CONSOLE_COLUMNS - 1} do
      local byte = sub_program:read_u8(base + col)
      if byte == 0 then
        break
      end
      if byte >= 0x20 and byte <= 0x7e then
        chars[#chars + 1] = string.char(byte)
      else
        chars[#chars + 1] = " "
      end
    end
    local line = trim_trailing_spaces(table.concat(chars))
    if line ~= "" then
      visible[#visible + 1] = line
    end
  end
  return visible
end

local function write_snapshot(snapshot)
  local serialized = table.concat(snapshot, "\\n")
  if serialized == previous_snapshot then
    return
  end
  previous_snapshot = serialized
  local handle = assert(io.open(output_path, "w"))
  for _, line in ipairs(snapshot) do
    handle:write(line, "\\n")
  end
  handle:close()
end
"""
    text = f"""\
local input_path = {quoted_input_path}
{exit_path_line}
{output_path_line}
local keyboard = manager.machine.natkeyboard
local frame = 0
local input_offset = 0
local pending_line = nil
local pending_enter_count = 0
local last_warning_frame = -1
{output_path_block}

local function queue_next_line()
  local handle = io.open(input_path, "r")
  if handle == nil then
    return false
  end
  handle:seek("set", input_offset)
  local line = handle:read("*l")
  if line == nil then
    handle:close()
    return false
  end
  input_offset = handle:seek()
  handle:close()
  pending_line = line
  return true
end
{capture_block}

emu.register_frame_done(function()
  frame = frame + 1
  if output_path ~= nil then
    write_snapshot(read_console_lines())
  end
  if exit_path ~= nil then
    local exit_handle = io.open(exit_path, "r")
    if exit_handle ~= nil then
      exit_handle:close()
      manager.machine:exit()
      return
    end
  end
  if frame < {warning_clear_frame} then
    if (frame % 30) == 0 and frame ~= last_warning_frame then
      keyboard:post(" ")
      last_warning_frame = frame
    end
    return
  end
  if pending_line ~= nil then
    keyboard:post(pending_line)
    pending_line = nil
    pending_enter_count = 1
    return
  end
  if pending_enter_count > 0 then
    keyboard:post("\\r")
    pending_enter_count = pending_enter_count - 1
    return
  end
  queue_next_line()
end)
"""
    lua_path.write_text(text, encoding="ascii")


def _build_ocr_interactive_lua(
    lua_path: Path,
    *,
    input_path: Path,
    pixel_output_path: Path,
) -> None:
    quoted_input_path = _lua_quote(str(input_path))
    quoted_output_path = _lua_quote(str(pixel_output_path))
    text = f"""\
local input_path = {quoted_input_path}
local pixel_output_path = {quoted_output_path}
local keyboard = manager.machine.natkeyboard
local screen = manager.machine.screens[":screen"]
local started_at = os.time()
local input_offset = 0
local pending_line = nil
local pending_enter_count = 0
local last_warning_second = -1

local function queue_next_line()
  local handle = io.open(input_path, "r")
  if handle == nil then
    return false
  end
  handle:seek("set", input_offset)
  local line = handle:read("*l")
  if line == nil then
    handle:close()
    return false
  end
  input_offset = handle:seek()
  handle:close()
  pending_line = line
  return true
end

local function write_pixels()
  local handle = assert(io.open(pixel_output_path, "w"))
  local w, h = screen.width, screen.height
  for y = 0, h - 1 do
    local row = {{}}
    for x = 0, w - 1 do
      row[#row + 1] = screen:pixel(x, y) == 0xff000000 and "0" or "1"
    end
    handle:write(table.concat(row), "\\n")
  end
  handle:close()
end

emu.register_frame_done(function()
  local elapsed = os.time() - started_at
  write_pixels()
  if elapsed < 6 then
    if elapsed ~= last_warning_second then
      keyboard:post(" ")
      last_warning_second = elapsed
    end
    return
  end
  if pending_line ~= nil then
    keyboard:post(pending_line)
    pending_line = nil
    pending_enter_count = 1
    return
  end
  if pending_enter_count > 0 then
    keyboard:post("\\r")
    pending_enter_count = pending_enter_count - 1
    return
  end
  queue_next_line()
end)
"""
    lua_path.write_text(text, encoding="ascii")


def _build_fm7_console_capture_lua(
    lua_path: Path,
    *,
    driver: str,
    program_line_count: int,
    console_output_path: Path,
) -> None:
    input_start_frame = _INPUT_START_FRAME
    input_gap = input_gap_frames(driver)
    marker_frame = input_start_frame + input_gap * program_line_count + pre_run_settle_frames(driver)
    run_frame = marker_frame + run_delay_frames(driver)
    text = f"""\
local program_path = os.getenv("FBASIC_PROGRAM_PATH")
local output_path = "{console_output_path}"
local keyboard = manager.machine.natkeyboard
local sub_program = manager.machine.devices[":sub"].spaces["program"]
local lines = {{}}
for line in io.lines(program_path) do
  table.insert(lines, line)
end
local frame = 0
local next_index = 1
local next_input_frame = {input_start_frame}
local posted_marker = false
local posted_run = false
local pending_enter_count = 0
local pending_text = nil
local last_warning_frame = -1
local saw_trigger = false
local saw_run = false
local previous_snapshot = nil
local stable_count = 0

local function trim_trailing_spaces(text)
  return (string.gsub(text, "%s+$", ""))
end

local function normalize_line(text)
  return string.upper((string.gsub(text, "[^%w]", "")))
end

local function read_console_lines()
  local visible = {{}}
  for row = 0, {_FM7_CONSOLE_ROWS - 1} do
    local chars = {{}}
    local base = {_FM7_CONSOLE_BASE} + (row * {_FM7_CONSOLE_COLUMNS})
    for col = 0, {_FM7_CONSOLE_COLUMNS - 1} do
      local byte = sub_program:read_u8(base + col)
      if byte == 0 then
        break
      end
      if byte >= 0x20 and byte <= 0x7e then
        chars[#chars + 1] = string.char(byte)
      else
        chars[#chars + 1] = " "
      end
    end
    local line = trim_trailing_spaces(table.concat(chars))
    if line ~= "" then
      visible[#visible + 1] = line
    end
  end
  return visible
end

local function contains_trigger(snapshot)
  for _, line in ipairs(snapshot) do
    local normalized = normalize_line(line)
    if normalized == "RUN" or string.find(normalized, "{_BATCH_MARKER}") ~= nil then
      return true
    end
  end
  return false
end

local function contains_run(snapshot)
  for _, line in ipairs(snapshot) do
    if normalize_line(line) == "RUN" then
      return true
    end
  end
  return false
end

local function has_ready_after_run(snapshot)
  local triggered = false
  for _, line in ipairs(snapshot) do
    local normalized = normalize_line(line)
    if normalized == "RUN" then
      triggered = true
    elseif triggered and normalized == "READY" then
      return true
    end
  end
  return false
end

local function write_lines(snapshot)
  local f = assert(io.open(output_path, "w"))
  for _, line in ipairs(snapshot) do
    f:write(line, "\\n")
  end
  f:close()
end

emu.register_frame_done(function()
  frame = frame + 1
  if pending_text ~= nil then
    keyboard:post(pending_text)
    pending_text = nil
    pending_enter_count = 2
    return
  end
  if pending_enter_count > 0 then
    keyboard:post("\\r")
    pending_enter_count = pending_enter_count - 1
    return
  end
  if frame < {input_start_frame} then
    if (frame % 30) == 0 and frame ~= last_warning_frame then
      keyboard:post(" ")
      last_warning_frame = frame
    end
    return
  end
  if next_index <= #lines and frame >= next_input_frame then
    pending_text = lines[next_index]
    next_index = next_index + 1
    next_input_frame = frame + {input_gap}
    return
  end
  if frame >= {marker_frame} and not posted_marker then
    pending_text = 'PRINT "{_BATCH_MARKER}"'
    posted_marker = true
    return
  end
  if frame >= {run_frame} and not posted_run then
    pending_text = "RUN"
    posted_run = true
    return
  end
  if posted_run and frame >= {run_frame} then
    local snapshot = read_console_lines()
    if contains_trigger(snapshot) then
      saw_trigger = true
    end
    if contains_run(snapshot) then
      saw_run = true
    end
    if saw_trigger and saw_run and has_ready_after_run(snapshot) then
      local serialized = table.concat(snapshot, "\\n")
      if serialized == previous_snapshot then
        stable_count = stable_count + 1
      else
        previous_snapshot = serialized
        stable_count = 0
      end
      if stable_count >= 1 then
        write_lines(snapshot)
        manager.machine:exit()
        return
      end
    end
  end
end)
"""
    lua_path.write_text(text, encoding="ascii")


def _require_ocr_tools() -> None:
    if shutil.which("tesseract") is None:
        raise RuntimeError("tesseract-ocr is not installed. Install the 'tesseract-ocr' package first.")
    try:
        import PIL  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError("python3-pil is not installed. Install the 'python3-pil' package first.") from exc


def _read_text_capture_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.rstrip() for line in path.read_text(encoding="ascii").splitlines() if line.strip()]


def _emit_console_diff(previous_lines: list[str], current_lines: list[str]) -> list[str]:
    def normalize_line(text: str) -> str:
        return text.strip().upper()

    prefix_length = 0
    max_prefix = min(len(previous_lines), len(current_lines))
    while prefix_length < max_prefix and previous_lines[prefix_length] == current_lines[prefix_length]:
        prefix_length += 1
    if prefix_length == len(current_lines):
        return []
    if prefix_length == len(previous_lines):
        return current_lines[prefix_length:]
    if prefix_length == len(previous_lines) - 1 and len(previous_lines) == len(current_lines):
        return current_lines[prefix_length:]

    previous_count = len(previous_lines)
    current_count = len(current_lines)
    normalized_previous = [normalize_line(line) for line in previous_lines]
    normalized_current = [normalize_line(line) for line in current_lines]
    if previous_count > 1:
        for start in range(current_count - previous_count + 1):
            if current_lines[start : start + previous_count] == previous_lines:
                return current_lines[start + previous_count :]
        best_match_length = 0
        best_match_end = 0
        for previous_start in range(previous_count):
            for current_start in range(current_count):
                match_length = 0
                while (
                    previous_start + match_length < previous_count
                    and current_start + match_length < current_count
                    and normalized_previous[previous_start + match_length]
                    == normalized_current[current_start + match_length]
                ):
                    match_length += 1
                if match_length > best_match_length:
                    best_match_length = match_length
                    best_match_end = current_start + match_length
        if best_match_length > 1:
            return current_lines[best_match_end:]

    overlap = min(previous_count, current_count)
    while overlap > 0:
        if previous_lines[-overlap:] == current_lines[:overlap]:
            return current_lines[overlap:]
        overlap -= 1
    return current_lines


def _run_mame(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    disk_path: Path | None,
    program_path: Path,
    lua_path: Path,
    extra_mame_args: list[str],
    timeout_seconds: float | None,
    headless: bool,
) -> int:
    env = os.environ.copy()
    env["FBASIC_PROGRAM_PATH"] = str(program_path)
    base_args: list[str] = []
    effective_headless = headless and not _FORCE_HEADFUL
    if effective_headless:
        env["SDL_VIDEODRIVER"] = "dummy"
        base_args.extend(["-video", "none", "-sound", "none"])
    else:
        base_args.extend(["-sound", "none"])
    command = [
        mame_command,
        driver,
        "-rompath",
        str(rompath),
        "-skip_gameinfo",
        "-nothrottle",
        *base_args,
        *extra_mame_args,
        "-autoboot_script",
        str(lua_path),
    ]
    if disk_path is not None:
        command[5:5] = ["-flop1", str(disk_path)]
    if not effective_headless and not env.get("DISPLAY") and shutil.which("xvfb-run") is not None:
        command = ["xvfb-run", "-a", *command]
    result = subprocess.run(command, env=env, timeout=timeout_seconds)
    return result.returncode


def launch_mame(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    disk_path: Path | None,
    lua_path: Path | None,
    extra_mame_args: list[str],
    headless: bool,
    env_overrides: dict[str, str] | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    base_args: list[str] = []
    effective_headless = headless and not _FORCE_HEADFUL
    if effective_headless:
        env["SDL_VIDEODRIVER"] = "dummy"
        base_args.extend(["-video", "none", "-sound", "none"])
    else:
        base_args.extend(["-sound", "none"])
    command = [
        mame_command,
        driver,
        "-rompath",
        str(rompath),
        "-skip_gameinfo",
        "-nothrottle",
        *base_args,
        *extra_mame_args,
    ]
    if disk_path is not None:
        command[5:5] = ["-flop1", str(disk_path)]
    if lua_path is not None:
        command.extend(["-autoboot_script", str(lua_path)])
    if not effective_headless and not env.get("DISPLAY") and shutil.which("xvfb-run") is not None:
        command = ["xvfb-run", "-a", *command]
    return subprocess.Popen(
        command,
        env=env,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        preexec_fn=_parent_death_preexec_fn(),
    )


def _pick_xvfb_display() -> str:
    for _ in range(32):
        display_num = random.randint(90, 199)
        lock_path = Path(f"/tmp/.X{display_num}-lock")
        if not lock_path.exists():
            return f":{display_num}"
    raise RuntimeError("unable to find a free Xvfb display")


def _run_mame_headful_capture(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    disk_path: Path | None,
    program_path: Path,
    lua_path: Path,
    extra_mame_args: list[str],
    image_path: Path,
    timeout_seconds: float | None,
) -> list[str]:
    command = [
        mame_command,
        driver,
        "-rompath",
        str(rompath),
        "-skip_gameinfo",
        "-nothrottle",
        "-video",
        "soft",
        "-sound",
        "none",
        *extra_mame_args,
        "-autoboot_script",
        str(lua_path),
    ]
    if disk_path is not None:
        command[5:5] = ["-flop1", str(disk_path)]
    display = _pick_xvfb_display()
    xvfb = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", "1280x960x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=_parent_death_preexec_fn(),
    )
    mame_proc: subprocess.Popen[bytes] | None = None
    env = os.environ.copy()
    env["DISPLAY"] = display
    env["FBASIC_PROGRAM_PATH"] = str(program_path)
    try:
        time.sleep(1.0)
        mame_proc = subprocess.Popen(
            command,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            preexec_fn=_parent_death_preexec_fn(),
        )
        deadline = time.monotonic() + (timeout_seconds if timeout_seconds is not None else 120.0)
        window_id: str | None = None
        while time.monotonic() < deadline:
            if mame_proc.poll() is not None:
                raise RuntimeError("MAME exited before the headful FM7 capture window appeared")
            result = subprocess.run(
                ["xdotool", "search", "--onlyvisible", "--pid", str(mame_proc.pid)],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            window_ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if window_ids:
                window_id = window_ids[0]
                break
            time.sleep(1.0)
        if window_id is None:
            if timeout_seconds is not None:
                raise subprocess.TimeoutExpired(command, timeout_seconds)
            raise RuntimeError("unable to find visible MAME window for headful fallback capture")

        # Neo Kobe ROMs can trigger the MAME bad-dump warning dialog.
        # Keep nudging it for a few seconds so late-appearing prompts are dismissed too.
        time.sleep(6.0)
        for key in ("space", "Return") * 6:
            if time.monotonic() >= deadline:
                if timeout_seconds is not None:
                    raise subprocess.TimeoutExpired(command, timeout_seconds)
                break
            subprocess.run(
                ["xdotool", "windowfocus", "--sync", window_id],
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            subprocess.run(
                ["xdotool", "key", "--window", window_id, key],
                env=env,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.0)

        stable_lines: list[str] = []
        previous_lines: list[str] | None = None
        stable_count = 0
        saw_trigger = False
        while time.monotonic() < deadline:
            try:
                _capture_xwd_to_png(window_id, image_path, env=env)
            except subprocess.CalledProcessError:
                time.sleep(1.0)
                continue
            plain_lines = _ocr_lines(image_path, whitelist=None)
            filtered_lines = _ocr_lines(image_path, whitelist=_OCR_WHITELIST)
            if any(
                is_batch_marker_line(line) or is_run_line(line)
                for line in [*plain_lines, *filtered_lines]
            ):
                saw_trigger = True
            output_lines = extract_output_lines(plain_lines, filtered_lines)
            if saw_trigger and output_lines:
                if output_lines == previous_lines:
                    stable_count += 1
                else:
                    previous_lines = list(output_lines)
                    stable_count = 0
                stable_lines = list(output_lines)
                if stable_count >= 1:
                    return stable_lines
            time.sleep(3.0)
        if timeout_seconds is not None:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        if not saw_trigger:
            raise RuntimeError("unable to detect FM7 batch execution on screen")
        return stable_lines
    finally:
        if mame_proc is not None and mame_proc.poll() is None:
            _terminate_launched_process(mame_proc)
        _terminate_launched_process(xvfb)


def run_batch(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    disk_path: Path | None,
    program_path: Path,
    timeout_seconds: float | None,
    extra_mame_args: list[str],
) -> int:
    program_lines = normalize_program_lines(program_path)
    string_literals = _extract_string_literals(program_lines)

    temp_dir = Path(tempfile.mkdtemp(prefix="fbasic-batch-"))
    try:
        normalized_program_path = temp_dir / "program.bas"
        pixel_path = temp_dir / "screen.txt"
        image_path = temp_dir / "screen.png"
        text_output_path = temp_dir / "screen.txt"
        lua_path = temp_dir / "capture.lua"
        output_lines: list[str] = []
        if driver in {"fm7", "fm77av"}:
            captured_lines = _run_fm7_batch_capture(
                mame_command=mame_command,
                rompath=rompath,
                driver=driver,
                disk_path=disk_path,
                extra_mame_args=extra_mame_args,
                program_lines=program_lines,
                temp_dir=temp_dir,
                timeout_seconds=timeout_seconds,
            )
            output_lines = extract_output_lines(captured_lines, captured_lines)
            if not captured_lines:
                raise RuntimeError("unable to detect FM7 batch execution in console RAM")
        elif driver in {"pc8801mk2"}:
            normalized_program_path.write_text("\n".join(program_lines) + "\n", encoding="ascii")
            gap_frames = input_gap_frames(driver)
            marker_frame = (
                _INPUT_START_FRAME
                + gap_frames * len(program_lines)
                + pre_run_settle_frames(driver)
            )
            run_frame = marker_frame + run_delay_frames(driver)
            capture_frame = run_frame + post_run_settle_frames(driver)
            _build_capture_lua(
                lua_path,
                driver=driver,
                program_line_count=len(program_lines),
                pixel_output_path=pixel_path,
                send_marker=True,
                send_run=True,
                capture_frame=capture_frame,
                run_frame=run_frame,
                marker_frame=marker_frame,
            )
            _require_ocr_tools()
            _build_capture_lua(
                lua_path,
                driver=driver,
                program_line_count=len(program_lines),
                pixel_output_path=None,
                send_marker=True,
                send_run=True,
                capture_frame=None,
                run_frame=run_frame,
                marker_frame=marker_frame,
            )
            output_lines = _run_mame_headful_capture(
                mame_command=mame_command,
                rompath=rompath,
                driver=driver,
                disk_path=disk_path,
                program_path=normalized_program_path,
                lua_path=lua_path,
                extra_mame_args=extra_mame_args,
                image_path=image_path,
                timeout_seconds=timeout_seconds,
            )
        else:
            normalized_program_path.write_text("\n".join(program_lines) + "\n", encoding="ascii")
            gap_frames = input_gap_frames(driver)
            marker_frame = (
                _INPUT_START_FRAME
                + gap_frames * len(program_lines)
                + pre_run_settle_frames(driver)
            )
            run_frame = marker_frame + run_delay_frames(driver)
            capture_frame = run_frame + post_run_settle_frames(driver)
            _build_capture_lua(
                lua_path,
                driver=driver,
                program_line_count=len(program_lines),
                pixel_output_path=pixel_path,
                send_marker=True,
                send_run=True,
                capture_frame=capture_frame,
                run_frame=run_frame,
                marker_frame=marker_frame,
            )
            _require_ocr_tools()
            _run_mame(
                mame_command=mame_command,
                rompath=rompath,
                driver=driver,
                disk_path=disk_path,
                program_path=normalized_program_path,
                lua_path=lua_path,
                extra_mame_args=extra_mame_args,
                timeout_seconds=timeout_seconds,
                headless=True,
            )
            if _pixel_dump_has_signal(pixel_path):
                _render_pixels_to_image(pixel_path, image_path)
                plain_lines = _ocr_lines(image_path, whitelist=None)
                filtered_lines = _ocr_lines(image_path, whitelist=_OCR_WHITELIST)
                output_lines = extract_output_lines(plain_lines, filtered_lines)
        if driver not in {"fm7", "fm77av"}:
            output_lines = _repair_output_lines(output_lines, string_literals)
        if any(is_input_prompt_line(line) for line in output_lines):
            raise UnsupportedProgramInputError("program input is not supported in --run mode")
        if output_lines and driver in {"fm7", "fm77av"}:
            pass
        elif output_lines:
            print("\n".join(output_lines))
        return 0
    finally:
        if not _KEEP_TEMP:
            shutil.rmtree(temp_dir, ignore_errors=True)


def run_interactive_load(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    disk_path: Path | None,
    program_path: Path,
    extra_mame_args: list[str],
) -> int:
    program_lines = normalize_program_lines(program_path)
    temp_dir = Path(tempfile.mkdtemp(prefix="fbasic-load-"))
    try:
        normalized_program_path = temp_dir / "program.bas"
        lua_path = temp_dir / "load.lua"
        normalized_program_path.write_text("\n".join(program_lines) + "\n", encoding="ascii")
        _build_capture_lua(
            lua_path,
            driver=driver,
            program_line_count=len(program_lines),
            pixel_output_path=None,
            send_marker=False,
            send_run=False,
            capture_frame=None,
            run_frame=None,
            marker_frame=None,
        )
        return _run_mame(
            mame_command=mame_command,
            rompath=rompath,
            driver=driver,
            disk_path=disk_path,
            program_path=normalized_program_path,
            lua_path=lua_path,
            extra_mame_args=extra_mame_args,
            timeout_seconds=None,
            headless=False,
        )
    finally:
        if not _KEEP_TEMP:
            shutil.rmtree(temp_dir, ignore_errors=True)


def launch_interactive_mame(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    disk_path: Path | None,
    extra_mame_args: list[str],
) -> subprocess.Popen[bytes]:
    return launch_mame(
        mame_command=mame_command,
        rompath=rompath,
        driver=driver,
        disk_path=disk_path,
        lua_path=None,
        extra_mame_args=extra_mame_args,
        headless=False,
    )


def prepare_interactive_load(
    *,
    program_path: Path,
    driver: str,
) -> tuple[Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="fbasic-load-"))
    normalized_program_path = temp_dir / "program.bas"
    lua_path = temp_dir / "load.lua"
    program_lines = normalize_program_lines(program_path)
    normalized_program_path.write_text("\n".join(program_lines) + "\n", encoding="ascii")
    _build_capture_lua(
        lua_path,
        driver=driver,
        program_line_count=len(program_lines),
        pixel_output_path=None,
        send_marker=False,
        send_run=False,
        capture_frame=None,
        run_frame=None,
        marker_frame=None,
    )
    return temp_dir, lua_path


def prepare_fm7_interactive_session(
    *,
    driver: str,
    program_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="fbasic-interactive-"))
    input_path = temp_dir / "input.txt"
    exit_path = temp_dir / "exit.txt"
    lua_path = temp_dir / "interactive.lua"
    initial_lines: list[str] = []
    if program_path is not None:
        initial_lines = normalize_program_lines(program_path)
    input_path.write_text(
        "".join(f"{line}\n" for line in initial_lines),
        encoding="ascii",
    )
    _build_fm7_interactive_lua(lua_path, input_path=input_path, exit_path=exit_path)
    return temp_dir, lua_path, input_path


def prepare_fm7_headless_interactive_session(
    *,
    driver: str,
    program_path: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="fbasic-interactive-"))
    input_path = temp_dir / "input.txt"
    exit_path = temp_dir / "exit.txt"
    output_path = temp_dir / "screen.txt"
    lua_path = temp_dir / "interactive.lua"
    initial_lines: list[str] = []
    if program_path is not None:
        initial_lines = normalize_program_lines(program_path)
    input_path.write_text(
        "".join(f"{line}\n" for line in initial_lines),
        encoding="ascii",
    )
    output_path.write_text("", encoding="ascii")
    _build_fm7_interactive_lua(
        lua_path,
        input_path=input_path,
        output_path=output_path,
        exit_path=exit_path,
    )
    return temp_dir, lua_path, input_path, output_path


def prepare_ocr_headless_interactive_session(
    *,
    driver: str,
    program_path: Path | None = None,
) -> tuple[Path, Path, Path, Path, Path]:
    temp_dir = Path(tempfile.mkdtemp(prefix="fbasic-interactive-"))
    input_path = temp_dir / "input.txt"
    pixel_path = temp_dir / "screen.txt"
    image_path = temp_dir / "screen.png"
    lua_path = temp_dir / "interactive.lua"
    initial_lines: list[str] = []
    if program_path is not None:
        initial_lines = normalize_program_lines(program_path)
    input_path.write_text(
        "".join(f"{line}\n" for line in initial_lines),
        encoding="ascii",
    )
    pixel_path.write_text("", encoding="ascii")
    _build_ocr_interactive_lua(lua_path, input_path=input_path, pixel_output_path=pixel_path)
    return temp_dir, lua_path, input_path, pixel_path, image_path


def launch_interactive_load(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    disk_path: Path | None,
    program_path: Path,
    extra_mame_args: list[str],
) -> tuple[subprocess.Popen[bytes], Path]:
    temp_dir, lua_path = prepare_interactive_load(program_path=program_path, driver=driver)
    proc = launch_mame(
        mame_command=mame_command,
        rompath=rompath,
        driver=driver,
        disk_path=disk_path,
        lua_path=lua_path,
        extra_mame_args=extra_mame_args,
        headless=False,
        env_overrides={"FBASIC_PROGRAM_PATH": str(temp_dir / "program.bas")},
    )
    return proc, temp_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="F-BASIC batch helper")
    parser.add_argument("--mame-command", required=True)
    parser.add_argument("--driver", required=True, choices=("fm7", "fm77av", "pc8801mk2"))
    parser.add_argument("--rompath", required=True, type=Path)
    parser.add_argument("--disk", type=Path)
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--timeout")
    parser.add_argument("mame_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    timeout_seconds = parse_timeout_spec(args.timeout)
    extra_mame_args = list(args.mame_args)
    if extra_mame_args and extra_mame_args[0] == "--":
        extra_mame_args = extra_mame_args[1:]

    try:
        if args.run:
            return run_batch(
                mame_command=args.mame_command,
                driver=args.driver,
                rompath=args.rompath,
                disk_path=args.disk,
                program_path=args.file,
                timeout_seconds=timeout_seconds,
                extra_mame_args=extra_mame_args,
            )
        return run_interactive_load(
                mame_command=args.mame_command,
                driver=args.driver,
                rompath=args.rompath,
                disk_path=args.disk,
            program_path=args.file,
            extra_mame_args=extra_mame_args,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=os.sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        if args.timeout:
            print(f"error: fbasic batch run timed out after {args.timeout}", file=os.sys.stderr)
        else:
            print("error: fbasic batch run timed out", file=os.sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
