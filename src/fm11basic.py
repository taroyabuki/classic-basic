#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import fbasic_batch


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "bootstrap_fm11basic_assets.sh"
EMULATOR_EXE = REPO_ROOT / "downloads" / "fm11basic" / "staged" / "emulator-x86" / "Fm11.exe"
FBASIC_DISK = REPO_ROOT / "downloads" / "fm11basic" / "staged" / "disks" / "fb2hd00.img"
ROM_STAGE_DIR = REPO_ROOT / "downloads" / "fm11basic" / "staged" / "roms"
REQUIRED_ROM_NAMES = ("boot6809.rom", "boot8088.rom", "kanji.rom", "subsys.rom", "subsys_e.rom")
DISPLAY_BASE = 99
DISPLAY_RANGE = 200
CACHE_ROOT = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "fm11-basic"
BASE_WINE_PREFIX = CACHE_ROOT / "wine-prefix-base"
BASE_WINE_PREFIX_LOCK = CACHE_ROOT / "wine-prefix-base.lock"
FAST_STARTUP_ANSWER_DELAY = 0.15
FAST_STARTUP_READY_TIMEOUT = 5.0
STARTUP_POLL_DELAY = 0.2
RUN_OUTPUT_POLL_DELAY = 0.2
LOAD_BATCH_SIZE = 3
LARGE_LOAD_BATCH_SIZE = 5
LOAD_BATCH_DELAY = 0.15
FAST_LOADER_MAX_LINES = 12
INPUT_PASTE_CHUNK_SIZE = 20


class BasicRuntimeError(RuntimeError):
    pass


class BasicTimeoutError(BasicRuntimeError):
    pass


class FastLoadFallbackRequired(BasicRuntimeError):
    pass


def _broken_pipe_exit_code() -> int:
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        pass
    except OSError:
        return 141
    else:
        return 141
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return 141
    try:
        os.dup2(devnull_fd, sys.stdout.fileno())
    except OSError:
        pass
    finally:
        os.close(devnull_fd)
    return 141


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


def _terminate_process(proc: subprocess.Popen[str] | None, *, use_process_group: bool) -> None:
    if proc is None or proc.poll() is not None:
        return

    pid = getattr(proc, "pid", None)
    terminated = False
    if use_process_group and isinstance(pid, int):
        try:
            os.killpg(pid, signal.SIGTERM)
            terminated = True
        except ProcessLookupError:
            return
        except OSError:
            terminated = False
    if not terminated:
        proc.terminate()

    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass

    killed = False
    if use_process_group and isinstance(pid, int):
        try:
            os.killpg(pid, signal.SIGKILL)
            killed = True
        except ProcessLookupError:
            return
        except OSError:
            killed = False
    if not killed:
        proc.kill()
    proc.wait(timeout=5)


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise BasicRuntimeError(f"missing required tool: {name}")


def read_basic_file(path: Path) -> str:
    data = path.read_bytes()
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise BasicRuntimeError(f"{path} must be ASCII-only") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def reject_unsupported_run_input(source: str) -> None:
    upper = source.upper()
    if re.search(r"(^|[^A-Z])(LINE[ \t]+INPUT|INPUT)([^A-Z]|$)", upper):
        raise BasicRuntimeError("INPUT-style program input is unsupported in --run mode")


def _remaining_timeout(deadline: float | None, *, fallback: float, message: str) -> float:
    if deadline is None:
        return fallback
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BasicTimeoutError(message)
    return max(0.1, min(fallback, remaining))


def _full_remaining_timeout(deadline: float | None, *, message: str) -> float | None:
    if deadline is None:
        return None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BasicTimeoutError(message)
    return max(0.1, remaining)


def _command_timeout(deadline: float | None, *, fallback: float, message: str) -> float | None:
    if deadline is None:
        return None
    return _remaining_timeout(deadline, fallback=fallback, message=message)


def _sleep_with_deadline(seconds: float, *, deadline: float | None, message: str) -> None:
    if deadline is None:
        time.sleep(seconds)
        return
    remaining = _remaining_timeout(deadline, fallback=seconds, message=message)
    time.sleep(min(seconds, remaining))


def _effective_deadline(timeout: float, *, deadline: float | None, message: str) -> float:
    local_deadline = time.monotonic() + max(timeout, 60.0) if deadline is None else time.monotonic() + timeout
    if deadline is None:
        return local_deadline
    _remaining_timeout(deadline, fallback=timeout, message=message)
    return min(local_deadline, deadline)


def _screen_has_ready_prompt(screen: str) -> bool:
    return "\nReady" in screen or screen.strip().endswith("Ready")


def _screen_lines_have_ready_prompt(lines: list[str]) -> bool:
    for line in reversed(lines):
        stripped = line.strip()
        if not stripped:
            continue
        return stripped == "Ready"
    return False


def _startup_prompt_index(screen: str, prompts: tuple[str, ...]) -> int | None:
    return next((index for index, prompt in enumerate(prompts) if prompt in screen), None)


def _merge_screen_suffix(previous_lines: list[str], current_lines: list[str]) -> list[str]:
    if not previous_lines:
        return current_lines
    max_overlap = min(len(previous_lines), len(current_lines))
    for overlap in range(max_overlap, -1, -1):
        if previous_lines[-overlap:] == current_lines[:overlap]:
            return current_lines[overlap:]
    return current_lines


_SCREEN_SOURCE_LINE_RE = re.compile(r"^\d+\s+(.+)$")


