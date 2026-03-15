from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from z80_basic.cpm import (
    CPM_BDOS_VECTOR,
    CPM_COMMAND_TAIL,
    CPM_TPA_BASE,
    CPM_WBOOT_VECTOR,
    build_cpm_environment,
)


class BuildCpmEnvironmentTests(unittest.TestCase):
    def test_builds_page_zero_and_loads_program(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            disk_path = temp_path / "cpm22.dsk"
            program_path = temp_path / "MBASIC.COM"
            disk_path.write_bytes(bytes(256))
            program_path.write_bytes(b"\x3E\x41\xC9")

            environment = build_cpm_environment(
                cpm_image_path=disk_path,
                program_path=program_path,
                memory_size=0x10000,
            )

            self.assertEqual(environment.program_entry, CPM_TPA_BASE)
            self.assertEqual(environment.program_size, 3)
            self.assertEqual(environment.memory.slice(CPM_TPA_BASE, 3), b"\x3E\x41\xC9")
            self.assertEqual(environment.memory.read_byte(CPM_WBOOT_VECTOR), 0xC3)
            self.assertEqual(environment.memory.read_byte(CPM_BDOS_VECTOR), 0xC3)
            self.assertEqual(environment.memory.read_byte(CPM_COMMAND_TAIL), 0x00)
            self.assertEqual(environment.memory.read_byte(CPM_COMMAND_TAIL + 1), 0x0D)

    def test_rejects_program_too_large_for_tpa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            disk_path = temp_path / "cpm22.dsk"
            program_path = temp_path / "MBASIC.COM"
            disk_path.write_bytes(bytes(256))
            program_path.write_bytes(bytes(0x10000))

            with self.assertRaisesRegex(ValueError, "too large for the TPA"):
                build_cpm_environment(
                    cpm_image_path=disk_path,
                    program_path=program_path,
                    memory_size=0x10000,
                )
