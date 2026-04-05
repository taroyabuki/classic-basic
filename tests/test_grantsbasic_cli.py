from __future__ import annotations

import io
import unittest
from pathlib import Path
from unittest.mock import patch

from grants_basic.cli import main
from grants_basic.machine import InputRequestError


class GrantsBasicCliTests(unittest.TestCase):
    def test_plain_launch_boots_and_enters_terminal(self) -> None:
        with patch("grants_basic.cli.GrantSearleMachine") as machine_cls:
            machine = machine_cls.return_value
            machine.run_terminal.return_value = 0

            result = main([])

        self.assertEqual(result, 0)
        machine.boot.assert_called_once_with()
        machine.load_program_file.assert_not_called()
        machine.run_program_file.assert_not_called()
        machine.run_terminal.assert_called_once_with()

    def test_file_launch_loads_program_before_terminal(self) -> None:
        with patch("grants_basic.cli.GrantSearleMachine") as machine_cls:
            machine = machine_cls.return_value
            machine.run_terminal.return_value = 0

            result = main(["--file", "demo.bas"])

        self.assertEqual(result, 0)
        machine.boot.assert_called_once_with()
        machine.load_program_file.assert_called_once_with(Path("demo.bas"))
        machine.run_program_file.assert_not_called()
        machine.run_terminal.assert_called_once_with()

    def test_run_file_writes_stdout_and_does_not_enter_terminal(self) -> None:
        stdout = io.StringIO()
        with patch("grants_basic.cli.GrantSearleMachine") as machine_cls, patch("sys.stdout", stdout):
            machine = machine_cls.return_value
            machine.run_program_file.return_value = "42\n"

            result = main(["--run", "--file", "demo.bas"])

        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "42\n")
        machine.boot.assert_called_once_with()
        machine.run_program_file.assert_called_once_with(Path("demo.bas"))
        machine.run_terminal.assert_not_called()

    def test_run_requires_file(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            main(["--run"])

        self.assertEqual(raised.exception.code, 2)

    def test_input_request_error_returns_exit_code_two(self) -> None:
        stderr = io.StringIO()
        with patch("grants_basic.cli.GrantSearleMachine") as machine_cls, patch("sys.stderr", stderr):
            machine = machine_cls.return_value
            machine.run_program_file.side_effect = InputRequestError("interactive input is unsupported")

            result = main(["--run", "--file", "demo.bas"])

        self.assertEqual(result, 2)
        self.assertIn("error: interactive input is unsupported", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