def preprocess_fm11_source(source: str, *, max_body_length: int = 24) -> str:
    del max_body_length
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    return normalized if not normalized or normalized.endswith("\n") else normalized + "\n"


def windows_path(path: Path) -> str:
    return "Z:" + str(path).replace("/", "\\")


def run_command(
    args,
    *,
    env=None,
    cwd=None,
    capture_output=False,
    check=True,
    timeout_seconds: float | None = None,
    timeout_error: str = "timed out waiting for FM-11 emulator command completion",
):
    try:
        return subprocess.run(
            args,
            env=env,
            cwd=cwd,
            check=check,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise BasicTimeoutError(timeout_error) from exc


class RuntimeSession:
    def __init__(self) -> None:
        self.tempdir = Path(tempfile.mkdtemp(prefix="fm11-basic-"))
        self.wine_prefix = self.tempdir / "wine-prefix"
        self.runtime_emulator_dir = self.tempdir / "emulator"
        self.display: str | None = None
        self.xvfb_proc: subprocess.Popen[str] | None = None
        self.wine_proc: subprocess.Popen[str] | None = None
        self.window_id: str | None = None
        self._window_geometry: tuple[int, int, int, int] | None = None

    @property
    def env(self):
        env = os.environ.copy()
        env["DISPLAY"] = self.display or ""
        env["WINEPREFIX"] = str(self.wine_prefix)
        return env

    def start(self, *, deadline: float | None = None) -> None:
        self._start_xvfb(deadline=deadline)
        self._prepare_wine_prefix(deadline=deadline)
        self._seed_registry(deadline=deadline)
        self._prepare_emulator()
        self.wine_proc = subprocess.Popen(
            ["wine", str(self.runtime_emulator_dir / "Fm11.exe")],
            cwd=str(self.runtime_emulator_dir),
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            preexec_fn=_parent_death_preexec_fn(),
        )
        self.window_id = self._wait_for_window(deadline=deadline)

    def close(self) -> None:
        if self.wine_proc is not None and self.wine_proc.poll() is None:
            _terminate_process(self.wine_proc, use_process_group=True)
        self._shutdown_wine_services()
        if self.xvfb_proc is not None and self.xvfb_proc.poll() is None:
            _terminate_process(self.xvfb_proc, use_process_group=True)
        shutil.rmtree(self.tempdir, ignore_errors=True)

    def _shutdown_wine_services(self) -> None:
        try:
            run_command(
                ["wineserver", "-k"],
                env=self.env,
                capture_output=True,
                check=False,
                timeout_seconds=5.0,
                timeout_error="timed out shutting down FM-11 wine services",
            )
        except BasicRuntimeError:
            pass

    def _seed_registry(self, *, deadline: float | None = None) -> None:
        reg_base = r"HKCU\Software\Culti\FM11\FM11"
        commands = [
            ["wine", "reg", "add", reg_base, "/v", "2HD0", "/t", "REG_SZ", "/d", windows_path(FBASIC_DISK), "/f"],
            ["wine", "reg", "add", reg_base, "/v", "DipSW", "/t", "REG_DWORD", "/d", "0x9f", "/f"],
        ]
        for command in commands:
            run_command(
                command,
                env=self.env,
                capture_output=True,
                timeout_seconds=_command_timeout(
                    deadline,
                    fallback=15.0,
                    message="timed out configuring FM-11 registry",
                ),
                timeout_error="timed out configuring FM-11 registry",
            )

    def _prepare_wine_prefix(self, *, deadline: float | None = None) -> None:
        base_prefix = self._ensure_base_wine_prefix(deadline=deadline)
        if self.wine_prefix.exists():
            shutil.rmtree(self.wine_prefix)
        shutil.copytree(base_prefix, self.wine_prefix, symlinks=True)

    def _ensure_base_wine_prefix(self, *, deadline: float | None = None) -> Path:
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        with BASE_WINE_PREFIX_LOCK.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            system_reg = BASE_WINE_PREFIX / "system.reg"
            if not system_reg.is_file():
                if BASE_WINE_PREFIX.exists():
                    shutil.rmtree(BASE_WINE_PREFIX)
                BASE_WINE_PREFIX.mkdir(parents=True, exist_ok=True)
                env = os.environ.copy()
                env["DISPLAY"] = self.display or ""
                env["WINEPREFIX"] = str(BASE_WINE_PREFIX)
                run_command(
                    ["wineboot", "-i"],
                    env=env,
                    capture_output=True,
                    timeout_seconds=_command_timeout(
                        deadline,
                        fallback=45.0,
                        message="timed out preparing FM-11 wine prefix",
                    ),
                    timeout_error="timed out preparing FM-11 wine prefix",
                )
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        return BASE_WINE_PREFIX

    def _wait_for_window(self, *, deadline: float | None = None) -> str:
        wait_deadline = _effective_deadline(
            45.0,
            deadline=deadline,
            message="timed out waiting for FM-11 emulator window",
        )
        while time.monotonic() < wait_deadline:
            result = run_command(
                ["xdotool", "search", "--name", "FUJITSU MICRO 11"],
                env=self.env,
                capture_output=True,
                check=False,
                timeout_seconds=_command_timeout(
                    wait_deadline,
                    fallback=2.0,
                    message="timed out waiting for FM-11 emulator window",
                ),
                timeout_error="timed out waiting for FM-11 emulator window",
            )
            ids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if ids:
                return ids[0]
            _sleep_with_deadline(
                STARTUP_POLL_DELAY,
                deadline=wait_deadline,
                message="timed out waiting for FM-11 emulator window",
            )
        raise BasicTimeoutError("timed out waiting for FM-11 emulator window")

    def _start_xvfb(self, *, deadline: float | None = None) -> None:
        start_offset = os.getpid() % DISPLAY_RANGE
        for i in range(DISPLAY_RANGE):
            display_no = DISPLAY_BASE + (start_offset + i) % DISPLAY_RANGE
            display = f":{display_no}"
            proc = subprocess.Popen(
                ["Xvfb", display, "-screen", "0", "1280x1024x24"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                preexec_fn=_parent_death_preexec_fn(),
            )
            _sleep_with_deadline(
                STARTUP_POLL_DELAY,
                deadline=deadline,
                message="timed out starting Xvfb for FM-11",
            )
            if proc.poll() is not None:
                continue
            test_env = os.environ.copy()
            test_env["DISPLAY"] = display
            probe = run_command(
                ["xdpyinfo"],
                env=test_env,
                capture_output=True,
                check=False,
                timeout_seconds=_command_timeout(
                    deadline,
                    fallback=2.0,
                    message="timed out starting Xvfb for FM-11",
                ),
                timeout_error="timed out starting Xvfb for FM-11",
            )
            if probe.returncode == 0:
                self.display = display
                self.xvfb_proc = proc
                return
            _terminate_process(proc, use_process_group=True)
        raise BasicRuntimeError("failed to start Xvfb")

    def _prepare_emulator(self) -> None:
        self.runtime_emulator_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(EMULATOR_EXE, self.runtime_emulator_dir / "Fm11.exe")
        for rom_path in ROM_STAGE_DIR.glob("*.rom"):
            shutil.copy2(rom_path, self.runtime_emulator_dir / rom_path.name)

    def _read_window_geometry(self, *, deadline: float | None = None) -> tuple[int, int, int, int]:
        geometry = run_command(
            ["xdotool", "getwindowgeometry", "--shell", self.window_id or ""],
            env=self.env,
            capture_output=True,
            timeout_seconds=_command_timeout(
                deadline,
                fallback=3.0,
                message="timed out capturing FM-11 screen",
            ),
            timeout_error="timed out capturing FM-11 screen",
        ).stdout
        info: dict[str, str] = {}
        for line in geometry.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                info[key] = value
        return int(info["X"]), int(info["Y"]), int(info["WIDTH"]), int(info["HEIGHT"])

    def _capture_region(self, *, refresh: bool, deadline: float | None = None) -> tuple[str, str, str, str]:
        if refresh or self._window_geometry is None:
            self._window_geometry = self._read_window_geometry(deadline=deadline)
        x_pos, y_pos, width, height = self._window_geometry
        return (
            str(x_pos + 2),
            str(y_pos + 24),
            str(x_pos + width - 2),
            str(y_pos + height - 2),
        )

    def key(self, *keys: str, deadline: float | None = None) -> None:
        run_command(
            ["xdotool", "key", "--window", self.window_id or "", *keys],
            env=self.env,
            capture_output=True,
            timeout_seconds=_command_timeout(
                deadline,
                fallback=3.0,
                message="timed out sending FM-11 keyboard input",
            ),
            timeout_error="timed out sending FM-11 keyboard input",
        )

    def set_clipboard(self, text: str, *, deadline: float | None = None) -> None:
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard", "-i"],
                env=self.env,
                check=True,
                text=True,
                input=text,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_command_timeout(
                    deadline,
                    fallback=5.0,
                    message="timed out loading FM-11 clipboard text",
                ),
            )
        except subprocess.CalledProcessError as exc:
            raise BasicRuntimeError("failed to load text into the X clipboard") from exc
        except subprocess.TimeoutExpired as exc:
            raise BasicTimeoutError("timed out loading FM-11 clipboard text") from exc

    def paste_text(self, text: str, *, deadline: float | None = None) -> None:
        self.set_clipboard(text, deadline=deadline)
        self.key("Alt+e", "p", deadline=deadline)
        _sleep_with_deadline(
            0.2,
            deadline=deadline,
            message="timed out waiting for FM-11 paste completion",
        )

    def copy_screen(self, *, deadline: float | None = None) -> str:
        for attempt in range(2):
            start_x, start_y, end_x, end_y = self._capture_region(refresh=attempt > 0, deadline=deadline)
            run_command(
                ["xdotool", "mousemove", start_x, start_y, "mousedown", "1", "mousemove", end_x, end_y, "mouseup", "1"],
                env=self.env,
                capture_output=True,
                timeout_seconds=_command_timeout(
                    deadline,
                    fallback=3.0,
                    message="timed out capturing FM-11 screen",
                ),
                timeout_error="timed out capturing FM-11 screen",
            )
            _sleep_with_deadline(0.05, deadline=deadline, message="timed out capturing FM-11 screen")
            self.key("Alt+e", "c", deadline=deadline)
            _sleep_with_deadline(0.08, deadline=deadline, message="timed out capturing FM-11 screen")
            result = run_command(
                ["xclip", "-selection", "clipboard", "-o"],
                env=self.env,
                capture_output=True,
                check=False,
                timeout_seconds=_command_timeout(
                    deadline,
                    fallback=3.0,
                    message="timed out capturing FM-11 screen",
                ),
                timeout_error="timed out capturing FM-11 screen",
            )
            if result.stdout or attempt == 1:
                return result.stdout.replace("\r\n", "\n").replace("\r", "\n")
        return ""

    def wait_for_text(
        self,
        needle: str | tuple[str, ...],
        timeout: float = 30.0,
        *,
        deadline: float | None = None,
    ) -> str:
        wait_deadline = _effective_deadline(
            timeout,
            deadline=deadline,
            message="timed out waiting for FM-11 startup prompt",
        )
        needles = (needle,) if isinstance(needle, str) else needle
        latest = ""
        while time.monotonic() < wait_deadline:
            latest = self.copy_screen(deadline=wait_deadline)
            if any(text in latest for text in needles):
                return latest
            _sleep_with_deadline(
                STARTUP_POLL_DELAY,
                deadline=wait_deadline,
                message="timed out waiting for FM-11 startup prompt",
            )
        raise BasicTimeoutError("timed out waiting for FM-11 startup prompt")


def normalize_screen_text(screen: str):
    normalized = []
    for raw_line in screen.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        if not raw_line:
            normalized.append("")
            continue
        weird_chars = sum(1 for char in raw_line if ord(char) < 32 or ord(char) > 126)
        if weird_chars and weird_chars / len(raw_line) > 0.2:
            continue
        clean_line = "".join(char if 32 <= ord(char) <= 126 else " " for char in raw_line).rstrip()
        alnum_count = sum(1 for char in clean_line if char.isalnum())
        if weird_chars and alnum_count < 4:
            continue
        if clean_line.startswith("File  Edit  Disk  Tape  Help"):
            continue
        if clean_line.startswith("FUJITSU FM-11"):
            continue
        normalized.append(clean_line)

    while normalized and normalized[-1] == "":
        normalized.pop()

    deduped = []
    for line in normalized:
        if deduped and deduped[-1] == line and line != "":
            continue
        deduped.append(line)
    return deduped


def is_noise_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and len(stripped) == 1 and stripped.islower() and line != stripped


def screen_appended_lines(before_lines, after_lines):
    max_overlap = min(len(before_lines), len(after_lines))
    for overlap in range(max_overlap, -1, -1):
        if before_lines[-overlap:] == after_lines[:overlap]:
            return after_lines[overlap:]
    return after_lines


def _merge_output_window(previous_lines: list[str], current_lines: list[str]) -> tuple[list[str], int]:
    if not previous_lines:
        return current_lines, 0
    max_overlap = min(len(previous_lines), len(current_lines))
    for overlap in range(max_overlap, -1, -1):
        previous_suffix = previous_lines[-overlap:] if overlap else []
        for start in range(0, len(current_lines) - overlap + 1):
            if overlap and previous_suffix != current_lines[start : start + overlap]:
                continue
            return current_lines[start + overlap :], overlap
    return current_lines, 0


def filter_new_lines(lines, sent_text: str):
    echoed = {
        line.strip().upper()
        for line in sent_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line
    }
    filtered = []
    for line in lines:
        if line.strip().upper() in echoed:
            continue
        if is_noise_line(line):
            continue
        filtered.append(line)
    while filtered and filtered[0] == "":
        filtered.pop(0)
    while filtered and filtered[-1] == "":
        filtered.pop()
    return filtered


def _looks_like_basic_source_line(line: str) -> bool:
    match = _SCREEN_SOURCE_LINE_RE.match(line.strip())
    if match is None:
        return False
    body = match.group(1).strip().upper()
    if not re.search(r"[A-Z]", body):
        return False
    if body.startswith(("REM", "'")):
        return True
    if any(
        keyword in body
        for keyword in (
            " IF ",
            " THEN ",
            " GOTO ",
            " GOSUB ",
            " PRINT ",
            " FOR ",
            " NEXT",
            " END",
            " RETURN",
            " INPUT",
            " READ",
            " DATA",
            " DIM ",
            " DEF",
            " ATN(",
            " INT(",
            " ABS(",
        )
    ):
        return True
    return re.fullmatch(r"[A-Z][A-Z0-9$%!#]*\s*=.*", body) is not None


def _looks_like_garbage_run_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped == "LX":
        return True
    if stripped.startswith("LX") and stripped.endswith('"'):
        return True
    # FM-11 redraw artifacts tend to be a few short alnum fragments separated by
    # wide gaps, often with a dangling quote from the screen edge.
    groups = [group for group in re.split(r"\s{2,}", stripped.rstrip('"')) if group.strip()]
    compact_groups = [re.sub(r"\s+", "", group) for group in groups]
    if len(compact_groups) < 2:
        return False
    if any(len(group) > 3 for group in compact_groups):
        return False
    if stripped.endswith('"'):
        return True
    if any(not group.isalnum() for group in compact_groups):
        return False
    return any(char.islower() for char in stripped)


def extract_marker_output_lines(lines, *, marker: str) -> list[str]:
    latest_marker = None
    for index, line in enumerate(lines):
        if line.strip() == marker:
            latest_marker = index
    if latest_marker is None:
        return []

    output = []
    for line in lines[latest_marker + 1 :]:
        stripped = line.strip()
        if not stripped or stripped == "Ready":
            continue
        if any(keyword in stripped for keyword in (" IF ", ":GOTO", " PRINT ", " THEN ")):
            continue
        output.append(stripped)
    return output


def _extract_run_output_lines(
    lines: list[str],
    *,
    marker: str | None = None,
    triggered: bool,
    ignored_source_lines: set[str] | None = None,
) -> tuple[list[str], bool]:
    start = 0
    latest_marker = None
    if marker is not None:
        for index, line in enumerate(lines):
            if line.strip() == marker:
                latest_marker = index
        if latest_marker is not None:
            triggered = True
            start = latest_marker + 1

    latest_run = None
    for index in range(start, len(lines)):
        if lines[index].strip() == "RUN":
            latest_run = index
    if latest_run is not None:
        triggered = True
        start = latest_run + 1

    if not triggered:
        return [], False

    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].strip() == "Ready":
            end = index
            break

    output: list[str] = []
    for line in lines[start:end]:
        stripped = line.strip()
        normalized = stripped.upper()
        if not stripped or normalized == "RUN" or normalized == "READY":
            continue
        if marker is not None and stripped == marker:
            continue
        if ignored_source_lines is not None and normalized in ignored_source_lines:
            continue
        if _looks_like_basic_source_line(line):
            continue
        if _looks_like_garbage_run_line(stripped):
            continue
        output.append(stripped)
    return output, True


