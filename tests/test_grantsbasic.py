from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grants_basic.machine import GrantSearleConfig, GrantSearleMachine, InputRequestError
from grants_basic.z80 import ExecutionResult


class GrantsBasicRuntimeTests(unittest.TestCase):
    def test_toy_rom_writes_character(self) -> None:
        rom = bytearray(0x2000)
        rom[0:6] = bytes([0x3E, ord("A"), 0xD3, 0x81, 0x76, 0x00])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rom.bin"
            path.write_bytes(rom)
            machine = GrantSearleMachine(GrantSearleConfig(rom_path=path, max_steps=16))
            machine.load_rom()
            result = machine.run_slice(16)
            self.assertEqual(result.reason, "halted")
            self.assertEqual(machine.console_text(), "A")

    def test_send_file_accepts_common_newline_variants(self) -> None:
        cases = {
            "lf.bas": b"10 PRINT 1\n20 END\n",
            "crlf.bas": b"10 PRINT 1\r\n20 END\r\n",
            "cr.bas": b"10 PRINT 1\r20 END\r",
        }

        for name, source in cases.items():
            with self.subTest(path=name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / name
                path.write_bytes(source)
                machine = GrantSearleMachine(GrantSearleConfig(rom_path=Path(tmp) / "rom.bin"))
                sent: list[str] = []
                machine.send_text = sent.append  # type: ignore[method-assign]

                machine.send_file(path)

                self.assertEqual(sent, ["10 PRINT 1\r", "20 END\r"])

    def test_load_program_file_sends_source_without_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prog.bas"
            path.write_text("10 PRINT 1\n20 END\n", encoding="ascii")
            machine = GrantSearleMachine(GrantSearleConfig(rom_path=Path(tmp) / "rom.bin"))
            machine._booted = True
            sent: list[str] = []
            machine.send_text = sent.append  # type: ignore[method-assign]
            idle_calls: list[bool] = []
            machine._run_until_input_idle = lambda *, step_budget, require_activity: idle_calls.append(require_activity)  # type: ignore[method-assign]

            machine.load_program_file(path)

            self.assertEqual(sent, ["10 PRINT 1\r", "20 END\r"])
            self.assertEqual(idle_calls, [True, True])

    def test_run_program_file_sends_run_and_returns_console_text(self) -> None:
        machine = GrantSearleMachine(GrantSearleConfig(rom_path=Path("/tmp/rom.bin")))
        machine._booted = True
        machine._console = list("Z80 BASIC\nOk\n42\n")
        sent: list[str] = []
        machine.send_text = sent.append  # type: ignore[method-assign]
        machine.send_file = lambda path: sent.append(f"FILE:{path.name}")  # type: ignore[method-assign]
        machine._run_until_input_idle = lambda *, step_budget, require_activity: None  # type: ignore[method-assign]
        machine._waiting_for_serial_input = lambda: False  # type: ignore[method-assign]
        machine._at_prompt = lambda: True  # type: ignore[method-assign]

        result = machine.run_program_file(Path("/tmp/demo.bas"))

        self.assertEqual(result, "Z80 BASIC\nOk\n42\n")
        self.assertEqual(sent, ["FILE:demo.bas", "RUN\r"])

    def test_run_program_file_raises_for_interactive_input_request(self) -> None:
        machine = GrantSearleMachine(GrantSearleConfig(rom_path=Path("/tmp/rom.bin")))
        machine._booted = True
        machine.send_file = lambda path: None  # type: ignore[method-assign]
        machine.send_text = lambda text: None  # type: ignore[method-assign]
        machine._run_until_input_idle = lambda *, step_budget, require_activity: None  # type: ignore[method-assign]
        machine._waiting_for_serial_input = lambda: True  # type: ignore[method-assign]
        machine._at_prompt = lambda: False  # type: ignore[method-assign]

        with self.assertRaisesRegex(InputRequestError, "interactive input"):
            machine.run_program_file(Path("/tmp/input.bas"))

    def test_run_terminal_non_tty_flushes_console_text(self) -> None:
        class FakeStdout(io.StringIO):
            def __init__(self) -> None:
                super().__init__()
                self.flush_calls = 0

            def isatty(self) -> bool:
                return False

            def flush(self) -> None:
                self.flush_calls += 1

        machine = GrantSearleMachine(GrantSearleConfig(rom_path=Path("/tmp/rom.bin")))
        machine._booted = True
        machine._console = list("READY\n")
        fake_stdout = FakeStdout()

        with patch("sys.stdin.isatty", return_value=False), patch("sys.stdout", fake_stdout):
            result = machine.run_terminal()

        self.assertEqual(result, 0)
        self.assertEqual(fake_stdout.getvalue(), "READY\n")
        self.assertEqual(fake_stdout.flush_calls, 1)

    def test_run_terminal_exits_on_ctrl_d(self) -> None:
        machine = GrantSearleMachine(GrantSearleConfig(rom_path=Path("/tmp/rom.bin"), max_steps=1))
        machine._booted = True
        machine._console = list("Ok\nREADY\n")
        machine.run_slice = lambda max_steps: ExecutionResult(reason="step_limit", steps=max_steps, pc=0)  # type: ignore[method-assign]

        class FakeTerminal:
            def __init__(self) -> None:
                self.writes: list[str] = []

            def __enter__(self) -> "FakeTerminal":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def write(self, text: str) -> None:
                self.writes.append(text)

            def input_ready(self) -> bool:
                return True

            def read_byte(self) -> int:
                return 0x04

        terminal = FakeTerminal()
        with patch("sys.stdin.isatty", return_value=True), patch("sys.stdout.isatty", return_value=True), patch(
            "grants_basic.machine.RawTerminal",
            return_value=terminal,
        ):
            result = machine.run_terminal()

        self.assertEqual(result, 0)
        self.assertEqual(terminal.writes, ["Ok\r\nREADY\r\n", "\r\n"])


if __name__ == "__main__":
    unittest.main()
