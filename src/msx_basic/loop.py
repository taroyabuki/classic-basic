"""Main terminal interaction loop for MSX-BASIC.

Bridges the user's terminal to the MSX running inside openMSX:
- reads typed characters and forwards them via ``type`` Tcl commands
- polls the MSX screen and outputs new/changed content to the terminal
"""
from __future__ import annotations

import os
import time
from pathlib import Path
import sys

from .bridge import OpenMSXBridge


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
_ENTER = r"\r"
_BACKSPACE = r"\b"

# How often to poll the MSX screen (seconds)
_POLL_INTERVAL = 0.05
_BATCH_INPUT_CHUNK_SIZE = 48
_BATCH_INPUT_PAUSE = 0.02
_VARTAB_ADDR = 0xF6C2
_FUNCTION_KEY_GUIDE = "color  auto   goto   list   run"


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


def _post_run_prompt_visible(lines: list[str], cursor_row: int) -> bool:
    return _last_non_empty_line_before_cursor(lines, cursor_row).strip() == "Ok"


def _last_row_with_text(lines: list[str], cursor_row: int, text: str) -> int | None:
    target = text.strip()
    start = min(cursor_row, len(lines) - 1)
    for row in range(start, -1, -1):
        if lines[row].strip() == target:
            return row
    return None


class ScreenTracker:
    """Track cursor-line progress for the interactive terminal view."""

    def __init__(self) -> None:
        self._previous_lines: list[str] | None = None
        self._previous_cursor_row = 0

    def update(self, lines: list[str], cursor_row: int) -> list[tuple[str, str]]:
        events: list[tuple[str, str]] = []
        if self._previous_lines is not None:
            previous_cursor_line = _cursor_line(
                self._previous_lines, self._previous_cursor_row
            ).rstrip()
            if (
                cursor_row != self._previous_cursor_row
                and previous_cursor_line.strip()
                and previous_cursor_line.strip() != "Ok"
            ):
                events.append(("line", previous_cursor_line))

        cursor_text = _cursor_line(lines, cursor_row).rstrip()
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
        if self._previous_lines is not None:
            if cursor_row != self._previous_cursor_row:
                start = min(self._previous_cursor_row, len(lines))
                stop = min(cursor_row, len(lines))
                for row in range(start, stop):
                    self._append(lines[row])

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

        # If _lines is a subsequence of candidates, the screen snapshot contains
        # a complete view (observe may have missed lines due to get_screen /
        # get_cursor_row race).  Replace _lines with the fuller candidates list.
        if _is_subsequence(self._lines, candidates):
            self._lines = list(candidates)
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
        stripped = line.strip()
        if not stripped or stripped in {"Ok", "RUN"}:
            return None
        if stripped == _FUNCTION_KEY_GUIDE:
            return None
        if stripped in self._ignored_lines:
            return None
        return stripped


def _emit_events(events: list[tuple[str, str]]) -> None:
    for kind, text in events:
        if kind == "line" and text:
            print(text)


def run_loop(bridge: OpenMSXBridge, terminal: "RawTerminal") -> None:  # type: ignore[name-defined]
    """Run the interactive terminal loop until the user exits.

    ``terminal`` must expose ``input_ready()``, ``read_byte()``, and
    ``write(str)`` (same interface as ``z80_basic.terminal.RawTerminal``).
    """
    tracker = ScreenTracker()
    last_poll = 0.0
    current_cursor_line = ""

    while True:
        now = time.monotonic()

        # ---- Handle keyboard input ----
        if terminal.input_ready():
            byte = terminal.read_byte()

            if byte == 0x04:  # Ctrl-D → exit
                terminal.write("\r\n[Exit]\r\n")
                return

            if byte == 0x03:  # Ctrl-C → send Break to MSX
                bridge.type_text("\\b")  # openMSX uses \\b for Break? Actually STOP key
                # Send Ctrl-C as a regular character; MSX BASIC interprets it
                bridge.command_nowait("keymatrixdown 6 4")  # STOP key row 6 bit 4
                time.sleep(0.05)
                bridge.command_nowait("keymatrixup 6 4")
                continue

            if byte == 0x0D or byte == 0x0A:  # Enter
                bridge.type_text(_ENTER)
            elif byte == 0x7F or byte == 0x08:  # Backspace / DEL
                bridge.type_text(_BACKSPACE)
            elif byte == 0x1B:  # Escape – eat it (could be ANSI sequence)
                # Drain any ANSI sequence bytes
                time.sleep(0.02)
                while terminal.input_ready():
                    terminal.read_byte()
            elif 0x20 <= byte <= 0x7E:  # Printable ASCII
                char = chr(byte)
                # Escape special Tcl/openMSX type characters
                if char in ('"', "\\"):
                    bridge.type_text(f"\\{char}")
                else:
                    bridge.type_text(char)
            # ignore other control codes

        # ---- Poll MSX screen ----
        if now - last_poll >= _POLL_INTERVAL:
            last_poll = now
            try:
                lines = bridge.get_screen(timeout=1.0)
                cursor_row = bridge.get_cursor_row()
            except TimeoutError:
                continue

            events = tracker.update(lines, cursor_row)

            for kind, text in events:
                if kind == "line":
                    # Overwrite the current cursor line with the completed line,
                    # then move to the next line
                    terminal.write(f"\r{text}\r\n")
                    current_cursor_line = ""
                elif kind == "cursor":
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