def emit_lines(lines) -> None:
    if not lines:
        return
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()


def submit_line(session: RuntimeSession, line: str, *, deadline: float | None = None) -> None:
    if not line:
        session.key("Return", deadline=deadline)
        return
    for start in range(0, len(line), INPUT_PASTE_CHUNK_SIZE):
        session.paste_text(line[start : start + INPUT_PASTE_CHUNK_SIZE], deadline=deadline)
    session.key("Return", deadline=deadline)


def interactive_startup_lines(screen_lines, source_text: str | None) -> list[str]:
    if source_text is None:
        return list(screen_lines)
    return [line for line in source_text.splitlines() if line] + ["Ready"]


def _extract_load_error(screen_lines: list[str]) -> str | None:
    for line in screen_lines:
        stripped = line.strip()
        if re.search(r"\bSyntax Error\b", stripped, re.IGNORECASE):
            return stripped
    return None


def _screen_shows_loaded_line(screen_lines: list[str], program_line: str) -> bool:
    target = program_line.strip()
    return any(line.strip() == target for line in screen_lines)


def _capture_loaded_line_without_prompt(
    session: RuntimeSession,
    program_line: str,
    *,
    deadline: float | None = None,
    retries: int = 4,
) -> tuple[str, list[str]]:
    raw_screen = ""
    screen_lines: list[str] = []
    for _ in range(retries):
        _sleep_with_deadline(
            0.5,
            deadline=deadline,
            message="timed out waiting for BASIC prompt",
        )
        raw_screen = session.copy_screen(deadline=deadline)
        screen_lines = normalize_screen_text(raw_screen)
        if _screen_shows_loaded_line(screen_lines, program_line):
            return raw_screen, screen_lines
    return raw_screen, screen_lines


