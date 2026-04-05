from __future__ import annotations


class Memory:
    def __init__(self, size: int) -> None:
        if size <= 0:
            raise ValueError("memory size must be positive")
        self.size = size
        self._data = bytearray(size)

    def _check_address(self, address: int) -> None:
        if not 0 <= address < self.size:
            raise ValueError(f"address out of range: 0x{address:04X}")

    def _check_range(self, address: int, length: int) -> None:
        if length < 0:
            raise ValueError("length must be non-negative")
        if address < 0 or address + length > self.size:
            raise ValueError(
                f"memory range out of bounds: 0x{address:04X}+{length}"
            )

    def read_byte(self, address: int) -> int:
        self._check_address(address)
        return self._data[address]

    def read_fetch_byte(self, address: int) -> int:
        """Read a byte for instruction fetch.

        Most memory implementations do not distinguish instruction fetches from
        data reads, so the default behavior is identical to ``read_byte``.
        Banked machines can override this to expose ROM on fetch while still
        returning banked RAM for ordinary data reads.
        """
        return self.read_byte(address)

    def write_byte(self, address: int, value: int) -> None:
        self._check_address(address)
        self._data[address] = value & 0xFF

    def read_word(self, address: int) -> int:
        self._check_range(address, 2)
        low = self._data[address]
        high = self._data[address + 1]
        return low | (high << 8)

    def write_word(self, address: int, value: int) -> None:
        self._check_range(address, 2)
        self._data[address] = value & 0xFF
        self._data[address + 1] = (value >> 8) & 0xFF

    def load(self, address: int, data: bytes) -> None:
        self._check_range(address, len(data))
        self._data[address : address + len(data)] = data

    def slice(self, address: int, length: int) -> bytes:
        self._check_range(address, length)
        return bytes(self._data[address : address + length])
