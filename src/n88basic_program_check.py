from __future__ import annotations

import re
import sys
from pathlib import Path


UNSUPPORTED_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("LINE INPUT", re.compile(r"(?<![A-Z0-9_])LINE[ \t]+INPUT(?![A-Z0-9_$])")),
    ("INPUT$", re.compile(r"(?<![A-Z0-9_])INPUT\$(?![A-Z0-9_])")),
    ("INKEY$", re.compile(r"(?<![A-Z0-9_])INKEY\$(?![A-Z0-9_])")),
    ("INPUT", re.compile(r"(?<![A-Z0-9_])INPUT(?![A-Z0-9_$])")),
)


def _strip_strings_and_comments(line: str) -> str:
    chars: list[str] = []
    index = 0
    in_string = False
    upper_line = line.upper()

    while index < len(line):
        char = line[index]
        if in_string:
            if char == '"':
                if index + 1 < len(line) and line[index + 1] == '"':
                    index += 2
                    continue
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if upper_line.startswith("REM", index):
            prev_char = upper_line[index - 1] if index > 0 else " "
            next_index = index + 3
            next_char = upper_line[next_index] if next_index < len(upper_line) else " "
            if not (prev_char.isalnum() or prev_char == "_") and not (next_char.isalnum() or next_char in "_$"):
                break

        chars.append(upper_line[index])
        index += 1

    return "".join(chars)


def find_unsupported_input(source_text: str) -> tuple[int, str] | None:
    normalized = source_text.replace("\r\n", "\n").replace("\r", "\n")
    for line_number, raw_line in enumerate(normalized.split("\n"), start=1):
        code = _strip_strings_and_comments(raw_line)
        for label, pattern in UNSUPPORTED_PATTERNS:
            if pattern.search(code):
                return line_number, label
    return None


def validate_program_text(source_text: str) -> None:
    match = find_unsupported_input(source_text)
    if match is None:
        return
    line_number, keyword = match
    raise ValueError(
        f"program input is not supported in --run mode: found {keyword} on line {line_number}"
    )


def validate_program_path(path: Path) -> None:
    validate_program_text(path.read_text(encoding="ascii"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("error: usage: python3 -m n88basic_program_check PROGRAM.bas", file=sys.stderr)
        return 2
    try:
        validate_program_path(Path(args[0]))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
