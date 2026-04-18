from __future__ import annotations

from dataclass_compat import dataclass


@dataclass(frozen=True, slots=True)
class TextGeometry:
    rows: int = 20
    cols: int = 80

    @property
    def size_bytes(self) -> int:
        return self.rows * self.cols


class TextScreen:
    def __init__(self, geometry: TextGeometry) -> None:
        self.geometry = geometry
        self._cells = bytearray(b" " * geometry.size_bytes)
        self._dirty = True

    def write_byte(self, offset: int, value: int) -> None:
        if not 0 <= offset < len(self._cells):
            raise ValueError(f"screen offset out of range: {offset}")
        value &= 0xFF
        if self._cells[offset] != value:
            self._cells[offset] = value
            self._dirty = True

    def write_text(self, row: int, col: int, text: str) -> None:
        if row < 0 or row >= self.geometry.rows:
            raise ValueError(f"row out of range: {row}")
        if col < 0 or col >= self.geometry.cols:
            raise ValueError(f"col out of range: {col}")

        offset = row * self.geometry.cols + col
        for char in text:
            if offset >= len(self._cells):
                break
            self.write_byte(offset, ord(char))
            offset += 1

    def clear(self) -> None:
        self._cells[:] = b" " * len(self._cells)
        self._dirty = True

    def render_lines(self) -> list[str]:
        lines: list[str] = []
        cols = self.geometry.cols
        for row in range(self.geometry.rows):
            chunk = self._cells[row * cols : (row + 1) * cols]
            lines.append("".join(_render_byte(value) for value in chunk).rstrip())
        return lines

    def consume_dirty(self) -> bool:
        dirty = self._dirty
        self._dirty = False
        return dirty


def _render_byte(value: int) -> str:
    if 0x20 <= value <= 0x7E:
        return chr(value)
    return " "
