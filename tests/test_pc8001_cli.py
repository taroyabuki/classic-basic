from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from pc8001_terminal.cli import main
from pc8001_terminal.machine import InputRequestError


class PC8001CliTests(unittest.TestCase):
    def test_plain_launch_runs_terminal_without_startup_program(self) -> None:
        with patch("pc8001_terminal.cli.PC8001Machine") as machine_cls:
            machine = machine_cls.return_value
            machine.run_terminal.return_value = 0

            result = main([])

        self.assertEqual(result, 0)
        config = machine_cls.call_args.args[0]
        self.assertIsNone(config.startup_program)
        self.assertTrue(config.run_startup)
        machine.run_terminal.assert_called_once_with()

    def test_interactive_program_load_disables_auto_run(self) -> None:
        with patch("pc8001_terminal.cli.PC8001Machine") as machine_cls:
            machine = machine_cls.return_value
            machine.run_terminal.return_value = 0

            result = main(["--interactive", "demo.bas"])

        self.assertEqual(result, 0)
        config = machine_cls.call_args.args[0]
        self.assertEqual(str(config.startup_program), "demo.bas")
        self.assertFalse(config.run_startup)
        machine.run_terminal.assert_called_once_with()

    def test_program_argument_runs_loaded_program_in_batch_mode(self) -> None:
        with patch("pc8001_terminal.cli.PC8001Machine") as machine_cls:
            machine = machine_cls.return_value
            machine.run_terminal.return_value = 0

            result = main(["demo.bas"])

        self.assertEqual(result, 0)
        config = machine_cls.call_args.args[0]
        self.assertEqual(str(config.startup_program), "demo.bas")
        self.assertTrue(config.run_startup)
        machine.run_terminal.assert_called_once_with()

    def test_input_request_error_returns_exit_code_two(self) -> None:
        stderr = io.StringIO()
        with patch("pc8001_terminal.cli.PC8001Machine") as machine_cls, patch("sys.stderr", stderr):
            machine = machine_cls.return_value
            machine.run_terminal.side_effect = InputRequestError("interactive program input is not supported")

            result = main(["demo.bas"])

        self.assertEqual(result, 2)
        self.assertIn("error: interactive program input is not supported", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
