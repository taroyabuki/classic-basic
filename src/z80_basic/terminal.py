from __future__ import annotations

import os
import select
import sys
import termios
import tty
from collections import deque
from typing import Deque


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

    def read_char(self) -> str:
        return chr(self.read_byte())

    def write_byte(self, value: int) -> None:
        os.write(sys.stdout.fileno(), bytes([value & 0xFF]))

    def write(self, text: str) -> None:
        os.write(sys.stdout.fileno(), text.encode("latin-1", errors="replace"))


class BufferedConsole:
    def __init__(self, input_bytes: bytes = b"") -> None:
        self._input: Deque[int] = deque(input_bytes)
        self._output = bytearray()

    def input_ready(self) -> bool:
        return bool(self._input)

    def read_byte(self) -> int:
        if self._input:
            return self._input.popleft()
        return 0x04

    def read_char(self) -> str:
        return chr(self.read_byte())

    def write_byte(self, value: int) -> None:
        self._output.append(value & 0xFF)

    def write(self, text: str) -> None:
        self._output.extend(text.encode("latin-1", errors="replace"))

    @property
    def output_bytes(self) -> bytes:
        return bytes(self._output)

    @property
    def output_text(self) -> str:
        return self.output_bytes.decode("latin-1")
