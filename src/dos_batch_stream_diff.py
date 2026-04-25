from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dos_batch_filter import extract_clean_lines


def _load_state(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"emitted": [], "pending": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"emitted": [], "pending": None}
    emitted = data.get("emitted")
    pending = data.get("pending")
    if not isinstance(emitted, list) or not all(isinstance(line, str) for line in emitted):
        emitted = []
    if pending is not None and not isinstance(pending, str):
        pending = None
    return {"emitted": emitted, "pending": pending}


def _save_state(path: Path, *, emitted: list[str], pending: str | None) -> None:
    path.write_text(
        json.dumps({"emitted": emitted, "pending": pending}, ensure_ascii=False),
        encoding="utf-8",
    )


def emit_stream_diff(source_path: Path, state_path: Path, *, final: bool = False) -> list[str]:
    raw = source_path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    filtered = extract_clean_lines(text)
    newline_terminated = not raw or raw.endswith((b"\n", b"\r"))

    if final or newline_terminated or not filtered:
        complete_lines = filtered
        pending_line = None
    else:
        complete_lines = filtered[:-1]
        pending_line = filtered[-1]

    state = _load_state(state_path)
    emitted_lines = list(state["emitted"])
    prefix = 0
    limit = min(len(emitted_lines), len(complete_lines))
    while prefix < limit and emitted_lines[prefix] == complete_lines[prefix]:
        prefix += 1

    new_lines = complete_lines[prefix:]
    _save_state(state_path, emitted=complete_lines, pending=pending_line)
    return new_lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m dos_batch_stream_diff",
        description="Emit newly completed lines from a growing DOS batch capture file.",
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("state", type=Path)
    parser.add_argument("--final", action="store_true", help="Flush the final unterminated line")
    args = parser.parse_args(argv)

    for line in emit_stream_diff(args.source, args.state, final=args.final):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
