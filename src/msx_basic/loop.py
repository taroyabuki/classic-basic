"""Main terminal interaction loop for MSX-BASIC.

Bridges the user's terminal to the MSX running inside openMSX:
- reads typed characters and forwards them via ``type`` Tcl commands
- polls the MSX screen and outputs new/changed content to the terminal
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path
import sys
from collections.abc import Callable

from mandelbrot_output import MANDELBROT_ALLOWED_CHARS, extract_any_mandelbrot_block, has_segmented_mandelbrot_fragments

from .bridge import OpenMSXBridge
from .program_check import validate_program_text


def _is_subsequence(subseq: list[str], seq: list[str]) -> bool:
    """Return True if *subseq* appears as an ordered subsequence inside *seq*."""
    pos = 0
    for item in subseq:
        while pos < len(seq) and seq[pos] != item:
            pos += 1
        if pos >= len(seq):
            return False
        pos += 1
    return True


# MSX special keys sent via openMSX ``type`` escapes
_ENTER = "\r"
_BACKSPACE = "\b"

# How often to poll the MSX screen (seconds)
_POLL_INTERVAL = 0.05
_BATCH_INPUT_CHUNK_SIZE = 24
_BATCH_INPUT_PAUSE = 0.05
_BATCH_INPUT_TIMEOUT = 30.0
_INTERACTIVE_INPUT_TIMEOUT = 2.0
_VARTAB_ADDR = 0xF6C2
_FUNCTION_KEY_GUIDE = "color  auto   goto   list   run"
_BATCH_STABLE_SCREEN_POLLS = 6
_SOURCE_FRAGMENT_MIN_LENGTH = 12
_BLINK_BLOCK_CURSOR = "\x1b[1 q"
_SHOW_CURSOR = "\x1b[?25h"
_RESET_CURSOR_STYLE = "\x1b[0 q"
_SOURCE_LISTING_KEYWORDS = (
    "PRINT",
    "FOR",
    "NEXT",
    "IF",
    "THEN",
    "GOTO",
    "DIM",
    "DEF",
    "CLS",
)


def _is_width_80_command(text: str) -> bool:
    return re.fullmatch(r"WIDTH\s*80", text.strip(), re.IGNORECASE) is not None


def _parse_timeout_spec(spec: str | None) -> float | None:
    if spec is None:
        return None
    text = spec.strip()
    if text in {"", "0"}:
        return None

    import re

    match = re.fullmatch(r"([0-9]+(?:\.[0-9]*)?|[0-9]*\.[0-9]+)\s*([smhdSMHD]?)", text)
    if match is None:
        raise ValueError(f"invalid timeout spec: {spec}")

    value = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    factor = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[unit]
    return value * factor


_BATCH_RUN_TIMEOUT = _parse_timeout_spec(os.environ.get("CLASSIC_BASIC_MSX_BATCH_TIMEOUT"))


def _get_screen_state(bridge: OpenMSXBridge, *, timeout: float = 1.0) -> tuple[list[str], int]:
    try:
        return bridge.get_screen_state(timeout=timeout)
    except AttributeError:
        return bridge.get_screen(timeout=timeout), bridge.get_cursor_row()



def _cursor_line(lines: list[str], cursor_row: int) -> str:
    if 0 <= cursor_row < len(lines):
        return lines[cursor_row]
    return ""


def _last_non_empty_line_before_cursor(lines: list[str], cursor_row: int) -> str:
    start = min(cursor_row, len(lines) - 1)
    for row in range(start, -1, -1):
        stripped = lines[row].strip()
        if stripped and stripped != _FUNCTION_KEY_GUIDE:
            return lines[row]
    return ""


def _screen_ready_for_input(lines: list[str], cursor_row: int) -> bool:
    return not _cursor_line(lines, cursor_row).strip() and any(
        line.strip() == "Ok" for line in lines
    )


def _normalize_interactive_cursor_row(lines: list[str], cursor_row: int) -> int:
    if 0 <= cursor_row < len(lines) and _cursor_line(lines, cursor_row).strip() == "Ok":
        next_row = cursor_row + 1
        if next_row < len(lines) and not _cursor_line(lines, next_row).strip():
            return next_row
    return cursor_row


def _post_run_prompt_visible(lines: list[str], cursor_row: int) -> bool:
    return _last_non_empty_line_before_cursor(lines, cursor_row).strip() == "Ok"


def _last_row_with_text(lines: list[str], cursor_row: int, text: str) -> int | None:
    target = text.strip()
    start = min(cursor_row, len(lines) - 1)
    for row in range(start, -1, -1):
        if lines[row].strip() == target:
            return row
    return None


def _looks_like_source_listing(text: str) -> bool:
    import re

    match = re.match(r"^\s*(\d+)(?:\s+(.*))?$", text)
    if match is None:
        return False
    body = (match.group(2) or "").upper()
    if not body:
        return False
    if not any(char.isalpha() for char in body):
        return False
    if any(keyword in body for keyword in _SOURCE_LISTING_KEYWORDS):
        return True
    return any(token in body for token in ("=", "+", "-", "*", "/", "(", ")", ":", "<", ">"))


def _looks_like_wrapped_listing_fragment(text: str) -> bool:
    stripped = text.strip().upper()
    if not stripped:
        return False
    if re.fullmatch(r"O?TO\s+\d+", stripped) is not None:
        return True
    if re.fullmatch(r"[A-Z0-9*()+/\- ]+", stripped) is not None and len(stripped) <= 12:
        return stripped in {"TO 290", "TO 310", "TO 330", "OTO 350"}
    return False


def _looks_like_source_fragment(text: str) -> bool:
    upper = text.strip().upper()
    if len(upper) < _SOURCE_FRAGMENT_MIN_LENGTH:
        return False
    if any(keyword in upper for keyword in _SOURCE_LISTING_KEYWORDS):
        return True
    if any(keyword in upper for keyword in ("NEXT", "VARPTR", "PEEK", "POKE", "HEX$", "ATN")):
        return True
    return any(token in upper for token in (":", "(", ")", "=", "<", ">", "+", "-", "*", "/"))


def _looks_like_loaded_source_fragment(text: str, loaded_lines: list[str]) -> bool:
    stripped = text.strip()
    if not _looks_like_source_fragment(stripped):
        return False

    for loaded_line in loaded_lines:
        candidate = loaded_line.strip()
        if not candidate:
            continue
        if stripped in candidate:
            return True
        match = re.match(r"^\s*\d+\s*(.*)$", candidate)
        if match is not None:
            body = match.group(1).strip()
            if body and stripped in body:
                return True
    return False


def _build_ignored_source_lines(loaded_lines: list[str]) -> set[str]:
    ignored = {line.strip() for line in loaded_lines if line.strip()}
    for loaded_line in loaded_lines:
        candidate = loaded_line.strip()
        if not candidate:
            continue
        variants = [candidate]
        match = re.match(r"^\s*\d+\s*(.*)$", candidate)
        if match is not None:
            body = match.group(1).strip()
            if body:
                variants.append(body)
        for variant in variants:
            for start in range(1, len(variant)):
                fragment = variant[start:].strip()
                if _looks_like_source_fragment(fragment) or _looks_like_short_source_suffix(fragment):
                    ignored.add(fragment)
    return ignored


def _looks_like_short_source_suffix(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 8:
        return False
    if not any(token in stripped for token in ('"', ";", "(", ")", "$", "#", "%", "!", ":", "=", "+", "-", "*", "/")):
        return False
    return any(char.isdigit() or char.isalpha() for char in stripped)


class ScreenTracker:
    """Track cursor-line progress for the interactive terminal view."""

    def __init__(self) -> None:
        self._previous_lines: list[str] | None = None
        self._previous_cursor_row = 0

    def seed(self, lines: list[str], cursor_row: int) -> None:
        self._previous_lines = list(lines)
        self._previous_cursor_row = cursor_row

    def update(self, lines: list[str], cursor_row: int) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if self._previous_lines is not None:
            if cursor_row > self._previous_cursor_row:
                start_row = max(0, self._previous_cursor_row - 1)
                for row in range(start_row, min(cursor_row, len(lines))):
                    completed_line = lines[row].rstrip()
                    if completed_line.strip():
                        events.append(("line", completed_line))
            else:
                previous_cursor_line = _cursor_line(
                    self._previous_lines, self._previous_cursor_row
                ).rstrip()
                if (
                    cursor_row != self._previous_cursor_row
                    and previous_cursor_line.strip()
                    and previous_cursor_line.strip() != "Ok"
                ):
                    events.append(("line", previous_cursor_line))

        cursor_text = _cursor_line(lines, cursor_row)
        if (
            cursor_row == self._previous_cursor_row
            and not cursor_text.strip()
            and cursor_row > 0
        ):
            candidate = lines[cursor_row - 1]
            if candidate.strip() and candidate.strip() not in {"Ok", _FUNCTION_KEY_GUIDE}:
                cursor_text = candidate
        if cursor_text.strip().casefold() == _FUNCTION_KEY_GUIDE.casefold():
            cursor_text = ""
        if not cursor_text.strip():
            cursor_text = ""
        events.append(("cursor", cursor_text))
        self._previous_lines = list(lines)
        self._previous_cursor_row = cursor_row
        return events


class BatchOutputTracker:
    """Collect stable output lines from successive MSX screen snapshots."""

    def __init__(self, ignored_lines: set[str] | None = None) -> None:
        self._previous_lines: list[str] | None = None
        self._previous_cursor_row = 0
        self._lines: list[str] = []
        self._ignored_lines = ignored_lines or set()

    def observe(self, lines: list[str], cursor_row: int) -> None:
        self._previous_lines = list(lines)
        self._previous_cursor_row = cursor_row

    def finish(self, lines: list[str], cursor_row: int) -> list[str]:
        self.observe(lines, cursor_row)
        return list(self._lines)

    def capture_window(self, lines: list[str], start_row: int, stop_row: int) -> None:
        candidates: list[str] = []
        for row in range(max(0, start_row), min(stop_row, len(lines))):
            normalized = self._normalize(lines[row])
            if normalized is not None:
                candidates.append(normalized)
        if not candidates:
            return

        # Otherwise find the longest suffix of _lines that matches a prefix of
        # candidates and append only the new tail.
        overlap = min(len(self._lines), len(candidates))
        while overlap > 0:
            if self._lines[-overlap:] == candidates[:overlap]:
                break
            overlap -= 1
        self._lines.extend(candidates[overlap:])

    def _append(self, line: str) -> None:
        stripped = self._normalize(line)
        if stripped is None:
            return
        if self._lines and self._lines[-1] == stripped:
            return
        self._lines.append(stripped)

    def _normalize(self, line: str) -> str | None:
        normalized = line
        stripped = normalized.strip()
        if not stripped or stripped in {"Ok", "RUN", "R"}:
            return None
        if _is_width_80_command(stripped):
            return None
        if stripped == _FUNCTION_KEY_GUIDE:
            return None
        if stripped.startswith("MSX BASIC version"):
            return None
        if stripped.startswith("Copyright 1983 by Microsoft"):
            return None
        if re.fullmatch(r"\d+ Bytes free", stripped) is not None:
            return None
        if _looks_like_source_listing(stripped):
            return None
        if _looks_like_wrapped_listing_fragment(stripped):
            return None
        if stripped in self._ignored_lines:
            return None
        if _looks_like_mandelbrot_fragment_text(normalized):
            return normalized
        return stripped


def _looks_like_mandelbrot_fragment_text(text: str) -> bool:
    if not text or len(text) > 79:
        return False
    if all(char == " " for char in text):
        return False
    return all(char in MANDELBROT_ALLOWED_CHARS for char in text)


def _emit_events(events: list[tuple[str, str]]) -> None:
    for kind, text in events:
        if kind == "line" and text:
            print(text)


def _startup_terminal_render(lines: list[str], cursor_row: int) -> str:
    if _screen_ready_for_input(lines, cursor_row):
        return "Ok\r\n"
    return ""


def _loaded_program_listing_render(loaded_program_lines: list[str] | None) -> str:
    if not loaded_program_lines:
        return ""
    return "".join(f"{line}\r\n" for line in loaded_program_lines if line)


def _interactive_echo_key(text: str) -> str:
    return text.strip().casefold()


def _normalize_interactive_completed_line(text: str) -> str | None:
    completed = text.rstrip()
    stripped = completed.strip()
    if not stripped:
        return None
    if stripped.casefold() == _FUNCTION_KEY_GUIDE.casefold():
        return None
    if stripped.casefold() == "ok":
        return "Ok"
    return completed


def run_loop(bridge: OpenMSXBridge, terminal: "RawTerminal") -> None:  # type: ignore[name-defined]
    """Run the interactive terminal loop until the user exits.

    ``terminal`` must expose ``input_ready()``, ``read_byte()``, and
    ``write(str)`` (same interface as ``z80_basic.terminal.RawTerminal``).
    """
    tracker = ScreenTracker()
    last_poll = 0.0
    current_cursor_line = ""
    local_input_line = ""
    pending_submitted_line: str | None = None
    try:
        initial_lines, initial_cursor_row = _get_screen_state(bridge, timeout=1.0)
    except TimeoutError:
        initial_lines, initial_cursor_row = [""] * 24, 0
    initial_cursor_row = _normalize_interactive_cursor_row(initial_lines, initial_cursor_row)
    tracker.seed(initial_lines, initial_cursor_row)

    while True:
        now = time.monotonic()

        # ---- Handle keyboard input ----
        if terminal.input_ready():
            byte = terminal.read_byte()

            if byte == 0x04:  # Ctrl-D → exit
                terminal.write("\r\n[Exit]\r\n")
                return

            if byte == 0x03:  # Ctrl-C → send Break to MSX
                bridge.type_text("\\b", via_keybuf=True, timeout=_INTERACTIVE_INPUT_TIMEOUT)
                # Send Ctrl-C as a regular character; MSX BASIC interprets it
                bridge.command_nowait("keymatrixdown 6 4")  # STOP key row 6 bit 4
                time.sleep(0.05)
                bridge.command_nowait("keymatrixup 6 4")
                continue

            if byte == 0x0D or byte == 0x0A:  # Enter
                bridge.type_text(_ENTER, via_keybuf=True, timeout=_INTERACTIVE_INPUT_TIMEOUT)
                terminal.write("\r\n")
                pending_submitted_line = local_input_line
                local_input_line = ""
                current_cursor_line = ""
            elif byte == 0x7F or byte == 0x08:  # Backspace / DEL
                bridge.type_text(_BACKSPACE, via_keybuf=True, timeout=_INTERACTIVE_INPUT_TIMEOUT)
                if local_input_line:
                    local_input_line = local_input_line[:-1]
                    terminal.write("\b \b")
                    current_cursor_line = local_input_line
            elif byte == 0x1B:  # Escape – eat it (could be ANSI sequence)
                # Drain any ANSI sequence bytes
                time.sleep(0.02)
                while terminal.input_ready():
                    terminal.read_byte()
            elif 0x20 <= byte <= 0x7E:  # Printable ASCII
                char = chr(byte)
                # Escape special Tcl/openMSX type characters
                if char in ('"', "\\"):
                    bridge.type_text(f"\\{char}", via_keybuf=True, timeout=_INTERACTIVE_INPUT_TIMEOUT)
                else:
                    bridge.type_text(char, via_keybuf=True, timeout=_INTERACTIVE_INPUT_TIMEOUT)
                terminal.write(char)
                local_input_line += char
                current_cursor_line = local_input_line
            # ignore other control codes

        # ---- Poll MSX screen ----
        if now - last_poll >= _POLL_INTERVAL:
            last_poll = now
            try:
                lines, cursor_row = _get_screen_state(bridge, timeout=1.0)
            except TimeoutError:
                continue
            cursor_row = _normalize_interactive_cursor_row(lines, cursor_row)

            events = tracker.update(lines, cursor_row)

            for kind, text in events:
                if kind == "line":
                    normalized_text = _normalize_interactive_completed_line(text)
                    if normalized_text is None:
                        continue
                    if (
                        pending_submitted_line is not None
                        and _interactive_echo_key(normalized_text)
                        == _interactive_echo_key(pending_submitted_line)
                    ):
                        pending_submitted_line = None
                        continue
                    pending_submitted_line = None
                    # Overwrite the current cursor line with the completed line,
                    # then move to the next line
                    terminal.write(f"\r{normalized_text}\r\n")
                    current_cursor_line = ""
                elif kind == "cursor":
                    if local_input_line:
                        continue
                    if pending_submitted_line is not None:
                        if _interactive_echo_key(text) == _interactive_echo_key(pending_submitted_line):
                            continue
                        if text.strip() or not _screen_ready_for_input(lines, cursor_row):
                            continue
                        pending_submitted_line = None
                    if text != current_cursor_line:
                        terminal.write(f"\r{text}")
                        # Erase to end of line in case text got shorter
                        terminal.write("\x1b[K")
                        current_cursor_line = text
        else:
            # Don't busy-loop when no input and no poll due
            time.sleep(0.005)


# ---------------------------------------------------------------------------
# Import here to avoid a circular dependency (terminal module is in z80_basic)
# ---------------------------------------------------------------------------

def _get_raw_terminal():
    """Return a RawTerminal from z80_basic.terminal (shared implementation)."""
    try:
        from z80_basic.terminal import RawTerminal  # type: ignore[import-untyped]
        return RawTerminal
    except ImportError:
        pass
    # Fallback: inline minimal implementation
    import os
    import select
    import termios
    import tty

    class RawTerminal:  # type: ignore[no-redef]
        def __init__(self) -> None:
            self._fd = sys.stdin.fileno()
            self._saved = None

        def __enter__(self) -> "RawTerminal":
            self._saved = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
            return self

        def __exit__(self, *_) -> None:
            if self._saved is not None:
                termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

        def input_ready(self) -> bool:
            r, _, _ = select.select([self._fd], [], [], 0.0)
            return bool(r)

        def read_byte(self) -> int:
            data = os.read(self._fd, 1)
            return data[0] if data else 0x04

        def write(self, text: str) -> None:
            os.write(sys.stdout.fileno(), text.encode("utf-8", errors="replace"))

    return RawTerminal


def run_interactive(bridge: OpenMSXBridge, loaded_program_lines: list[str] | None = None) -> None:
    """Start the interactive MSX-BASIC terminal session."""
    RawTerminal = _get_raw_terminal()
    print("Booting MSX-BASIC… (Ctrl-D to exit)", flush=True)
    booted = bridge.wait_for_boot(timeout=10)
    if not booted:
        print(
            "warning: MSX-BASIC Ok prompt not detected within 10 s "
            "(wrong machine, missing ROM, or slow boot). Continuing anyway.",
            flush=True,
        )
    with RawTerminal() as terminal:
        terminal.write(_SHOW_CURSOR)
        terminal.write(_BLINK_BLOCK_CURSOR)
        try:
            if booted:
                terminal.write("MSX-BASIC ready.\r\n")
            try:
                lines, cursor_row = _get_screen_state(bridge, timeout=1.0)
            except TimeoutError:
                lines, cursor_row = [""] * 24, 0
            cursor_row = _normalize_interactive_cursor_row(lines, cursor_row)
            startup_render = _startup_terminal_render(lines, cursor_row)
            if startup_render:
                terminal.write(startup_render)
            loaded_program_render = _loaded_program_listing_render(loaded_program_lines)
            if loaded_program_render:
                terminal.write(loaded_program_render)
            run_loop(bridge, terminal)
        finally:
            terminal.write(_RESET_CURSOR_STYLE)
            terminal.write(_SHOW_CURSOR)


def _configure_batch_speed(bridge: OpenMSXBridge) -> None:
    command = getattr(bridge, "command", None)
    if command is None:
        return
    command("set throttle off", timeout=3.0)
    command("set speed 999", timeout=3.0)


def run_batch(bridge: OpenMSXBridge, program: Path) -> int:
    """Load a BASIC source file, RUN it, and print completed output lines."""
    booted = bridge.wait_for_boot(timeout=10)
    if not booted:
        print("warning: MSX-BASIC Ok prompt not detected within 10 s.", file=sys.stderr)

    _wait_for_stable_ok_prompt(bridge, timeout=4.0)
    _configure_batch_speed(bridge)
    loaded_lines = _load_program_lines(bridge, program)
    pre_run_commands = _batch_pre_run_commands(program)
    ignored_lines = _build_ignored_source_lines(loaded_lines)
    ignored_lines.update(pre_run_commands)

    initial_lines, initial_cursor_row = _get_screen_state(bridge, timeout=1.0)
    output_lines = _run_loaded_program(
        bridge,
        initial_lines=initial_lines,
        initial_cursor_row=initial_cursor_row,
        pre_run_commands=pre_run_commands,
        ignored_lines=ignored_lines,
        emit_lines=None,
    )
    _print_batch_result(
        _filter_batch_result_lines(output_lines, loaded_lines, ignored_lines),
        loaded_lines,
        ignored_lines,
    )
    return 0


def run_loaded_batch(bridge: OpenMSXBridge, program: Path) -> int:
    """Type a BASIC source file into memory via the interactive path, then RUN it."""
    source_path = program.expanduser().resolve()
    source_text = source_path.read_text(encoding="ascii")
    validate_program_text(source_text)
    run_load(bridge, program)
    _configure_batch_speed(bridge)
    loaded_lines = [
        line.rstrip("\r")
        for line in source_text.splitlines()
        if line.rstrip("\r")
    ]
    pre_run_commands = _batch_pre_run_commands(program)
    ignored_lines = _build_ignored_source_lines(loaded_lines)
    ignored_lines.update(pre_run_commands)
    initial_lines, initial_cursor_row = _get_screen_state(bridge, timeout=1.0)
    output_lines = _run_loaded_program(
        bridge,
        initial_lines=initial_lines,
        initial_cursor_row=initial_cursor_row,
        pre_run_commands=pre_run_commands,
        ignored_lines=ignored_lines,
        emit_lines=None,
    )
    _print_batch_result(
        _filter_batch_result_lines(output_lines, loaded_lines, ignored_lines),
        loaded_lines,
        ignored_lines,
    )
    return 0


def run_load(bridge: OpenMSXBridge, program: Path) -> list[str]:
    """Type a BASIC source file into memory without running it.

    After this returns the MSX-BASIC Ok prompt is active and the caller can
    hand control to the user (e.g. by calling :func:`run_interactive`).
    """
    booted = bridge.wait_for_boot(timeout=10)
    if not booted:
        print(
            "warning: MSX-BASIC Ok prompt not detected within 10 s "
            "(wrong machine, missing ROM, or slow boot). Continuing anyway.",
            flush=True,
        )

    _wait_for_stable_ok_prompt(bridge, timeout=4.0)
    bridge.command("set throttle off", timeout=3.0)
    bridge.command("set speed 999", timeout=3.0)

    loaded_lines = _load_program_lines(bridge, program)

    # Wait for the last line to be accepted and the Ok prompt to reappear.
    _ensure_prompt_after_load(bridge, timeout=4.0)
    return loaded_lines


def _load_program_lines(bridge: OpenMSXBridge, program: Path) -> list[str]:
    loaded_lines: list[str] = []
    source_path = program.expanduser().resolve()
    for raw_line in source_path.read_text(encoding="ascii").splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            continue
        loaded_lines.append(line)
        _type_program_line(bridge, line)
    return loaded_lines


def _run_loaded_program(
    bridge: OpenMSXBridge,
    *,
    initial_lines: list[str],
    initial_cursor_row: int,
    pre_run_commands: list[str],
    ignored_lines: set[str],
    emit_lines: Callable[[list[str]], None] | None = None,
) -> list[str]:
    _wait_for_stable_ok_prompt(bridge, timeout=4.0)
    for command in pre_run_commands:
        bridge.type_text(f"{command}\r", via_keybuf=True, timeout=_BATCH_INPUT_TIMEOUT)
        _wait_for_stable_ok_prompt(bridge, timeout=4.0)
    bridge.type_text("RUN\r", via_keybuf=True, timeout=_BATCH_INPUT_TIMEOUT)
    return _drain_until_prompt(
        bridge,
        timeout=_BATCH_RUN_TIMEOUT,
        initial_lines=initial_lines,
        initial_cursor_row=initial_cursor_row,
        ignored_lines=ignored_lines,
        emit_lines=emit_lines,
    )


def _drain_screen(bridge: OpenMSXBridge, tracker: ScreenTracker, *, duration: float) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            lines, cursor_row = _get_screen_state(bridge, timeout=1.0)
        except TimeoutError:
            return
        _emit_events(tracker.update(lines, cursor_row))
        time.sleep(_POLL_INTERVAL)


def _wait_for_ok_prompt(bridge: OpenMSXBridge, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lines, cursor_row = _get_screen_state(bridge, timeout=1.0)
        except TimeoutError:
            continue
        if _screen_ready_for_input(lines, cursor_row):
            return True
        time.sleep(_POLL_INTERVAL)
    return False


def _wait_for_stable_ok_prompt(
    bridge: OpenMSXBridge, *, timeout: float, stable_polls: int = 3
) -> bool:
    deadline = time.monotonic() + timeout
    previous_state: tuple[tuple[str, ...], int] | None = None
    stable_count = 0
    while time.monotonic() < deadline:
        try:
            lines, cursor_row = _get_screen_state(bridge, timeout=1.0)
        except TimeoutError:
            continue
        if not _screen_ready_for_input(lines, cursor_row):
            previous_state = None
            stable_count = 0
            time.sleep(_POLL_INTERVAL)
            continue
        state = (tuple(lines), cursor_row)
        if state == previous_state:
            stable_count += 1
        else:
            previous_state = state
            stable_count = 0
        if stable_count >= stable_polls:
            return True
        time.sleep(_POLL_INTERVAL)
    return False


def _ensure_prompt_after_load(bridge: OpenMSXBridge, *, timeout: float) -> bool:
    if _wait_for_stable_ok_prompt(bridge, timeout=timeout):
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        bridge.type_text("\r", via_keybuf=True, timeout=_BATCH_INPUT_TIMEOUT)
        if _wait_for_stable_ok_prompt(bridge, timeout=2.0):
            return True
    return False


def _type_program_line(bridge: OpenMSXBridge, line: str) -> None:
    baseline_lines, baseline_cursor_row = _get_screen_state(bridge, timeout=1.0)
    baseline_program_end = _get_program_end_pointer(bridge)
    _type_text_chunks(bridge, line)
    bridge.type_text("\r", via_keybuf=True, timeout=_BATCH_INPUT_TIMEOUT)
    _wait_for_line_entry(
        bridge,
        baseline_lines,
        baseline_cursor_row,
        baseline_program_end,
        timeout=2.0,
    )


def _type_text_chunks(bridge: OpenMSXBridge, text: str) -> None:
    for start in range(0, len(text), _BATCH_INPUT_CHUNK_SIZE):
        chunk = text[start : start + _BATCH_INPUT_CHUNK_SIZE]
        _type_text_chunk(bridge, chunk)
        time.sleep(_BATCH_INPUT_PAUSE)


def _type_text_chunk(bridge: OpenMSXBridge, chunk: str) -> None:
    """Type one chunk, splitting it if keybuf injection times out."""
    try:
        bridge.type_text(chunk, via_keybuf=True, timeout=_BATCH_INPUT_TIMEOUT)
    except TimeoutError:
        if len(chunk) <= 1:
            raise
        midpoint = len(chunk) // 2
        _type_text_chunk(bridge, chunk[:midpoint])
        time.sleep(_BATCH_INPUT_PAUSE)
        _type_text_chunk(bridge, chunk[midpoint:])


def _wait_for_line_entry(
    bridge: OpenMSXBridge,
    baseline_lines: list[str],
    baseline_cursor_row: int,
    baseline_program_end: int | None,
    *,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lines, cursor_row = _get_screen_state(bridge, timeout=1.0)
        except TimeoutError:
            continue
        current_program_end = _get_program_end_pointer(bridge)
        if (
            baseline_program_end is not None
            and current_program_end is not None
            and current_program_end != baseline_program_end
        ):
            return True
        if (
            not _cursor_line(lines, cursor_row).strip()
            and (cursor_row > baseline_cursor_row or lines != baseline_lines)
        ):
            return True
        time.sleep(_POLL_INTERVAL)
    return False


def _get_program_end_pointer(bridge: OpenMSXBridge) -> int | None:
    """Return VARTAB, which matches the end of the program before RUN."""
    try:
        return bridge.peek16(_VARTAB_ADDR, timeout=1.0)
    except (AttributeError, TimeoutError, ValueError):
        return None


def _drain_until_prompt(
    bridge: OpenMSXBridge,
    *,
    timeout: float | None,
    initial_lines: list[str],
    initial_cursor_row: int,
    ignored_lines: set[str],
    emit_lines: Callable[[list[str]], None] | None = None,
) -> list[str]:
    deadline = None if timeout is None else time.monotonic() + timeout
    tracker = BatchOutputTracker(ignored_lines)
    last_lines = list(initial_lines)
    last_cursor_row = initial_cursor_row
    saw_prompt_departure = False
    while deadline is None or time.monotonic() < deadline:
        try:
            lines, cursor_row = _get_screen_state(bridge, timeout=1.0)
        except TimeoutError:
            continue
        if not saw_prompt_departure:
            saw_prompt_departure = cursor_row != initial_cursor_row or lines != initial_lines
        if saw_prompt_departure:
            previous_count = len(tracker._lines)
            tracker.observe(lines, cursor_row)
            tracker.capture_window(lines, 0, cursor_row)
            if emit_lines is not None and len(tracker._lines) > previous_count:
                emit_lines(tracker._lines)
            if _post_run_prompt_visible(lines, cursor_row):
                previous_count = len(tracker._lines)
                tracker.capture_window(lines, initial_cursor_row, cursor_row)
                run_row = _last_row_with_text(lines, cursor_row, "RUN")
                if run_row is not None:
                    tracker.capture_window(lines, run_row + 1, cursor_row)
                if emit_lines is not None and len(tracker._lines) > previous_count:
                    emit_lines(tracker._lines)
                return tracker.finish(lines, cursor_row)
        last_lines = lines
        last_cursor_row = cursor_row
        time.sleep(_POLL_INTERVAL)
    return tracker.finish(last_lines, last_cursor_row)


def _print_batch_result(
    lines: list[str],
    loaded_lines: list[str],
    ignored_lines: set[str] | None = None,
) -> None:
    for line in _filter_batch_result_lines(lines, loaded_lines, ignored_lines):
        print(line)


def _emit_filtered_batch_lines(
    lines: list[str],
    loaded_lines: list[str],
    emitted_lines: list[str],
    ignored_lines: set[str] | None = None,
) -> None:
    filtered_lines = _filter_batch_result_lines(lines, loaded_lines, ignored_lines)
    prefix = 0
    limit = min(len(emitted_lines), len(filtered_lines))
    while prefix < limit and emitted_lines[prefix] == filtered_lines[prefix]:
        prefix += 1
    for line in filtered_lines[prefix:]:
        print(line)
    emitted_lines[:] = filtered_lines


def _filter_batch_result_lines(
    lines: list[str],
    loaded_lines: list[str],
    ignored_lines: set[str] | None = None,
) -> list[str]:
    mandelbrot_block = extract_any_mandelbrot_block(lines)
    if mandelbrot_block:
        return mandelbrot_block
    if _has_segmented_mandelbrot_fragments_with_gutter(lines):
        return []
    source_lines = {line.strip() for line in loaded_lines}
    ignored = ignored_lines if ignored_lines is not None else _build_ignored_source_lines(loaded_lines)
    filtered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _is_width_80_command(stripped):
            continue
        if stripped in source_lines:
            continue
        if stripped in ignored:
            continue
        if _looks_like_loaded_source_fragment(stripped, loaded_lines):
            continue
        filtered.append(line)
    return _merge_wrapped_batch_result_lines(filtered)


def _has_segmented_mandelbrot_fragments_with_gutter(lines: list[str]) -> bool:
    materialized = [line.rstrip("\r\n") for line in lines]
    if has_segmented_mandelbrot_fragments(materialized) or _has_plain_fragment_run(materialized):
        return True
    for indent in range(1, 5):
        prefix = " " * indent
        dedented = [line[indent:] if line.startswith(prefix) else line for line in materialized]
        if has_segmented_mandelbrot_fragments(dedented) or _has_plain_fragment_run(dedented):
            return True
    return False


def _has_plain_fragment_run(lines: list[str]) -> bool:
    run = 0
    for line in lines:
        stripped = line.strip()
        if (
            stripped
            and all(char in MANDELBROT_ALLOWED_CHARS for char in stripped)
            and (len(stripped) == 5 or 25 <= len(stripped) <= 37)
        ):
            run += 1
            if run >= 3:
                return True
        else:
            run = 0
    return False


def _batch_pre_run_commands(program: Path) -> list[str]:
    return []


def _merge_wrapped_batch_result_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (
            merged
            and re.fullmatch(r"\d+\s*/\s*\d+", merged[-1].strip()) is not None
            and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[ED][+-]?\d+)?", stripped, re.IGNORECASE) is not None
        ):
            merged[-1] = f"{merged[-1].rstrip()} {stripped}"
            continue
        merged.append(line)
    return merged