def _load_source_conservative(
    session: RuntimeSession,
    source_lines: list[str],
    raw_screen: str,
    *,
    deadline: float | None = None,
    optimize_for_run: bool,
) -> tuple[str, list[str]]:
    screen_lines = normalize_screen_text(raw_screen)
    allow_offscreen_prompt = len(source_lines) > FAST_LOADER_MAX_LINES
    prompt_visible = _screen_has_ready_prompt(raw_screen)
    for program_line in source_lines:
        before = raw_screen
        submit_line(session, program_line, deadline=deadline)
        if allow_offscreen_prompt and not prompt_visible:
            raw_screen, screen_lines = _capture_loaded_line_without_prompt(
                session,
                program_line,
                deadline=deadline,
            )
        else:
            try:
                raw_screen = wait_for_ready(
                    session,
                    before,
                    timeout=_remaining_timeout(
                        deadline,
                        fallback=10.0 if allow_offscreen_prompt else 20.0,
                        message="timed out waiting for BASIC prompt",
                    ),
                    deadline=deadline,
                )
            except BasicTimeoutError:
                if not allow_offscreen_prompt:
                    raise
                raw_screen, screen_lines = _capture_loaded_line_without_prompt(
                    session,
                    program_line,
                    deadline=deadline,
                )
            except BasicRuntimeError as exc:
                if "BASIC prompt" not in str(exc):
                    raise
                raw_screen, screen_lines = _capture_loaded_line_without_prompt(
                    session,
                    program_line,
                    deadline=deadline,
                )
            else:
                screen_lines = normalize_screen_text(raw_screen)
        load_error = _extract_load_error(screen_lines)
        if load_error is not None:
            raise BasicRuntimeError(load_error)
        if allow_offscreen_prompt and not _screen_has_ready_prompt(raw_screen):
            if not _screen_shows_loaded_line(screen_lines, program_line):
                raise BasicTimeoutError("timed out waiting for BASIC prompt")
            prompt_visible = False
        else:
            prompt_visible = True
    return raw_screen, screen_lines


