from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import threading
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

    def test_main_returns_130_on_keyboard_interrupt(self) -> None:
        with mock.patch.object(fm7basic_cli, "run_interactive_session", side_effect=KeyboardInterrupt):
            result = fm7basic_cli.main(
                ["--mame-command", "mame", "--driver", "fm7", "--rompath", "/tmp/roms"]
            )

        self.assertEqual(result, 130)

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
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            with (
                mock.patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True),
                mock.patch.object(
                    fm7basic_cli,
                    "_launch_interactive_session",
                    return_value=(proc, temp_path, temp_path / "input.txt"),
                ),
                mock.patch.object(fm7basic_cli, "_forward_stdin_to_input_queue"),
                mock.patch.object(fm7basic_cli, "_shutdown_interactive_process", return_value=0) as shutdown_process,
            ):
                result = fm7basic_cli.run_interactive_session(
                    mame_command="mame",
                    rompath=Path("/tmp/roms"),
                    driver="fm7",
                    file_path=None,
                    extra_mame_args=[],
                )

        self.assertEqual(result, 0)
        shutdown_process.assert_called_once_with(proc, temp_dir=temp_path)

    def test_run_interactive_session_waits_for_ready_before_emitting_loaded_listing(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stdout = None
        proc.stderr = None
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            manager = mock.Mock()
            with (
                mock.patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True),
                mock.patch.object(
                    fm7basic_cli,
                    "_launch_interactive_session",
                    return_value=(proc, temp_path, temp_path / "input.txt"),
                ),
                mock.patch.object(fm7basic_cli, "_forward_stdin_to_input_queue"),
                mock.patch.object(fm7basic_cli, "_shutdown_interactive_process", return_value=0),
                mock.patch.object(fm7basic_cli, "_wait_for_gui_ready_output") as wait_ready,
                mock.patch.object(fm7basic_cli, "_emit_loaded_program_listing") as emit_listing,
            ):
                manager.attach_mock(wait_ready, "wait_ready")
                manager.attach_mock(emit_listing, "emit_listing")
                result = fm7basic_cli.run_interactive_session(
                    mame_command="mame",
                    rompath=Path("/tmp/roms"),
                    driver="fm7",
                    file_path=Path("demo.bas"),
                    extra_mame_args=[],
                )

        self.assertEqual(result, 0)
        self.assertEqual(manager.mock_calls[:2], [mock.call.wait_ready(mock.ANY), mock.call.emit_listing(Path("demo.bas"))])

    def test_run_interactive_session_returns_zero_on_eof(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stdout = None
        proc.stderr = None
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            with (
                mock.patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True),
                mock.patch.object(
                    fm7basic_cli,
                    "_launch_interactive_session",
                    return_value=(proc, temp_path, temp_path / "input.txt"),
                ),
                mock.patch.object(fm7basic_cli, "_forward_stdin_to_input_queue"),
                mock.patch.object(fm7basic_cli, "_shutdown_interactive_process", return_value=0) as shutdown_process,
            ):
                result = fm7basic_cli.run_interactive_session(
                    mame_command="mame",
                    rompath=Path("/tmp/roms"),
                    driver="fm7",
                    file_path=None,
                    extra_mame_args=[],
                )

        self.assertEqual(result, 0)
        shutdown_process.assert_called_once_with(proc, temp_dir=temp_path)

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
                mock.patch.object(fm7basic_cli, "_wait_for_headless_ready_output"),
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

    def test_run_headless_interactive_session_waits_for_ready_before_emitting_loaded_listing(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        proc.stdout = None
        proc.stderr = None

        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            output_path = temp_path / "screen.txt"
            output_path.write_text("READY\n", encoding="ascii")
            program_path = temp_path / "demo.bas"
            program_path.write_text("10 PRINT 1\n", encoding="ascii")
            manager = mock.Mock()
            with (
                mock.patch("sys.stdin", mock.Mock(isatty=mock.Mock(return_value=False))),
                mock.patch.object(
                    fbasic_batch,
                    "prepare_fm7_headless_interactive_session",
                    return_value=(temp_path, temp_path / "interactive.lua", temp_path / "input.txt", output_path),
                ),
                mock.patch.object(fbasic_batch, "launch_mame", return_value=proc),
                mock.patch.object(fm7basic_cli, "_copy_console_snapshot", return_value=mock.Mock()),
                mock.patch.object(fm7basic_cli, "_forward_stdin_to_input_queue", side_effect=KeyboardInterrupt),
                mock.patch.object(fm7basic_cli, "_wait_for_headless_ready_output") as wait_ready,
                mock.patch.object(fm7basic_cli, "_emit_loaded_program_listing") as emit_listing,
            ):
                manager.attach_mock(wait_ready, "wait_ready")
                manager.attach_mock(emit_listing, "emit_listing")
                result = fm7basic_cli.run_headless_interactive_session(
                    mame_command="mame",
                    rompath=temp_path,
                    driver="fm7",
                    file_path=program_path,
                    extra_mame_args=[],
                )

        self.assertEqual(result, 0)
        self.assertEqual(
            manager.mock_calls[:2],
            [mock.call.wait_ready(output_path), mock.call.emit_listing(program_path)],
        )

    def test_console_echo_filter_suppresses_tty_input_echo_once(self) -> None:
        echo_filter = fm7basic_cli._ConsoleEchoFilter(suppress_terminal_echo=True)
        echo_filter.record_input(b"PRINT 2+2\n")

        self.assertEqual(echo_filter.filter_lines(["      PRI", "      PRINT 2+2", "4", "READY"]), ["4", "READY"])
        self.assertEqual(echo_filter.filter_lines(["PRINT 2+2"]), ["PRINT 2+2"])

    def test_console_echo_filter_suppresses_repeated_numbered_line_until_prompt(self) -> None:
        echo_filter = fm7basic_cli._ConsoleEchoFilter(suppress_terminal_echo=True)
        echo_filter.record_input(b"10 a=10\n")

        self.assertEqual(echo_filter.filter_lines(["10 A=10"]), [])
        self.assertEqual(echo_filter.filter_lines(["10 A=10"]), [])
        self.assertEqual(echo_filter.filter_lines(["10 A=10", "READY"]), ["READY"])

    def test_copy_console_snapshot_does_not_reprint_program_listing_during_run(self) -> None:
        snapshots = iter(
            [
                ["Ready"],
                ["Ready", "10 A=1"],
                ["Ready", "10 A=1", "20 PRINT A+1"],
                ["Ready", "10 A=1", "20 PRINT A+1", "RUN"],
                ["10 A=1", "20 PRINT A+1", "RUN", " 2", "Ready"],
                ["10 A=1", "20 PRINT A+1", "RUN", " 2", "Ready"],
            ]
        )
        stop_event = threading.Event()
        stdout_sink = io.StringIO()
        echo_filter = fm7basic_cli._ConsoleEchoFilter(suppress_terminal_echo=True)
        echo_filter.record_input(b"10 a=1\n20 print a+1\nrun\n")

        def fake_read_text_capture_lines(_path: Path) -> list[str]:
            try:
                return next(snapshots)
            except StopIteration:
                return ["10 A=1", "20 PRINT A+1", "RUN", " 2", "Ready"]

        sleep_count = {"value": 0}

        def fake_sleep(_seconds: float) -> None:
            sleep_count["value"] += 1
            if sleep_count["value"] >= 5:
                stop_event.set()

        with (
            mock.patch.object(fbasic_batch, "_read_text_capture_lines", side_effect=fake_read_text_capture_lines),
            mock.patch("fm7basic_cli.time.sleep", side_effect=fake_sleep),
            mock.patch("sys.stdout", stdout_sink),
        ):
            thread = fm7basic_cli._copy_console_snapshot(Path("/tmp/fm7-screen.txt"), stop_event, echo_filter)
            thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(stdout_sink.getvalue(), "Ready\n 2\nReady\n")

    def test_console_echo_filter_keeps_echo_for_non_tty_input(self) -> None:
        echo_filter = fm7basic_cli._ConsoleEchoFilter(suppress_terminal_echo=False)
        echo_filter.record_input(b"PRINT 2+2\n")

        self.assertEqual(echo_filter.filter_lines(["PRINT 2+2", "4", "READY"]), ["PRINT 2+2", "4", "READY"])

    def test_console_echo_filter_suppresses_preloaded_file_echo_even_without_tty_echo_suppression(self) -> None:
        echo_filter = fm7basic_cli._ConsoleEchoFilter(suppress_terminal_echo=False)
        echo_filter.record_input(b'10 PRINT "COMMON SAMPLE"\n20 PRINT "INTEGER";2\n', suppress_echo=True)

        self.assertEqual(echo_filter.filter_lines(['       10 PRINT "C']), [])
        self.assertEqual(echo_filter.filter_lines(['       10 PRINT "COMMON SAMPLE"']), [])
        self.assertEqual(echo_filter.filter_lines(["20 PRIN"]), [])
        self.assertEqual(echo_filter.filter_lines(['20 PRINT "INTEGER";2', "Ready"]), ["Ready"])

    def test_emit_loaded_program_listing_prints_normalized_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "demo.bas"
            program.write_text('10 print "hello"\n20 end\n', encoding="ascii")
            stdout_sink = io.StringIO()

            with mock.patch("sys.stdout", stdout_sink):
                fm7basic_cli._emit_loaded_program_listing(program)

        self.assertEqual(stdout_sink.getvalue(), '10 print "hello"\n20 end\n')

    def test_copy_console_snapshot_suppresses_partial_preloaded_file_lines(self) -> None:
        snapshots = iter(
            [
                ["Ready"],
                ["Ready", '       10 PRINT "C'],
                ["Ready", '       10 PRINT "COMMON SAMPL'],
                ["Ready", '       10 PRINT "COMMON SAMPLE"'],
                ["Ready", '       10 PRINT "COMMON SAMPLE"', "20 PRIN"],
                ["Ready", '       10 PRINT "COMMON SAMPLE"', '20 PRINT "INTEGER";2'],
                ["Ready", '       10 PRINT "COMMON SAMPLE"', '20 PRINT "INTEGER";2'],
            ]
        )
        stop_event = threading.Event()
        stdout_sink = io.StringIO()
        echo_filter = fm7basic_cli._ConsoleEchoFilter(suppress_terminal_echo=False)
        echo_filter.record_input(
            b'10 PRINT "COMMON SAMPLE"\n20 PRINT "INTEGER";2\n',
            suppress_echo=True,
        )

        def fake_read_text_capture_lines(_path: Path) -> list[str]:
            try:
                return next(snapshots)
            except StopIteration:
                return ["Ready", '       10 PRINT "COMMON SAMPLE"', '20 PRINT "INTEGER";2']

        sleep_count = {"value": 0}

        def fake_sleep(_seconds: float) -> None:
            sleep_count["value"] += 1
            if sleep_count["value"] >= 6:
                stop_event.set()

        with (
            mock.patch.object(fbasic_batch, "_read_text_capture_lines", side_effect=fake_read_text_capture_lines),
            mock.patch("fm7basic_cli.time.sleep", side_effect=fake_sleep),
            mock.patch("sys.stdout", stdout_sink),
        ):
            thread = fm7basic_cli._copy_console_snapshot(Path("/tmp/fm7-screen.txt"), stop_event, echo_filter)
            thread.join(timeout=1.0)

        self.assertFalse(thread.is_alive())
        self.assertEqual(stdout_sink.getvalue(), "Ready\n")

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
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            with (
                mock.patch.dict("os.environ", {"DISPLAY": ":1"}, clear=True),
                mock.patch.object(
                    fm7basic_cli,
                    "_launch_interactive_session",
                    return_value=(proc, temp_path, temp_path / "input.txt"),
                ),
                mock.patch.object(
                    fm7basic_cli,
                    "_forward_stdin_to_input_queue",
                    side_effect=KeyboardInterrupt,
                ),
                mock.patch.object(fm7basic_cli, "_shutdown_interactive_process", return_value=0) as shutdown_process,
            ):
                result = fm7basic_cli.run_interactive_session(
                    mame_command="mame",
                    rompath=Path("/tmp/roms"),
                    driver="fm7",
                    file_path=None,
                    extra_mame_args=[],
                )

        self.assertEqual(result, 0)
        shutdown_process.assert_called_once_with(proc, temp_dir=temp_path)

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

    def test_terminate_process_kills_process_group_when_available(self) -> None:
        proc = mock.Mock()
        proc.pid = 4321
        proc.poll.return_value = None
        proc.wait.return_value = 0

        with (
            mock.patch("fm7basic_cli.os.getpgid", return_value=4321),
            mock.patch("fm7basic_cli.os.killpg") as killpg,
        ):
            result = fm7basic_cli._terminate_process(proc)

        self.assertEqual(result, 0)
        killpg.assert_called_once()
        proc.terminate.assert_not_called()

    def test_shutdown_interactive_process_requests_lua_exit_before_forcing_termination(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.return_value = 0

        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            with mock.patch.object(fm7basic_cli, "_terminate_process") as terminate_process:
                result = fm7basic_cli._shutdown_interactive_process(proc, temp_dir=temp_path)

            self.assertEqual(result, 0)
            self.assertTrue((temp_path / "exit.txt").exists())
            proc.wait.assert_called_once_with(timeout=1.0)
            terminate_process.assert_not_called()

    def test_shutdown_interactive_process_falls_back_to_terminate_after_timeout(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired(cmd="mame", timeout=1.0)

        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            with mock.patch.object(fm7basic_cli, "_terminate_process", return_value=0) as terminate_process:
                result = fm7basic_cli._shutdown_interactive_process(proc, temp_dir=temp_path)

            self.assertEqual(result, 0)
            self.assertTrue((temp_path / "exit.txt").exists())
            terminate_process.assert_called_once_with(proc)

    def test_fm7_batch_progress_tracks_run_across_scrolled_snapshots(self) -> None:
        progress = fbasic_batch._FM7BatchProgress()

        self.assertFalse(progress.observe(["READY"]))
        self.assertFalse(progress.observe(["CBATCHBEGIN"]))
        self.assertFalse(progress.observe(["RUN"]))
        self.assertFalse(progress.observe(["30 EQ", "READY"]))
        self.assertTrue(progress.observe(["RUN", "30 EQ", "READY"]))

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

    def test_startup_filter_drops_average_speed_after_passthrough(self) -> None:
        startup_filter = fm7basic_cli._StartupOutputFilter(fm7basic_cli._FM77AV_FILTERED_STARTUP_LINES)

        self.assertTrue(startup_filter.should_emit(b"FM-7 BASIC READY\n"))
        self.assertFalse(startup_filter.should_emit(b"Average speed: 718.78% (3 seconds)\n"))

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
