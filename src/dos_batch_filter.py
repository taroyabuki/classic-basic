"""Extract user-visible batch output from noisy dosemu/QBasic/GW-BASIC captures."""
from __future__ import annotations

import re
import sys

from mandelbrot_output import is_mandelbrot_art_line


_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_ESC_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\([0-9A-Za-z])")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_TRAILING_READY_NOISE_RE = re.compile(r"^(Ok)[\u00A0\u00FF\uFFFD\x00-\x1f\x7f ]+$")
_BASIC_SOURCE_RE = re.compile(
    r"^\d+\s+(?:"
    r"REM\b|IF\b|FOR\b|NEXT\b|PRINT\b|INPUT\b|LET\b|DIM\b|READ\b|DATA\b|"
    r"GOTO\b|GOSUB\b|RETURN\b|END\b|STOP\b|SYSTEM\b|CLS\b|COLOR\b|LOCATE\b|"
    r"SCREEN\b|OPEN\b|CLOSE\b|RUN\b|[A-Z][A-Z0-9$#%!]*\s*=)"
)
_NOISE_RE = re.compile(
    r"^(?:"
    r"Prepared (?:QBasic|GW-BASIC) runtime|"
    r"Prepared dosemu HOME|"
    r"(?:QBasic|GW-BASIC) executable:|"
    r"Running dosemu2 with root privs|"
    r"Note that DOS needs 25 lines|"
    r"window before starting dosemu|"
    r"dosemu2\b|"
    r"Get the latest code at|"
    r"Submit Bugs via|"
    r"Ask for help in mail list|"
    r"This program is free software:|"
    r"This program comes with|"
    r"This is free software|"
    r"FDPP kernel|"
    r"Kernel compatibility|"
    r"Written by|"
    r"Based on FreeDOS|"
    r"it under the terms of|"
    r"the Free Software Foundation|"
    r"\(at your option\)|"
    r"[C-F]: HD\d|"
    r"\d+ MB, size=\s+\d+ MB|"
    r"dosemu XMS|"
    r"dosemu EMS|"
    r"EMUFS host file|"
    r"dosemu CDROM|"
    r"Process 0 starting|"
    r"FreeDOS userspace|"
    r"BLASTER=|"
    r"MIDI=|"
    r"Welcome to dosemu2!?|"
    r"Build 2\.0pre|"
    r"ERROR:|"
    r"Your kernel is too old|"
    r"Failed to create secure directory|"
    r"Please specify your keyboard map|"
    r"File\s+Edit\s+View\s+Search\s+Run\s+Debug\s+Options|"
    r"File$"
    r")"
)


def extract_clean_lines(text: str) -> list[str]:
    """Return likely program output lines from a dosemu batch capture."""
    text = _OSC_RE.sub("\n", text)
    text = _CSI_RE.sub("\n", text)
    text = _ESC_RE.sub("\n", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL_RE.sub("", text)

    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.expandtabs(8).rstrip()
        if not line.strip():
            continue
        line = "".join("\uFFFD" if 0xDC80 <= ord(char) <= 0xDCFF else char for char in line)
        stripped = line.strip()
        ready_match = _TRAILING_READY_NOISE_RE.match(stripped)
        if ready_match is not None:
            line = ready_match.group(1)
            stripped = line
        if _should_skip(line, stripped=stripped):
            continue
        if is_mandelbrot_art_line(line):
            lines.append(line)
        else:
            lines.append(_WHITESPACE_RE.sub(" ", line).strip())
    return lines


def _should_skip(line: str, *, stripped: str | None = None) -> bool:
    stripped_line = line.strip() if stripped is None else stripped
    if _NOISE_RE.match(stripped_line):
        return True
    if "RUNFILE.BAS" in stripped_line or "Immediate" in stripped_line:
        return True
    if stripped_line == "Ok":  # GW-BASIC / MSX-BASIC ready prompt
        return True
    if any(ch in line for ch in "┌┐└┘├┤│─░↑↓←→"):
        return True
    if _BASIC_SOURCE_RE.match(stripped_line):
        return True
    return False


def main() -> int:
    for line in extract_clean_lines(sys.stdin.read()):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
