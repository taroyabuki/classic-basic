from __future__ import annotations

import pathlib
import re
import sys


_LINE_RE = re.compile(r"^(\d+)(?:\s+(.*))?$")
_ONLY_END_RE = re.compile(r"(?i)^\s*(END|STOP)\s*$")
_COLON_END_RE = re.compile(r"(?i)^(.*?:)\s*(END|STOP)\s*$")
_MAX_LINE_NUMBER = 65529


def rewrite_program_for_system_exit(lines: list[str]) -> list[str]:
    if not lines:
        raise ValueError("program is empty")

    parsed: list[tuple[int, str | None]] = []
    for raw_line in lines:
        match = _LINE_RE.match(raw_line.rstrip())
        if match is None:
            raise ValueError(f"line-numbered BASIC required; got: {raw_line!r}")
        parsed.append((int(match.group(1)), match.group(2)))

    last_number, last_body = parsed[-1]
    body = (last_body or "").rstrip()
    replacement = _rewrite_last_body(body)
    if replacement is not None:
        return [*lines[:-1], f"{last_number} {replacement}".rstrip()]

    appended_number = last_number + 1
    if appended_number > _MAX_LINE_NUMBER:
        raise ValueError("cannot append SYSTEM line after maximum BASIC line number")
    return [*lines, f"{appended_number} SYSTEM"]


def _rewrite_last_body(body: str) -> str | None:
    if not body:
        return "SYSTEM"
    if _ONLY_END_RE.match(body):
        return "SYSTEM"
    match = _COLON_END_RE.match(body)
    if match is not None:
        return f"{match.group(1)} SYSTEM"
    return None


def rewrite_file(source_path: pathlib.Path, destination_path: pathlib.Path) -> None:
    text = source_path.read_text(encoding="ascii")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line for line in text.split("\n") if line]
    rewritten = rewrite_program_for_system_exit(lines)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    destination_path.write_bytes(("\r\n".join(rewritten) + "\r\n").encode("ascii"))


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        print("usage: basic_batch_exit.py SOURCE.bas DEST.bas", file=sys.stderr)
        return 2
    rewrite_file(pathlib.Path(args[0]), pathlib.Path(args[1]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