def run_interactive(bridge: OpenMSXBridge) -> None:
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
    else:
        print("MSX-BASIC ready.\r", flush=True)

    with RawTerminal() as terminal:
        run_loop(bridge, terminal)


def run_batch(bridge: OpenMSXBridge, program: Path) -> int:
    """Load a BASIC source file, RUN it, and print completed output lines."""
    booted = bridge.wait_for_boot(timeout=10)
    if not booted:
        print("warning: MSX-BASIC Ok prompt not detected within 10 s.", file=sys.stderr)

    _wait_for_ok_prompt(bridge, timeout=4.0)
    loaded_lines = _load_program_lines(bridge, program)

    initial_lines = bridge.get_screen(timeout=1.0)
    initial_cursor_row = bridge.get_cursor_row()
    output_lines = _run_loaded_program(
        bridge,
        initial_lines=initial_lines,
        initial_cursor_row=initial_cursor_row,
        ignored_lines={line.strip() for line in loaded_lines},
    )
    _print_batch_result(output_lines, loaded_lines)
    return 0


def run_loaded_batch(bridge: OpenMSXBridge, program: Path) -> int:
    """Type a BASIC source file into memory via the interactive path, then RUN it."""
    run_load(bridge, program)
    loaded_lines = [
        line.rstrip("\r")
        for line in program.expanduser().resolve().read_text(encoding="utf-8").splitlines()
        if line.rstrip("\r")
    ]
    initial_lines = bridge.get_screen(timeout=1.0)
    initial_cursor_row = bridge.get_cursor_row()
    output_lines = _run_loaded_program(
        bridge,
        initial_lines=initial_lines,
        initial_cursor_row=initial_cursor_row,
        ignored_lines={line.strip() for line in loaded_lines},
    )
    _print_batch_result(output_lines, loaded_lines)
    return 0


def run_load(bridge: OpenMSXBridge, program: Path) -> None:
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

    _wait_for_ok_prompt(bridge, timeout=4.0)

    _load_program_lines(bridge, program)

    # Wait for the last line to be accepted and the Ok prompt to reappear.
    _wait_for_ok_prompt(bridge, timeout=4.0)


def _load_program_lines(bridge: OpenMSXBridge, program: Path) -> list[str]:
    loaded_lines: list[str] = []
    source_path = program.expanduser().resolve()
    for raw_line in source_path.read_text(encoding="utf-8").splitlines():
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
    ignored_lines: set[str],
) -> list[str]:
    _type_text_chunks(bridge, "RUN")
    bridge.type_text("\r", via_keybuf=True, timeout=15.0)
    return _drain_until_prompt(
        bridge,
        timeout=_BATCH_RUN_TIMEOUT,
        initial_lines=initial_lines,
        initial_cursor_row=initial_cursor_row,
        ignored_lines=ignored_lines,
    )


def _drain_screen(bridge: OpenMSXBridge, tracker: ScreenTracker, *, duration: float) -> None:
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        try:
            lines = bridge.get_screen(timeout=1.0)
            cursor_row = bridge.get_cursor_row()
        except TimeoutError:
            return
        _emit_events(tracker.update(lines, cursor_row))
        time.sleep(_POLL_INTERVAL)


def _wait_for_ok_prompt(bridge: OpenMSXBridge, *, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lines = bridge.get_screen(timeout=1.0)
            cursor_row = bridge.get_cursor_row()
        except TimeoutError:
            continue
        if _screen_ready_for_input(lines, cursor_row):
            return True
        time.sleep(_POLL_INTERVAL)
    return False


def _type_program_line(bridge: OpenMSXBridge, line: str) -> None:
    baseline_lines = bridge.get_screen(timeout=1.0)
    baseline_cursor_row = bridge.get_cursor_row()
    baseline_program_end = _get_program_end_pointer(bridge)
    _type_text_chunks(bridge, line)
    bridge.type_text("\r", via_keybuf=True, timeout=15.0)
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
        bridge.type_text(chunk, via_keybuf=True, timeout=15.0)
        time.sleep(_BATCH_INPUT_PAUSE)


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
            lines = bridge.get_screen(timeout=1.0)
            cursor_row = bridge.get_cursor_row()
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
) -> list[str]:
    deadline = None if timeout is None else time.monotonic() + timeout
    tracker = BatchOutputTracker(ignored_lines)
    last_lines = list(initial_lines)
    last_cursor_row = initial_cursor_row
    saw_prompt_departure = False
    while deadline is None or time.monotonic() < deadline:
        try:
            lines = bridge.get_screen(timeout=1.0)
            cursor_row = bridge.get_cursor_row()
        except TimeoutError:
            continue
        if not saw_prompt_departure:
            saw_prompt_departure = cursor_row != initial_cursor_row or lines != initial_lines
        if saw_prompt_departure:
            tracker.observe(lines, cursor_row)
            if _post_run_prompt_visible(lines, cursor_row):
                tracker.capture_window(lines, initial_cursor_row, cursor_row)
                run_row = _last_row_with_text(lines, cursor_row, "RUN")
                if run_row is not None:
                    tracker.capture_window(lines, run_row + 1, cursor_row)
                return tracker.finish(lines, cursor_row)
        last_lines = lines
        last_cursor_row = cursor_row
        time.sleep(_POLL_INTERVAL)
    return tracker.finish(last_lines, last_cursor_row)


def _print_batch_result(lines: list[str], loaded_lines: list[str]) -> None:
    source_lines = {line.strip() for line in loaded_lines}
    for line in lines:
        if line.strip() in source_lines:
            continue
        print(line)