def _load_source_batched(
    session: RuntimeSession,
    source_lines: list[str],
    raw_screen: str,
    *,
    deadline: float | None = None,
) -> tuple[str, list[str]]:
    screen_lines = normalize_screen_text(raw_screen)
    batch_size = LOAD_BATCH_SIZE
    if len(source_lines) > LOAD_BATCH_SIZE * 4 and max(len(line) for line in source_lines) <= 32:
        batch_size = LARGE_LOAD_BATCH_SIZE

    for start in range(0, len(source_lines), batch_size):
        batch = source_lines[start : start + batch_size]
        before = raw_screen
        for index, program_line in enumerate(batch):
            submit_line(session, program_line, deadline=deadline)
            if index + 1 != len(batch):
                _sleep_with_deadline(
                    LOAD_BATCH_DELAY,
                    deadline=deadline,
                    message="timed out waiting for BASIC prompt",
                )
        try:
            raw_screen = wait_for_ready(
                session,
                before,
                timeout=_remaining_timeout(
                    deadline,
                    fallback=60.0,
                    message="timed out waiting for BASIC prompt",
                ),
                deadline=deadline,
            )
        except BasicTimeoutError as exc:
            raise FastLoadFallbackRequired("fast loader timed out") from exc
        screen_lines = normalize_screen_text(raw_screen)
        load_error = _extract_load_error(screen_lines)
        if load_error is not None:
            raise FastLoadFallbackRequired(load_error)
    return raw_screen, screen_lines


