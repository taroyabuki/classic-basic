from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from z80_basic.emulator import EmulatorConfig, Z80BasicMachine
from z80_basic.terminal import BufferedConsole


class ExecutionTests(unittest.TestCase):
    def test_runs_bdos_print_string_and_warm_boots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            disk_path = temp_path / "cpm22.dsk"
            program_path = temp_path / "HELLO.COM"
            disk_path.write_bytes(bytes(256))
            program_path.write_bytes(
                bytes(
                    [
                        0x11,
                        0x0B,
                        0x01,
                        0x0E,
                        0x09,
                        0xCD,
                        0x05,
                        0x00,
                        0xC3,
                        0x00,
                        0x00,
                    ]
                )
                + b"HELLO$"
            )

            machine = Z80BasicMachine(
                EmulatorConfig(cpm_image=disk_path, mbasic_path=program_path)
            )
            console = BufferedConsole()
            result = machine.run(console=console, max_steps=64)

            self.assertEqual(result.reason, "warm_boot")
            self.assertEqual(console.output_text, "HELLO")
