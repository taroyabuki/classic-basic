from __future__ import annotations

import unittest

from z80_basic.memory import Memory


class MemoryTests(unittest.TestCase):
    def test_reads_and_writes_bytes_and_words(self) -> None:
        memory = Memory(16)
        memory.write_byte(1, 0x12)
        memory.write_word(2, 0x3456)

        self.assertEqual(memory.read_byte(1), 0x12)
        self.assertEqual(memory.read_word(2), 0x3456)

    def test_range_checks_are_enforced(self) -> None:
        memory = Memory(4)

        with self.assertRaisesRegex(ValueError, "address out of range"):
            memory.write_byte(4, 0xFF)

        with self.assertRaisesRegex(ValueError, "memory range out of bounds"):
            memory.load(3, b"\x00\x01")