def _load_source(
    session: RuntimeSession,
    source_text: str,
    raw_screen: str,
    *,
    deadline: float | None = None,
    use_fast_loader: bool,
    optimize_for_run: bool,
) -> tuple[str, list[str]]:
    source_lines = [line for line in source_text.split("\n") if line]
    if not source_lines:
        return raw_screen, normalize_screen_text(raw_screen)
    if use_fast_loader:
        return _load_source_batched(session, source_lines, raw_screen, deadline=deadline)
    return _load_source_conservative(
        session,
        source_lines,
        raw_screen,
        deadline=deadline,
        optimize_for_run=optimize_for_run,
    )


def wait_for_ready(
    session: RuntimeSession,
    previous: str,
    timeout: float = 20.0,
    *,
    deadline: float | None = None,
) -> str:
    wait_deadline = _effective_deadline(timeout, deadline=deadline, message="timed out waiting for BASIC prompt")
    started = time.monotonic()
    latest = previous
    previous_ready = _screen_has_ready_prompt(previous)
    saw_change = False
    while time.monotonic() < wait_deadline:
        latest = session.copy_screen(deadline=wait_deadline)
        if latest != previous:
            saw_change = True
        if _screen_has_ready_prompt(latest) and (
            not previous_ready or saw_change or time.monotonic() - started >= 0.8
        ):
            return latest
        _sleep_with_deadline(0.2, deadline=wait_deadline, message="timed out waiting for BASIC prompt")
    raise BasicTimeoutError("timed out waiting for BASIC prompt")


def _resolve_wait_deadline(
    timeout: float | None,
    *,
    deadline: float | None,
    message: str,
) -> float | None:
    if timeout is None:
        if deadline is None:
            return None
        _remaining_timeout(deadline, fallback=0.1, message=message)
        return deadline
    return _effective_deadline(timeout, deadline=deadline, message=message)


def collect_command_output(
    session: RuntimeSession,
    previous_lines: list[str],
    sent_text: str,
    timeout: float | None = 20.0,
    *,
    deadline: float | None = None,
    on_lines=None,
    ignored_source_lines: set[str] | None = None,
) -> tuple[str, list[str]]:
    message = "timed out waiting for BASIC prompt"
    wait_deadline = _resolve_wait_deadline(timeout, deadline=deadline, message=message)
    latest = previous_lines
    raw_screen = ""
    run_mode = any(line.strip().upper() == "RUN" for line in sent_text.replace("\r", "\n").split("\n") if line.strip())
    previous_output_lines: list[str] = []
    run_triggered = run_mode

    _sleep_with_deadline(
        0.2,
        deadline=wait_deadline,
        message=message,
    )
    raw_screen = session.copy_screen(deadline=wait_deadline)
    latest = normalize_screen_text(raw_screen)
    if run_mode:
        initial_lines, run_triggered = _extract_run_output_lines(
            latest,
            triggered=run_triggered,
            ignored_source_lines=ignored_source_lines,
        )
        previous_output_lines = list(initial_lines)
    else:
        initial_lines = filter_new_lines(screen_appended_lines(previous_lines, latest), sent_text)
    if initial_lines and on_lines is not None:
        on_lines(initial_lines)
    if _screen_has_ready_prompt(raw_screen):
        if run_mode and on_lines is not None:
            on_lines(["Ready"])
        return raw_screen, latest

    stagnant_since = None
    while wait_deadline is None or time.monotonic() < wait_deadline:
        raw_screen = session.copy_screen(deadline=wait_deadline)
        current = normalize_screen_text(raw_screen)
        if current != latest:
            if run_mode:
                current_output_lines, run_triggered = _extract_run_output_lines(
                    current,
                    triggered=run_triggered,
                    ignored_source_lines=ignored_source_lines,
                )
                if not current_output_lines:
                    latest = current
                    stagnant_since = None
                    continue
                new_lines, overlap = _merge_output_window(previous_output_lines, current_output_lines)
                if previous_output_lines and current_output_lines and overlap == 0:
                    latest = current
                    stagnant_since = None
                    continue
                previous_output_lines = current_output_lines
            else:
                new_lines = filter_new_lines(screen_appended_lines(latest, current), sent_text)
            if new_lines and on_lines is not None:
                on_lines(new_lines)
            latest = current
            stagnant_since = None
        else:
            if stagnant_since is None:
                stagnant_since = time.monotonic()

        if _screen_has_ready_prompt(raw_screen):
            if run_mode and on_lines is not None:
                on_lines(["Ready"])
            return raw_screen, current

        if stagnant_since is not None and time.monotonic() - stagnant_since >= 3:
            stripped_lines = [line.rstrip() for line in current if line.strip()]
            if stripped_lines and stripped_lines[-1].endswith("?"):
                raise BasicRuntimeError("program requested unsupported input during interactive execution")

        _sleep_with_deadline(
            0.2,
            deadline=wait_deadline,
            message=message,
        )
    raise BasicTimeoutError(message)


