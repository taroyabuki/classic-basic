from __future__ import annotations

import os
import select
import sys
import termios
import tty


class RawTerminal:
    def __init__(self) -> None:
        self._fd = sys.stdin.fileno()
        self._saved: list[int] | None = None
        self._isatty = os.isatty(self._fd)

    def __enter__(self) -> "RawTerminal":
        if self._isatty:
            self._saved = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._isatty and self._saved is not None:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    def input_ready(self) -> bool:
        readable, _, _ = select.select([self._fd], [], [], 0.0)
        return bool(readable)

    def read_byte(self) -> int:
        data = os.read(self._fd, 1)
        if not data:
            return 0x04
        return data[0]

    def write(self, text: str) -> None:
        os.write(sys.stdout.fileno(), text.encode("latin-1", errors="replace"))
