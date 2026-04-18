from __future__ import annotations

from collections import deque
from dataclass_compat import dataclass
from typing import Deque

from .display import TextGeometry, TextScreen


@dataclass(frozen=True, slots=True)
class RomRegion:
    name: str
    start: int
    data: bytes

    @property
    def end(self) -> int:
        return self.start + len(self.data)


class PC8001Memory:
    def __init__(
        self,
        *,
        size: int = 0x10000,
        vram_start: int = 0xF300,
        vram_stride: int | None = None,
        vram_cell_width: int = 1,
        geometry: TextGeometry | None = None,
    ) -> None:
        if size <= 0:
            raise ValueError("memory size must be positive")
        self.size = size
        self.geometry = geometry or TextGeometry()
        self.vram_start = vram_start
        self.vram_stride = vram_stride or self.geometry.cols
        self.vram_cell_width = vram_cell_width
        if self.vram_cell_width <= 0:
            raise ValueError("VRAM cell width must be positive")
        if self.vram_stride < self.geometry.cols:
            raise ValueError("VRAM stride must be at least as wide as the visible columns")
        if self.geometry.cols * self.vram_cell_width > self.vram_stride:
            raise ValueError("Visible columns exceed the VRAM stride for this cell width")
        self.vram_end = vram_start + self.geometry.rows * self.vram_stride
        if self.vram_end > size:
            raise ValueError("VRAM range exceeds memory size")

        self._ram = bytearray(size)
        self._rom_data = bytearray(size)
        self._rom_mask = bytearray(size)
        self._rom_regions: list[RomRegion] = []
        self.screen = TextScreen(self.geometry)
        self.current_pc = 0
        self.vram_log_limit = 0
        self.vram_log: Deque[str] = deque()
        self.vram_summary: dict[tuple[int, int], int] = {}

    def add_rom(self, *, name: str, start: int, data: bytes) -> None:
        end = start + len(data)
        if start < 0 or end > self.size:
            raise ValueError(f"ROM range out of bounds: {name} 0x{start:04X}-0x{end - 1:04X}")
        region_data = bytes(data)
        self._rom_regions.append(RomRegion(name=name, start=start, data=region_data))
        self._rom_data[start:end] = region_data
        self._rom_mask[start:end] = b"\x01" * len(region_data)

    def read_byte(self, address: int) -> int:
        if self._rom_mask[address]:
            return self._rom_data[address]
        return self._ram[address]

    def read_fetch_byte(self, address: int) -> int:
        if self._rom_mask[address]:
            return self._rom_data[address]
        return self._ram[address]

    def write_byte(self, address: int, value: int) -> None:
        if self._rom_mask[address]:
            return
        value &= 0xFF
        self._ram[address] = value
        if self.vram_start <= address < self.vram_end:
            self._record_vram_write(address, value)
            offset = address - self.vram_start
            row = offset // self.vram_stride
            col = offset % self.vram_stride
            if col % self.vram_cell_width == 0:
                visible_col = col // self.vram_cell_width
                if visible_col < self.geometry.cols:
                    self.screen.write_byte(row * self.geometry.cols + visible_col, value)

    def read_word(self, address: int) -> int:
        low = self._rom_data[address] if self._rom_mask[address] else self._ram[address]
        next_address = address + 1
        high = self._rom_data[next_address] if self._rom_mask[next_address] else self._ram[next_address]
        return low | (high << 8)

    def write_word(self, address: int, value: int) -> None:
        self.write_byte(address, value & 0xFF)
        self.write_byte(address + 1, (value >> 8) & 0xFF)

    def load(self, address: int, data: bytes) -> None:
        for offset, value in enumerate(data):
            self.write_byte(address + offset, value)

    def visible_address(self, row: int, col: int) -> int:
        if row < 0 or row >= self.geometry.rows:
            raise ValueError(f"row out of range: {row}")
        if col < 0 or col >= self.geometry.cols:
            raise ValueError(f"col out of range: {col}")
        return self.vram_start + row * self.vram_stride + col * self.vram_cell_width

    def set_current_pc(self, pc: int) -> None:
        self.current_pc = pc & 0xFFFF

    def set_vram_log_limit(self, limit: int) -> None:
        self.vram_log_limit = max(0, limit)
        while len(self.vram_log) > self.vram_log_limit:
            self.vram_log.popleft()

    def format_vram_log(self) -> str:
        if not self.vram_log:
            return "No VRAM activity recorded"
        return "\n".join(self.vram_log)

    def format_vram_summary(self, limit: int | None = None) -> str:
        if not self.vram_summary:
            return "No VRAM activity recorded"
        lines = []
        items = sorted(
            self.vram_summary.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1]),
        )
        if limit is not None:
            items = items[: max(0, limit)]
        for (pc, address), count in items:
            lines.append(f"{count:5d} VRAM 0x{address:04X} from 0x{pc:04X}")
        return "\n".join(lines)

    def slice(self, start: int, length: int) -> bytes:
        if length < 0:
            raise ValueError("length must be non-negative")
        if start < 0 or start + length > self.size:
            raise ValueError("slice exceeds memory size")
        return bytes(self.read_byte(start + offset) for offset in range(length))

    def _record_vram_write(self, address: int, value: int) -> None:
        key = (self.current_pc, address & 0xFFFF)
        self.vram_summary[key] = self.vram_summary.get(key, 0) + 1
        if self.vram_log_limit <= 0:
            return
        if len(self.vram_log) >= self.vram_log_limit:
            self.vram_log.popleft()
        self.vram_log.append(
            f"PC=0x{self.current_pc:04X} VRAM 0x{address & 0xFFFF:04X} 0x{value & 0xFF:02X}"
        )

    def _check_address(self, address: int) -> None:
        if not 0 <= address < self.size:
            raise ValueError(f"address out of range: 0x{address:04X}")

    def _check_range(self, address: int, length: int) -> None:
        if length < 0:
            raise ValueError("length must be non-negative")
        if address < 0 or address + length > self.size:
            raise ValueError(f"memory range out of bounds: 0x{address:04X}+{length}")