def collect_run_output(
    session: RuntimeSession,
    previous_lines,
    sent_text: str,
    timeout: float | None = 30.0,
    marker: str | None = None,
    *,
    deadline: float | None = None,
    on_lines=None,
):
    wait_deadline = _resolve_wait_deadline(
        timeout,
        deadline=deadline,
        message="timed out waiting for BASIC program completion",
    )
    latest = previous_lines
    emitted = []
    previous_output_lines: list[str] = []
    triggered = any(line.strip().upper() == "RUN" for line in sent_text.replace("\r", "\n").split("\n") if line.strip())
    _sleep_with_deadline(
        RUN_OUTPUT_POLL_DELAY,
        deadline=wait_deadline,
        message="timed out waiting for BASIC program completion",
    )
    raw_screen = session.copy_screen(deadline=wait_deadline)
    latest = normalize_screen_text(raw_screen)
    previous_output_lines, triggered = _extract_run_output_lines(
        latest,
        marker=marker,
        triggered=triggered,
    )
    emitted.extend(previous_output_lines)
    if previous_output_lines and on_lines is not None:
        on_lines(previous_output_lines)
    if _screen_lines_have_ready_prompt(latest):
        return latest, emitted

    def process_output_lines(current_output_lines: list[str]) -> None:
        nonlocal previous_output_lines, emitted
        new_lines, overlap = _merge_output_window(previous_output_lines, current_output_lines)
        if previous_output_lines and current_output_lines and overlap == 0:
            new_lines = current_output_lines
        for line in new_lines:
            if line:
                emitted.append(line)
        if new_lines and on_lines is not None:
            on_lines(new_lines)
        previous_output_lines = current_output_lines

    stagnant_since = None
    while wait_deadline is None or time.monotonic() < wait_deadline:
        raw_screen = session.copy_screen(deadline=wait_deadline)
        current = normalize_screen_text(raw_screen)
        current_changed = current != latest
        current_output_lines: list[str] = []
        if current_changed:
            current_output_lines, triggered = _extract_run_output_lines(
                current,
                marker=marker,
                triggered=triggered,
            )
            if not current_output_lines:
                latest = current
                stagnant_since = None
            else:
                process_output_lines(current_output_lines)
            latest = current
            stagnant_since = None
        else:
            if stagnant_since is None:
                stagnant_since = time.monotonic()

        if _screen_lines_have_ready_prompt(current):
            return current, emitted

        if stagnant_since is not None and time.monotonic() - stagnant_since >= 3:
            stripped_lines = [line.rstrip() for line in current if line.strip()]
            if stripped_lines and stripped_lines[-1].endswith("?"):
                raise BasicRuntimeError("program requested unsupported input during --run execution")

        _sleep_with_deadline(
            RUN_OUTPUT_POLL_DELAY,
            deadline=wait_deadline,
            message="timed out waiting for BASIC program completion",
        )
    raise BasicTimeoutError("timed out waiting for BASIC program completion")


