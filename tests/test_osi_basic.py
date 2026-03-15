from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from z80_basic.osi_basic import OsiBasicMachine, build_staged_input
from z80_basic.terminal import BufferedConsole


class OsiBasicTests(unittest.TestCase):
    def test_boots_and_runs_direct_mode_expression(self) -> None:
        machine = OsiBasicMachine()
        console = BufferedConsole(
            build_staged_input(program_path=None, exec_text="PRINT 2+2")
        )

        result = machine.run(console=console, max_steps=200_000)

        self.assertEqual(result.reason, "eof")
        self.assertIn("OSI 6502 BASIC VERSION 1.0 REV 3.2", console.output_text)
        self.assertIn("PRINT 2+2", console.output_text)
        self.assertIn(" 4 ", console.output_text)

    def test_types_program_and_runs_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            program_path = Path(tmp) / "demo.bas"
            program_path.write_text("10 PRINT 2+2\n20 END\n", encoding="ascii")

            machine = OsiBasicMachine()
            console = BufferedConsole(
                build_staged_input(program_path=program_path, exec_text=None)
            )

            result = machine.run(console=console, max_steps=300_000)

        self.assertEqual(result.reason, "eof")
        self.assertIn("RUN", console.output_text)
        self.assertIn(" 4 ", console.output_text)
