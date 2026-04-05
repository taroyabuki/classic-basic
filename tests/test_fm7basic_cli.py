from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

import fm7basic_cli
import fbasic_batch
from fbasic_batch import UnsupportedProgramInputError


class Fm7BasicCliTests(unittest.TestCase):
    def test_main_without_file_starts_interactive_session(self) -> None:
        with mock.patch.object(fm7basic_cli, "run_interactive_session", return_value=0) as run_interactive:
            result = fm7basic_cli.main(
                ["--mame-command", "mame", "--driver", "fm7", "--rompath", "/tmp/roms"]
            )

        self.assertEqual(result, 0)
        run_interactive.assert_called_once()
        self.assertIsNone(run_interactive.call_args.kwargs["file_path"])

    def test_main_with_file_starts_loaded_interactive_session(self) -> None:
        with mock.patch.object(fm7basic_cli, "run_interactive_session", return_value=0) as run_interactive:
            result = fm7basic_cli.main(
                ["--mame-command", "mame", "--driver", "fm7", "--rompath", "/tmp/roms", "--file", "demo.bas"]
            )

        self.assertEqual(result, 0)
        self.assertEqual(run_interactive.call_args.kwargs["file_path"], Path("demo.bas"))

    def test_main_run_mode_skips_interactive_loop(self) -> None:
        with mock.patch("fbasic_batch.run_batch", return_value=0) as run_batch, mock.patch.object(
            fm7basic_cli, "run_interactive_session"
        ) as run_interactive:
            result = fm7basic_cli.main(
                [
                    "--mame-command",
                    "mame",
                    "--driver",
                    "fm7",
                    "--rompath",
                    "/tmp/roms",
                    "--run",
                    "--file",
                    "demo.bas",
                ]
            )

        self.assertEqual(result, 0)
        run_batch.assert_called_once()
        run_interactive.assert_not_called()

    def test_main_converts_expected_errors_to_exit_code_2(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(fm7basic_cli, "run_interactive_session", side_effect=UnsupportedProgramInputError("no input")), mock.patch(
            "sys.stderr", stderr
        ):
            result = fm7basic_cli.main(
                ["--mame-command", "mame", "--driver", "fm7", "--rompath", "/tmp/roms"]
            )

        self.assertEqual(result, 2)
        self.assertIn("error: no input", stderr.getvalue())

    def test_run_interactive_session_uses_headless_path_without_display(self) -> None:
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch.object(fm7basic_cli, "run_headless_interactive_session", return_value=0) as run_headless,
        ):
            result = fm7basic_cli.run_interactive_session(
                mame_command="mame",
                rompath=Path("/tmp/roms"),
                driver="fm77av",
                file_path=None,
                extra_mame_args=[],
            )

        self.assertEqual(result, 0)
        run_headless.assert_called_once()

    def test_run_interactive_session_uses_gui_path_with_display(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stdout = None
        proc.stderr = None
        with (
            mock.patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True),
            mock.patch.object(
                fm7basic_cli,
                "_launch_interactive_session",
                return_value=(proc, None, Path("/tmp/input.txt")),
            ),
            mock.patch.object(fm7basic_cli, "_forward_stdin_to_input_queue"),
        ):
            result = fm7basic_cli.run_interactive_session(
                mame_command="mame",
                rompath=Path("/tmp/roms"),
                driver="fm7",
                file_path=None,
                extra_mame_args=[],
            )

        self.assertEqual(result, 0)
        proc.terminate.assert_called_once()

    def test_run_interactive_session_returns_zero_on_eof(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stdout = None
        proc.stderr = None
        with (
            mock.patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True),
            mock.patch.object(
                fm7basic_cli,
                "_launch_interactive_session",
                return_value=(proc, None, Path("/tmp/input.txt")),
            ),
            mock.patch.object(fm7basic_cli, "_forward_stdin_to_input_queue"),
        ):
            result = fm7basic_cli.run_interactive_session(
                mame_command="mame",
                rompath=Path("/tmp/roms"),
                driver="fm7",
                file_path=None,
                extra_mame_args=[],
            )

        self.assertEqual(result, 0)
        proc.terminate.assert_called_once()

    def test_run_headless_interactive_session_copies_console_snapshot(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stdout = None
        proc.stderr = None
        stdout_sink = io.StringIO()
        stdout_proxy = mock.Mock(buffer=io.BytesIO())
        stdout_proxy.write = stdout_sink.write
        stdout_proxy.flush = stdout_sink.flush
        sleep_calls = {"count": 0}

        def fake_sleep(_seconds: float) -> None:
            sleep_calls["count"] += 1
            if sleep_calls["count"] == 1:
                output_path.write_text("READY\nPRINT 2+2\n4\nREADY\n", encoding="ascii")
            else:
                raise AssertionError("unexpected extra sleep call")

        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            output_path = temp_path / "screen.txt"
            output_path.write_text("READY\n", encoding="ascii")
            with (
                mock.patch("sys.stdin", mock.Mock(isatty=mock.Mock(return_value=False))),
                mock.patch.object(
                    fbasic_batch,
                    "prepare_fm7_headless_interactive_session",
                    return_value=(temp_path, temp_path / "interactive.lua", temp_path / "input.txt", output_path),
                ),
                mock.patch.object(fbasic_batch, "launch_mame", return_value=proc),
                mock.patch.object(fm7basic_cli, "_forward_stdin_to_input_queue", side_effect=KeyboardInterrupt),
                mock.patch("fm7basic_cli.time.sleep", side_effect=fake_sleep),
                mock.patch("sys.stdout", stdout_proxy),
            ):
                result = fm7basic_cli.run_headless_interactive_session(
                    mame_command="mame",
                    rompath=temp_path,
                    driver="fm7",
                    file_path=None,
                    extra_mame_args=[],
                )

        self.assertEqual(result, 0)
        self.assertEqual(stdout_sink.getvalue(), "READY\nPRINT 2+2\n4\nREADY\n")

    def test_console_echo_filter_suppresses_tty_input_echo_once(self) -> None:
        echo_filter = fm7basic_cli._ConsoleEchoFilter(suppress_terminal_echo=True)
        echo_filter.record_input(b"PRINT 2+2\n")

        self.assertEqual(echo_filter.filter_lines(["      PRI", "      PRINT 2+2", "4", "READY"]), ["4", "READY"])
        self.assertEqual(echo_filter.filter_lines(["PRINT 2+2"]), ["PRINT 2+2"])

    def test_console_echo_filter_keeps_echo_for_non_tty_input(self) -> None:
        echo_filter = fm7basic_cli._ConsoleEchoFilter(suppress_terminal_echo=False)
        echo_filter.record_input(b"PRINT 2+2\n")

        self.assertEqual(echo_filter.filter_lines(["PRINT 2+2", "4", "READY"]), ["PRINT 2+2", "4", "READY"])

    def test_snapshot_looks_stable_requires_ready_prompt(self) -> None:
        self.assertTrue(fm7basic_cli._snapshot_looks_stable(["READY"]))
        self.assertFalse(fm7basic_cli._snapshot_looks_stable(["P"]))
        self.assertFalse(fm7basic_cli._snapshot_looks_stable([]))

    def test_run_interactive_session_returns_zero_on_keyboard_interrupt(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stdout = None
        proc.stderr = None
        with (
            mock.patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True),
            mock.patch.object(
                fm7basic_cli,
                "_launch_interactive_session",
                return_value=(proc, None, Path("/tmp/input.txt")),
            ),
            mock.patch.object(
                fm7basic_cli,
                "_forward_stdin_to_input_queue",
                side_effect=KeyboardInterrupt,
            ),
        ):
            result = fm7basic_cli.run_interactive_session(
                mame_command="mame",
                rompath=Path("/tmp/roms"),
                driver="fm7",
                file_path=None,
                extra_mame_args=[],
            )

        self.assertEqual(result, 0)
        proc.terminate.assert_called_once()

    def test_forward_stdin_to_input_queue_appends_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.txt"
            fm7basic_cli._append_input_bytes(input_path, b"PRINT 2+2\r\nRUN\n")

            self.assertEqual(input_path.read_bytes(), b"PRINT 2+2\nRUN\n")

    def test_stream_stdin_to_input_queue_stops_on_pipe_eof(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.txt"
            fake_stdin = mock.Mock(fileno=mock.Mock(return_value=11))
            with (
                mock.patch("sys.stdin", fake_stdin),
                mock.patch("os.isatty", return_value=False),
                mock.patch("select.select", side_effect=[([11], [], []), ([11], [], [])]),
                mock.patch("os.read", side_effect=[b"PRINT 2+2\r\nRUN\n", b""]),
            ):
                fm7basic_cli._stream_stdin_to_input_queue(input_path)

            self.assertEqual(input_path.read_bytes(), b"PRINT 2+2\nRUN\n")

    def test_stream_stdin_to_input_queue_stops_on_ctrl_d_in_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.txt"
            fake_stdin = mock.Mock(fileno=mock.Mock(return_value=12))
            with (
                mock.patch("sys.stdin", fake_stdin),
                mock.patch("select.select", side_effect=[([12], [], []), ([12], [], [])]),
                mock.patch("os.read", side_effect=[b"PRINT 2+2\n", b""]),
            ):
                fm7basic_cli._stream_stdin_to_input_queue(input_path)

            self.assertEqual(input_path.read_bytes(), b"PRINT 2+2\n")

    def test_stream_stdin_to_input_queue_treats_ctrl_d_byte_as_eof_for_non_tty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.txt"
            fake_stdin = mock.Mock(fileno=mock.Mock(return_value=13))
            with (
                mock.patch("sys.stdin", fake_stdin),
                mock.patch("select.select", return_value=([13], [], [])),
                mock.patch("os.read", return_value=b"PRINT 2+2\x04RUN\n"),
            ):
                fm7basic_cli._stream_stdin_to_input_queue(input_path)

            self.assertEqual(input_path.read_bytes(), b"PRINT 2+2")

    def test_startup_filter_drops_known_fm77av_redump_variants(self) -> None:
        startup_filter = fm7basic_cli._StartupOutputFilter(fm7basic_cli._FM77AV_FILTERED_STARTUP_LINES)

        known_lines = [
            b"Warning: -video none doesn't make much sense without -seconds_to_run\n",
            b"fm77av      : fbasic30.rom (31744 bytes) - NEEDS REDUMP\n",
            b"fm77av      : subsys_c.rom (10240 bytes) - NEEDS REDUMP\n",
            b"fbasic30.rom ROM NEEDS REDUMP\n",
            b"subsys_c.rom ROM NEEDS REDUMP\n",
            b"romset fm77av [fm7] is best available\n",
            b"1 romsets found, 1 were OK.\n",
            b"WARNING: the machine might not run correctly.\n",
        ]
        for line in known_lines:
            self.assertFalse(startup_filter.should_emit(line))

        self.assertTrue(startup_filter.should_emit(b"FM-7 BASIC READY\n"))
        self.assertTrue(startup_filter.should_emit(known_lines[0]))

    def test_startup_filter_switches_to_passthrough_after_unknown_line(self) -> None:
        startup_filter = fm7basic_cli._StartupOutputFilter(fm7basic_cli._FM77AV_FILTERED_STARTUP_LINES)

        self.assertFalse(startup_filter.should_emit(b"fm77av      : fbasic30.rom (31744 bytes) - NEEDS REDUMP\n"))
        self.assertTrue(startup_filter.should_emit(b"unknown warning\n"))
        self.assertTrue(startup_filter.should_emit(b"romset fm77av [fm7] is best available\n"))

    def test_launch_interactive_session_filters_known_fm77av_startup_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            proc = mock.Mock()
            proc.stdout = io.BytesIO(
                b"Warning: -video none doesn't make much sense without -seconds_to_run\n"
                b"fbasic30.rom ROM NEEDS REDUMP\n"
                b"subsys_c.rom ROM NEEDS REDUMP\n"
                b"WARNING: the machine might not run correctly.\n"
                b"unknown warning\n"
            )
            proc.stderr = io.BytesIO(b"stderr line\n")
            stdout_sink = io.BytesIO()
            stderr_sink = io.BytesIO()

            with mock.patch("fbasic_batch.prepare_fm7_interactive_session", return_value=(temp_path, temp_path / "interactive.lua", temp_path / "input.txt")), mock.patch(
                "fbasic_batch.launch_mame",
                return_value=proc,
            ), mock.patch.object(
                sys, "stdout", mock.Mock(buffer=stdout_sink)
            ), mock.patch.object(sys, "stderr", mock.Mock(buffer=stderr_sink)):
                launched_proc, temp_dir, input_path = fm7basic_cli._launch_interactive_session(
                    mame_command="mame",
                    rompath=temp_path,
                    driver="fm77av",
                    file_path=None,
                    extra_mame_args=[],
                )
                for thread in getattr(launched_proc, "_classic_basic_io_threads", ()):
                    thread.join(timeout=1.0)

        self.assertIs(launched_proc, proc)
        self.assertEqual(temp_dir, temp_path)
        self.assertEqual(input_path, temp_path / "input.txt")
        self.assertEqual(stdout_sink.getvalue(), b"unknown warning\n")
        self.assertEqual(stderr_sink.getvalue(), b"stderr line\n")

    def test_main_rejects_timeout_without_run(self) -> None:
        stderr = io.StringIO()
        with mock.patch("sys.stderr", stderr):
            result = fm7basic_cli.main(
                ["--mame-command", "mame", "--driver", "fm7", "--rompath", "/tmp/roms", "--timeout", "5"]
            )

        self.assertEqual(result, 2)
        self.assertIn("--timeout requires --run --file PROGRAM.bas", stderr.getvalue())