def bootstrap_assets(*, deadline: float | None = None) -> None:
    result = run_command(
        ["bash", str(BOOTSTRAP_SCRIPT)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
        timeout_seconds=_command_timeout(
            deadline,
            fallback=30.0,
            message="timed out preparing FM-11 assets",
        ),
        timeout_error="timed out preparing FM-11 assets",
    )
    if result.returncode != 0:
        raise BasicRuntimeError(result.stderr.strip() or "asset bootstrap failed")
    if not EMULATOR_EXE.is_file():
        raise BasicRuntimeError(f"missing staged emulator: {EMULATOR_EXE}")
    if not FBASIC_DISK.is_file():
        raise BasicRuntimeError(f"missing staged F-BASIC disk image: {FBASIC_DISK}")
    for rom_name in REQUIRED_ROM_NAMES:
        rom_path = ROM_STAGE_DIR / rom_name
        if not rom_path.is_file():
            raise BasicRuntimeError(f"missing staged FM-11 ROM: {rom_path}")


def _try_fast_startup_ready(session: RuntimeSession, *, deadline: float | None = None) -> str | None:
    prompts = (
        "How many 1MB disk drives",
        "How many 320KB disk drives",
        "How many disk files",
    )
    answered_count = 0
    wait_deadline = time.monotonic() + FAST_STARTUP_READY_TIMEOUT
    if deadline is not None:
        wait_deadline = min(wait_deadline, deadline)
    latest = ""
    while time.monotonic() < wait_deadline:
        latest = session.copy_screen(deadline=deadline)
        if _screen_has_ready_prompt(latest):
            return latest
        prompt_index = _startup_prompt_index(latest, prompts)
        if prompt_index is not None and prompt_index >= answered_count:
            session.key("2", "Return", deadline=deadline)
            answered_count = prompt_index + 1
            _sleep_with_deadline(
                FAST_STARTUP_ANSWER_DELAY,
                deadline=deadline,
                message="timed out waiting for FM-11 startup prompt",
            )
            continue
        _sleep_with_deadline(
            STARTUP_POLL_DELAY,
            deadline=deadline,
            message="timed out waiting for FM-11 startup prompt",
        )
    return None


def handle_startup_prompts(session: RuntimeSession, *, deadline: float | None = None) -> str:
    prompts = (
        "How many 1MB disk drives",
        "How many 320KB disk drives",
        "How many disk files",
    )
    fast_screen = _try_fast_startup_ready(session, deadline=deadline)
    if fast_screen is not None:
        return fast_screen

    screen = session.copy_screen(deadline=deadline)
    remaining_prompts = prompts
    while remaining_prompts:
        if _screen_has_ready_prompt(screen):
            return screen

        matched_index = _startup_prompt_index(screen, remaining_prompts)
        if matched_index is None:
            screen = session.wait_for_text(
                remaining_prompts,
                timeout=8.0
                if deadline is None
                else _full_remaining_timeout(deadline, message="timed out waiting for FM-11 startup prompt"),
                deadline=deadline,
            )
            continue

        remaining_prompts = remaining_prompts[matched_index:]
        session.key("2", "Return", deadline=deadline)
        _sleep_with_deadline(
            FAST_STARTUP_ANSWER_DELAY,
            deadline=deadline,
            message="timed out waiting for FM-11 startup prompt",
        )
        screen = session.copy_screen(deadline=deadline)
        remaining_prompts = remaining_prompts[1:]

    return wait_for_ready(
        session,
        screen,
        timeout=15.0
        if deadline is None
        else _full_remaining_timeout(deadline, message="timed out waiting for BASIC prompt"),
        deadline=deadline,
    )


def _run_basic_once(
    args,
    source_text: str | None,
    *,
    deadline: float | None,
    interactive_tty: bool,
    use_fast_loader: bool,
) -> int:
    session = RuntimeSession()
    try:
        session.start(deadline=deadline)
        raw_screen = handle_startup_prompts(session, deadline=deadline)
        screen_lines = normalize_screen_text(raw_screen)
        if interactive_tty and source_text is None:
            emit_lines(interactive_startup_lines(screen_lines, None))

        if source_text is not None:
            raw_screen, screen_lines = _load_source(
                session,
                source_text,
                raw_screen,
                deadline=deadline,
                use_fast_loader=use_fast_loader,
                optimize_for_run=args.run,
            )
            if interactive_tty:
                emit_lines(interactive_startup_lines(screen_lines, source_text))

        if args.run:
            before = raw_screen
            submit_line(session, "CLS", deadline=deadline)
            try:
                raw_screen = wait_for_ready(
                    session,
                    before,
                    timeout=10.0
                    if deadline is None
                    else _full_remaining_timeout(deadline, message="timed out waiting for BASIC prompt"),
                    deadline=deadline,
                )
            except BasicTimeoutError:
                raise
            except BasicRuntimeError:
                raw_screen = session.copy_screen(deadline=deadline)
            screen_lines = normalize_screen_text(raw_screen)
            submit_line(session, 'PRINT "CBATCHBEGIN"', deadline=deadline)
            submit_line(session, "RUN", deadline=deadline)
            _, lines = collect_run_output(
                session,
                screen_lines,
                'PRINT "CBATCHBEGIN"\nRUN\n',
                timeout=None
                if deadline is None
                else _full_remaining_timeout(
                    deadline,
                    message="timed out waiting for BASIC program completion",
                ),
                marker="CBATCHBEGIN",
                deadline=deadline,
                on_lines=emit_lines,
            )
            return 0

        for raw_line in sys.stdin:
            line = raw_line.rstrip("\n")
            before = raw_screen
            before_lines = screen_lines
            submit_line(session, line, deadline=deadline)
            command_timeout: float | None = 20.0
            ignored_source_lines: set[str] | None = None
            if line.strip().upper() == "RUN":
                command_timeout = None
                if source_text is not None:
                    ignored_source_lines = {
                        source_line.strip().upper()
                        for source_line in source_text.splitlines()
                        if source_line.strip()
                    }
            raw_screen, screen_lines = collect_command_output(
                session,
                before_lines,
                line + "\n",
                timeout=command_timeout,
                deadline=deadline,
                on_lines=emit_lines,
                ignored_source_lines=ignored_source_lines,
            )
        return 0
    finally:
        session.close()


def _should_use_fast_loader(source_text: str | None) -> bool:
    if source_text is None:
        return False
    source_lines = [line for line in source_text.split("\n") if line]
    return len(source_lines) <= FAST_LOADER_MAX_LINES


def run_basic(args) -> int:
    require_tool("Xvfb")
    require_tool("xdpyinfo")
    require_tool("wine")
    require_tool("xdotool")
    require_tool("xclip")
    source_text = None
    if args.file:
        source_text = preprocess_fm11_source(read_basic_file(Path(args.file)))
        if args.run:
            reject_unsupported_run_input(source_text)

    interactive_tty = sys.stdin.isatty() and not args.run
    timeout_seconds = fbasic_batch.parse_timeout_spec(args.timeout)
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    bootstrap_assets(deadline=deadline)

    if interactive_tty:
        print("Starting FM-11 F-BASIC...", file=sys.stderr, flush=True)
    try:
        return _run_basic_once(
            args,
            source_text,
            deadline=deadline,
            interactive_tty=interactive_tty,
            use_fast_loader=_should_use_fast_loader(source_text),
        )
    except FastLoadFallbackRequired:
        return _run_basic_once(
            args,
            source_text,
            deadline=deadline,
            interactive_tty=interactive_tty,
            use_fast_loader=False,
        )


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("-f", "--file")
    parser.add_argument("-r", "--run", action="store_true")
    parser.add_argument("--timeout")
    args = parser.parse_args()
    if args.run and not args.file:
        parser.error("--run requires --file")
    if args.timeout is not None and not args.run:
        parser.error("--timeout requires --run --file")
    return args


def main() -> int:
    args = parse_args()
    try:
        return run_basic(args)
    except BrokenPipeError:
        return _broken_pipe_exit_code()
    except KeyboardInterrupt:
        return 130
    except BasicTimeoutError:
        if args.timeout:
            print(f"error: fm11basic batch run timed out after {args.timeout}", file=sys.stderr)
        else:
            print("error: fm11basic batch run timed out", file=sys.stderr)
        return 124
    except BasicRuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
