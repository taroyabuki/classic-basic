from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from z80_basic import osi_basic
from z80_basic.osi_basic import OsiBasicMachine, SessionConsole, build_staged_input, load_basic_program
from z80_basic.terminal import BufferedConsole


class OsiBasicTests(unittest.TestCase):
    def test_batch_mode_does_not_open_raw_terminal_even_when_stdin_is_a_tty(self) -> None:
        class FakeMachine:
            def run(self, console, max_steps: int = 5_000_000):
                console.write_byte(ord("O"))
                console.write_byte(ord("K"))
                return osi_basic.OsiBasicResult(reason="eof", steps=1, pc=0)

        fake_stdin = io.TextIOWrapper(io.BytesIO(b""), encoding="ascii")
        writes: list[bytes] = []

        with patch.object(osi_basic, "OsiBasicMachine", return_value=FakeMachine()), \
             patch.object(osi_basic, "RawTerminal", side_effect=AssertionError("RawTerminal should not be used")), \
             patch.object(osi_basic.sys, "stdin", fake_stdin), \
             patch.object(osi_basic.os, "write", side_effect=lambda fd, data: writes.append(data) or len(data)):
            with patch.object(osi_basic.sys.stdin, "isatty", return_value=True):
                exit_code = osi_basic.main(["--exec", "PRINT 2+2"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(b"".join(writes), b"OK")

    def test_interactive_mode_requires_a_tty(self) -> None:
        fake_stdin = io.TextIOWrapper(io.BytesIO(b""), encoding="ascii")

        with patch.object(osi_basic.sys, "stdin", fake_stdin):
            with patch.object(osi_basic.sys.stdin, "isatty", return_value=False):
                with patch("sys.stderr", new_callable=io.StringIO) as stderr:
                    exit_code = osi_basic.main(["--interactive"])

        self.assertEqual(exit_code, 2)
        self.assertIn("--interactive requires a TTY", stderr.getvalue())

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

    def test_file_mode_loads_program_without_autorun_when_run_is_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            program_path = Path(tmp) / "demo.bas"
            program_path.write_text("10 PRINT 2+2\n20 END\n", encoding="ascii")

            staged_input = build_staged_input(program_path=program_path, exec_text=None, run=False)

        self.assertIn(b"10 PRINT 2+2\r20 END\r", staged_input)
        self.assertNotIn(b"RUN\r", staged_input)

    def test_session_console_normalizes_lowercase_letters_to_uppercase(self) -> None:
        console = SessionConsole(staged_input=b"print a\r")

        self.assertEqual(
            [console.read_byte() for _ in range(8)],
            [ord("P"), ord("R"), ord("I"), ord("N"), ord("T"), ord(" "), ord("A"), 0x0D],
        )

    def test_session_console_maps_backspace_and_del_to_osi_rubout(self) -> None:
        console = SessionConsole(staged_input=b"A\x08\x7f\r")

        self.assertEqual(
            [console.read_byte() for _ in range(4)],
            [ord("A"), 0x5F, 0x5F, 0x0D],
        )

    def test_interactive_backspace_erases_character_on_terminal(self) -> None:
        """When the user presses backspace/DEL in interactive mode, the ROM echoes
        the OSI rubout character (_) which is intercepted and converted to a
        backspace+space+backspace erase sequence on the terminal."""
        boot = build_staged_input(program_path=None, exec_text=None)

        written: list[int] = []

        class FakeTerminal:
            def write_byte(self, value: int) -> None:
                written.append(value)

        console = SessionConsole(staged_input=boot + b"A\x7f\r\x04", terminal=FakeTerminal())

        OsiBasicMachine().run(console=console, max_steps=300_000)

        written_bytes = bytes(written)
        self.assertIn(b"\x08 \x08", written_bytes)

    def test_load_basic_program_accepts_lf_crlf_and_cr_line_endings(self) -> None:
        cases = {
            "lf.bas": b"10 PRINT 1\n20 END\n",
            "crlf.bas": b"10 PRINT 1\r\n20 END\r\n",
            "cr.bas": b"10 PRINT 1\r20 END\r",
        }

        with tempfile.TemporaryDirectory() as tmp:
            temp_dir = Path(tmp)
            for name, source in cases.items():
                program_path = temp_dir / name
                program_path.write_bytes(source)

                with self.subTest(path=name):
                    self.assertEqual(load_basic_program(program_path), b"10 PRINT 1\r20 END\r")
