#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r", "")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(f"usage: {argv[0]} EXPECTED ACTUAL", file=sys.stderr)
        return 2

    expected_path = Path(argv[1])
    actual_path = Path(argv[2])
    expected = _load_text(expected_path)
    actual = _load_text(actual_path)

    if expected == actual:
        print("MATCH")
        print(f"chars={len(expected)}")
        return 0

    limit = min(len(expected), len(actual))
    mismatch = next((i for i in range(limit) if expected[i] != actual[i]), limit)

    print("DIFF")
    print(f"expected_chars={len(expected)}")
    print(f"actual_chars={len(actual)}")
    print(f"first_mismatch_index={mismatch}")
    if mismatch < len(expected):
        print(f"expected_char={expected[mismatch]!r}")
    if mismatch < len(actual):
        print(f"actual_char={actual[mismatch]!r}")
    start = max(0, mismatch - 20)
    stop = mismatch + 20
    print(f"expected_context={expected[start:stop]!r}")
    print(f"actual_context={actual[start:stop]!r}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
