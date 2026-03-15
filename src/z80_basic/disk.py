from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class DiskImage:
    path: Path
    data: bytes

    @classmethod
    def from_path(cls, path: Path) -> "DiskImage":
        return cls(path=path, data=path.read_bytes())

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sector_count(self) -> int:
        return self.size // 128

    def describe(self) -> str:
        return (
            f"{self.path} ({self.size} bytes, "
            f"{self.sector_count} x 128-byte sectors)"
        )
