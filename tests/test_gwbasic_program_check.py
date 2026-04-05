from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

import gwbasic_program_check


class GwBasicProgramCheckTests(unittest.TestCase):
    def test_accepts_program_without_interactive_input(self) -> None:
        gwbasic_program_check.validate_program_text('10 PRINT "HELLO"\n20 END\n')

    def test_rejects_input_statement(self) -> None:
        with self.assertRaisesRegex(ValueError, "found INPUT on line 1"):
            gwbasic_program_check.validate_program_text('10 INPUT "N";A\n')

    def test_rejects_line_input_statement(self) -> None:
        with self.assertRaisesRegex(ValueError, "found LINE INPUT on line 2"):
            gwbasic_program_check.validate_program_text('10 PRINT "A"\n20 LINE INPUT A$\n')

    def test_rejects_input_dollar_and_inkey_dollar(self) -> None:
        with self.assertRaisesRegex(ValueError, "found INPUT\\$ on line 1"):
            gwbasic_program_check.validate_program_text("10 A$=INPUT$(1)\n")
        with self.assertRaisesRegex(ValueError, "found INKEY\\$ on line 1"):
            gwbasic_program_check.validate_program_text("10 A$=INKEY$\n")

    def test_ignores_strings_and_rem_comments(self) -> None:
        gwbasic_program_check.validate_program_text(
            '10 PRINT "INPUT INKEY$ LINE INPUT"\n20 REM INPUT "N";A\n30 PRINT "OK"\n'
        )


if __name__ == "__main__":
    unittest.main()
