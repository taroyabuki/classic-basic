from __future__ import annotations

import sys
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from fbasic_batch import (
    _FM7BatchProgress,
    _build_fm7_console_capture_lua,
    _extract_output_lines_from_candidate,
    _merge_cumulative_history,
    _emit_console_diff,
    _normalize_symmetric_mandelbrot_output,
    _read_fm7_capture_snapshots,
    _merge_output_history,
    _read_text_capture_lines,
    _run_mame_headful_capture,
    _wait_for_fm7_snapshot,
    _terminate_launched_process,
    _build_fm7_interactive_lua,
    extract_output_lines,
    is_input_prompt_line,
    launch_mame,
    normalize_program_lines,
    post_run_settle_frames,
    run_batch,
)


class FBasicBatchTests(unittest.TestCase):
    def test_extract_output_lines_uses_run_and_ready_boundaries(self) -> None:
        plain = ["RUN", "HELLO, WORLD", "INTEGER 14", "PI 3.14159", "Ready"]
        filtered = ["RUN", "HELLO, WORLD", "INTEGER 14", "PI 3.14159", "R"]
        self.assertEqual(
            extract_output_lines(plain, filtered),
            ["HELLO, WORLD", "INTEGER 14", "PI 3.14159"],
        )

    def test_extract_output_lines_prefers_batch_marker_and_filters_source_listing(self) -> None:
        plain = [
            'PRINT "CBATCHBEGIN"',
            "CBATCHBEGIN",
            "READY",
            "RUN",
            '100 PRINT "HELLO"',
            "HELLO",
            "READY",
        ]
        filtered = [
            'PRINT "CBATCHBEGIN"',
            "CBATCHBEGIN",
            "READU",
            "RUN",
            '100 PRINT "HELLO"',
            "HELLO",
            "READY",
        ]
        self.assertEqual(extract_output_lines(plain, filtered), ["HELLO"])

    def test_extract_fm7_non_art_output_allows_numeric_rows(self) -> None:
        snapshot = [
            "RUN",
            "BEST",
            " 194           104",
            " 33            162",
            " 218           15",
            " 73            130",
            "AGM 2",
            " 180           139",
            " 123           228",
            " 219           15",
            " 73            130",
            "DIFF 3.002137837215813D-07",
            "Ready",
        ]

        self.assertEqual(
            _extract_output_lines_from_candidate(snapshot),
            [
                "BEST",
                " 194           104",
                " 33            162",
                " 218           15",
                " 73            130",
                "AGM 2",
                " 180           139",
                " 123           228",
                " 219           15",
                " 73            130",
                "DIFF 3.002137837215813D-07",
            ],
        )

    def test_normalize_program_lines_requires_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "direct.bas"
            path.write_text('PRINT "X"\n', encoding="ascii")
            with self.assertRaisesRegex(ValueError, "line-numbered"):
                normalize_program_lines(path)

    def test_normalize_program_lines_accepts_lf_crlf_and_cr(self) -> None:
        expected = ['10 PRINT "X"', '20 PRINT "Y"']
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            for name, content in {
                "lf.bas": b'10 PRINT "X"\n20 PRINT "Y"\n',
                "crlf.bas": b'10 PRINT "X"\r\n20 PRINT "Y"\r\n',
                "cr.bas": b'10 PRINT "X"\r20 PRINT "Y"\r',
            }.items():
                path = temp_path / name
                path.write_bytes(content)
                self.assertEqual(normalize_program_lines(path), expected)

    def test_normalize_program_lines_rejects_non_ascii(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nonascii.bas"
            path.write_bytes("10 PRINT \"あ\"\n".encode("utf-8"))
            with self.assertRaises(UnicodeDecodeError):
                normalize_program_lines(path)

    def test_normalize_program_lines_rejects_empty_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty.bas"
            path.write_text("\r\n", encoding="ascii")
            with self.assertRaisesRegex(ValueError, "program is empty"):
                normalize_program_lines(path)

    def test_normalize_program_lines_preserves_numeric_literal_hash_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "probe.bas"
            path.write_text(
                '10 A#=16#*ATN(.2#)-4#*ATN(1#/239#):PRINT "1#":REM 2# stays here\n',
                encoding="ascii",
            )
            self.assertEqual(
                normalize_program_lines(path),
                ['10 A#=16#*ATN(.2#)-4#*ATN(1#/239#):PRINT "1#":REM 2# stays here'],
            )

    def test_post_run_settle_frames_defaults_and_allows_override(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertEqual(post_run_settle_frames("pc8801mk2"), 1800)
            self.assertEqual(post_run_settle_frames("fm77av"), 600)
        with mock.patch.dict("os.environ", {"CLASSIC_BASIC_FM_BATCH_SETTLE_FRAMES": "4200"}, clear=False):
            self.assertEqual(post_run_settle_frames("pc8801mk2"), 4200)
            self.assertEqual(post_run_settle_frames("fm77av"), 4200)

    def test_post_run_settle_frames_rejects_negative_values(self) -> None:
        with mock.patch.dict("os.environ", {"CLASSIC_BASIC_FM_BATCH_SETTLE_FRAMES": "-1"}, clear=False):
            with self.assertRaisesRegex(ValueError, "non-negative"):
                post_run_settle_frames("fm77av")

    def test_is_input_prompt_line_detects_basic_input_prompts(self) -> None:
        for text in ("INPUT", "?", "IN?", "INPUT A$", "INPUT B"):
            self.assertTrue(is_input_prompt_line(text), text)
        self.assertFalse(is_input_prompt_line("HELLO"))

    def test_read_text_capture_lines_skips_blank_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.txt"
            path.write_text("READY\n\nHELLO\n  \n", encoding="ascii")
            self.assertEqual(_read_text_capture_lines(path), ["READY", "HELLO"])

    def test_read_fm7_capture_snapshots_splits_marker_delimited_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.txt"
            path.write_text(
                "@@CLASSIC_BASIC_FM7_SNAPSHOT@@\nRUN\nHELLO\n@@CLASSIC_BASIC_FM7_SNAPSHOT_END@@\n"
                "@@CLASSIC_BASIC_FM7_SNAPSHOT@@\nRUN\nHELLO\nREADY\n@@CLASSIC_BASIC_FM7_SNAPSHOT_END@@\n",
                encoding="ascii",
            )

            self.assertEqual(
                _read_fm7_capture_snapshots(path),
                [["RUN", "HELLO"], ["RUN", "HELLO", "READY"]],
            )

    def test_wait_for_fm7_snapshot_returns_completed_snapshot_with_run_still_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.txt"
            path.write_text(
                "@@CLASSIC_BASIC_FM7_SNAPSHOT@@\n"
                'PRINT "CBATCHBEGIN"\n'
                "CBATCHBEGIN\n"
                "Ready\n"
                "RUN\n"
                "MATCH OK\n"
                "ATN GT\n"
                "Ready\n"
                "@@CLASSIC_BASIC_FM7_SNAPSHOT_END@@\n",
                encoding="ascii",
            )
            proc = mock.Mock()
            proc.poll.return_value = None

            output = _wait_for_fm7_snapshot(
                proc=proc,
                output_path=path,
                progress=_FM7BatchProgress(),
                settle_seconds=10.0,
                command=["mame", "fm77av"],
                timeout_seconds=60.0,
                deadline=time.monotonic() + 60.0,
                exit_error="failed",
            )

            self.assertEqual(output, ["MATCH OK", "ATN GT"])

    def test_wait_for_fm7_snapshot_returns_accumulated_scrolled_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.txt"
            path.write_text(
                "@@CLASSIC_BASIC_FM7_SNAPSHOT@@\n"
                "CBATCHBEGIN\n"
                "Ready\n"
                "RUN\n"
                "BEST\n"
                " 194           104\n"
                " 33            162\n"
                " 218           15\n"
                " 73            130\n"
                "TERMHIGH 7\n"
                "@@CLASSIC_BASIC_FM7_SNAPSHOT_END@@\n"
                "@@CLASSIC_BASIC_FM7_SNAPSHOT@@\n"
                "TERMHIGH 7\n"
                " 242           20\n"
                " 33            162\n"
                " 218           15\n"
                " 73            130\n"
                "-1.191047260817868D-12\n"
                "Ready\n"
                "@@CLASSIC_BASIC_FM7_SNAPSHOT_END@@\n",
                encoding="ascii",
            )
            proc = mock.Mock()
            proc.poll.return_value = None

            output = _wait_for_fm7_snapshot(
                proc=proc,
                output_path=path,
                progress=_FM7BatchProgress(),
                settle_seconds=10.0,
                command=["mame", "fm77av"],
                timeout_seconds=60.0,
                deadline=time.monotonic() + 60.0,
                exit_error="failed",
            )

            self.assertEqual(
                output,
                [
                    "BEST",
                    " 194           104",
                    " 33            162",
                    " 218           15",
                    " 73            130",
                    "TERMHIGH 7",
                    " 242           20",
                    " 33            162",
                    " 218           15",
                    " 73            130",
                    "-1.191047260817868D-12",
                ],
            )

    def test_emit_console_diff_returns_appended_lines(self) -> None:
        self.assertEqual(
            _emit_console_diff(["READY"], ["READY", "PRINT 2+2", "4", "READY"]),
            ["PRINT 2+2", "4", "READY"],
        )

    def test_emit_console_diff_falls_back_to_full_snapshot_on_rewrite(self) -> None:
        self.assertEqual(
            _emit_console_diff(["READY"], ["PRINT 2+2", "4", "READY"]),
            ["PRINT 2+2", "4", "READY"],
        )

    def test_emit_console_diff_uses_subsequence_match_for_screen_redraw(self) -> None:
        self.assertEqual(
            _emit_console_diff(
                ["READY", "PRINT 2+2", "4", "READY"],
                [
                    "FUJITSU F-BASIC Version 3.0",
                    "Copyright (C) 1981 by FUJITSU/MICROSOFT",
                    "30530 Bytes Free",
                    "READY",
                    "PRINT 2+2",
                    "4",
                    "READY",
                    "print 2-3",
                    "-1",
                    "READY",
                ],
            ),
            ["print 2-3", "-1", "READY"],
        )

    def test_emit_console_diff_returns_only_rewritten_last_line(self) -> None:
        self.assertEqual(
            _emit_console_diff(["READY", "10"], ["READY", "10 A=1"]),
            ["10 A=1"],
        )

    def test_emit_console_diff_uses_normalized_suffix_match_for_screen_redraw(self) -> None:
        self.assertEqual(
            _emit_console_diff(
                ["Ready", "10 a=1", "20 print a+1", "run"],
                [
                    "FUJITSU F-BASIC Version 3.0",
                    "Copyright (C) 1981 by FUJITSU/MICROSOFT",
                    "30530 Bytes Free",
                    "Ready",
                    "       10 a=1",
                    "20 print a+1",
                    " 2",
                    "Ready",
                ],
            ),
            [" 2", "Ready"],
        )

    def test_merge_output_history_appends_scrolling_tail_without_replaying_duplicates(self) -> None:
        history = [
            "L1",
            "L2",
            "L3",
            "L4",
        ]
        window = [
            "L3",
            "L4",
            "L5",
            "L6",
        ]

        self.assertEqual(_merge_output_history(history, window), ["L1", "L2", "L3", "L4", "L5", "L6"])

    def test_merge_output_history_replaces_with_fuller_subsequence_reconstruction(self) -> None:
        history = ["L1", "L3", "L4"]
        window = ["L1", "L2", "L3", "L4"]

        self.assertEqual(_merge_output_history(history, window), ["L1", "L2", "L3", "L4"])

    def test_merge_cumulative_history_keeps_existing_rows_when_window_shrinks(self) -> None:
        history = ["A1", "B1", "A2", "B2", "A3", "B3"]
        window = ["A2", "B2", "A3", "B3"]

        self.assertEqual(_merge_cumulative_history(history, window), history)

    def test_merge_cumulative_history_appends_only_new_tail_from_scrolling_window(self) -> None:
        history = ["A1", "B1", "A2", "B2", "A3", "B3"]
        window = ["A3", "B3", "A4", "B4"]

        self.assertEqual(
            _merge_cumulative_history(history, window),
            ["A1", "B1", "A2", "B2", "A3", "B3", "A4", "B4"],
        )

    def test_normalize_symmetric_mandelbrot_output_rebuilds_full_block_from_noisy_fm7_capture(self) -> None:
        expected = [
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
            "000001111111111111111122222222333557BF75433222211111000000000000000000000000000",
            "000111111111111111112222222233445C      643332222111110000000000000000000000000",
            "011111111111111111222222233444556C      654433332211111100000000000000000000000",
            "11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000",
            "111111111111122223333334469                 D   6322111111000000000000000000000",
            "1111111111222333333334457DB                    85332111111100000000000000000000",
            "11111122234B744444455556A                      96532211111110000000000000000000",
            "122222233347BAA7AB776679                         A32211111110000000000000000000",
            "2222233334567        9A                         A532221111111000000000000000000",
            "222333346679                                    9432221111111000000000000000000",
            "234445568  F                                   B5432221111111000000000000000000",
            "                                              864332221111111000000000000000000",
            "234445568  F                                   B5432221111111000000000000000000",
            "222333346679                                    9432221111111000000000000000000",
            "2222233334567        9A                         A532221111111000000000000000000",
            "122222233347BAA7AB776679                         A32211111110000000000000000000",
            "11111122234B744444455556A                      96532211111110000000000000000000",
            "1111111111222333333334457DB                    85332111111100000000000000000000",
            "111111111111122223333334469                 D   6322111111000000000000000000000",
            "11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000",
            "011111111111111111222222233444556C      654433332211111100000000000000000000000",
            "000111111111111111112222222233445C      643332222111110000000000000000000000000",
            "000001111111111111111122222222333557BF75433222211111000000000000000000000000000",
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
        ]
        noisy = [
            expected[0],
            expected[0],
            expected[0],
            expected[1],
            expected[0],
            expected[1],
            *expected[2:17],
            expected[0],
            expected[1],
            expected[2],
            expected[3],
            expected[4],
        ]

        self.assertEqual(_normalize_symmetric_mandelbrot_output(noisy), expected)

    def test_run_mame_headful_capture_raises_timeout_when_window_never_appears(self) -> None:
        mame_proc = mock.Mock()
        mame_proc.pid = 123
        mame_proc.poll.return_value = None
        mame_proc.wait.return_value = 0
        xvfb_proc = mock.Mock()
        xvfb_proc.wait.return_value = 0
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            with (
                mock.patch("fbasic_batch._pick_xvfb_display", return_value=":99"),
                mock.patch("fbasic_batch.subprocess.Popen", side_effect=[xvfb_proc, mame_proc]),
                mock.patch(
                    "fbasic_batch.subprocess.run",
                    return_value=mock.Mock(stdout="", returncode=1),
                ),
                mock.patch("fbasic_batch.time.sleep", return_value=None),
                mock.patch("fbasic_batch.time.monotonic", side_effect=[0.0, 0.0, 0.2]),
            ):
                with self.assertRaises(subprocess.TimeoutExpired):
                    _run_mame_headful_capture(
                        mame_command="mame",
                        rompath=temp_path,
                        driver="fm77av",
                        disk_path=None,
                        program_path=temp_path / "program.bas",
                        lua_path=temp_path / "capture.lua",
                        extra_mame_args=[],
                        image_path=temp_path / "screen.png",
                        timeout_seconds=0.1,
                    )

    def test_launch_mame_sets_parent_death_preexec_on_linux(self) -> None:
        fake_proc = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            with mock.patch("fbasic_batch.subprocess.Popen", return_value=fake_proc) as popen:
                result = launch_mame(
                    mame_command="mame",
                    rompath=temp_path,
                    driver="fm77av",
                    disk_path=None,
                    lua_path=temp_path / "interactive.lua",
                    extra_mame_args=[],
                    headless=True,
                )

        self.assertIs(result, fake_proc)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        if sys.platform.startswith("linux"):
            self.assertTrue(callable(popen.call_args.kwargs["preexec_fn"]))
        else:
            self.assertIsNone(popen.call_args.kwargs["preexec_fn"])

    def test_terminate_launched_process_uses_process_group_when_available(self) -> None:
        proc = mock.Mock()
        proc.pid = 4321
        proc.poll.return_value = None

        with mock.patch("fbasic_batch.os.killpg") as killpg:
            _terminate_launched_process(proc)

        killpg.assert_called_once_with(4321, signal.SIGTERM)
        proc.terminate.assert_not_called()
        proc.wait.assert_called_once_with(timeout=5.0)

    def test_fm7_batch_progress_accepts_ready_after_run_in_later_snapshot(self) -> None:
        progress = _FM7BatchProgress()

        self.assertFalse(progress.observe(["1220 NEXT N%", 'PRINT "CBATCHBEGIN"', "CBATCHBEGIN", "Ready"]))
        self.assertFalse(progress.observe(['PRINT "CBATCHBEGIN"', "CBATCHBEGIN", "Ready", "RUN"]))
        self.assertTrue(progress.observe(["FOUND 16", "Ready"]))

    def test_fm7_batch_progress_accepts_scrolled_numeric_output(self) -> None:
        progress = _FM7BatchProgress()

        self.assertFalse(progress.observe(["CBATCHBEGIN", "Ready"]))
        self.assertFalse(progress.observe(["RUN"]))
        self.assertTrue(
            progress.observe(
                [
                    " 242           20",
                    " 33            162",
                    "-1.191047260817868D-12",
                    "Ready",
                ]
            )
        )

    def test_extract_output_lines_keeps_numeric_output_without_marker(self) -> None:
        lines = [" 229", " 230", " 231"]

        self.assertEqual(extract_output_lines(lines, lines), lines)

    def test_extract_output_lines_keeps_fractional_numeric_output_after_run(self) -> None:
        lines = ["RUN", " 3 / 1         3", " 13 / 4        3.25"]

        self.assertEqual(extract_output_lines(lines, lines), [" 3 / 1         3", " 13 / 4        3.25"])

    def test_extract_output_lines_ignores_width_80_command_echo(self) -> None:
        lines = ["WIDTH 80", "RUN", "HELLO", "READY"]

        self.assertEqual(extract_output_lines(lines, lines), ["HELLO"])

    def test_extract_output_lines_ignores_short_line_numbered_source_fragments(self) -> None:
        lines = ["RUN", "30 EQ", "READY"]

        self.assertEqual(extract_output_lines(lines, lines), [])

    def test_extract_output_lines_reconstructs_wrapped_width_80_mandelbrot_rows(self) -> None:
        line = "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000"
        lines = ["RUN", line[:40], line[40:], "READY"]

        self.assertEqual(extract_output_lines(lines, lines), [line])

    def test_run_batch_reads_fm77av_output_from_headless_interactive_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            program_path = temp_path / "program.bas"
            program_path.write_text('10 PRINT "HELLO"\n20 END\n', encoding="ascii")
            queued_input: dict[str, str] = {}

            class FakeProc:
                def __init__(self) -> None:
                    self.returncode: int | None = None

                def poll(self) -> int | None:
                    return self.returncode

                def terminate(self) -> None:
                    self.returncode = 0

                def wait(self, timeout: float | None = None) -> int:
                    self.returncode = 0
                    return 0

                def kill(self) -> None:
                    self.returncode = -9

            def fake_launch_mame(**kwargs: object) -> FakeProc:
                lua_path = Path(kwargs["lua_path"])
                input_path = lua_path.with_name("input.txt")
                output_path = lua_path.with_name("screen.txt")
                proc = FakeProc()

                def writer() -> None:
                    output_path.write_text("READY\n", encoding="ascii")
                    while True:
                        current = input_path.read_text(encoding="ascii")
                        queued_input["text"] = current
                        if 'PRINT "CBATCHBEGIN"\nRUN\n' in current:
                            output_path.write_text("CBATCHBEGIN\nRUN\nHELLO\nREADY\n", encoding="ascii")
                            return
                        time.sleep(0.01)

                thread = threading.Thread(target=writer, daemon=True)
                thread.start()
                proc.thread = thread
                return proc

            with (
                mock.patch("fbasic_batch.launch_mame", side_effect=fake_launch_mame),
                mock.patch("builtins.print") as print_mock,
            ):
                exit_code = run_batch(
                    mame_command="mame",
                    rompath=temp_path,
                    driver="fm77av",
                    disk_path=None,
                    program_path=program_path,
                    timeout_seconds=5.0,
                    extra_mame_args=[],
                )

            self.assertEqual(exit_code, 0)
            print_mock.assert_called_once_with("HELLO", flush=True)
            self.assertIn('PRINT "CBATCHBEGIN"\nRUN\n', queued_input["text"])

    def test_run_batch_waits_for_post_run_ready_before_printing_fm77av_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            program_path = temp_path / "program.bas"
            program_path.write_text('10 PRINT "FOUND 16"\n20 END\n', encoding="ascii")

            class FakeProc:
                def __init__(self) -> None:
                    self.returncode: int | None = None

                def poll(self) -> int | None:
                    return self.returncode

                def terminate(self) -> None:
                    self.returncode = 0

                def wait(self, timeout: float | None = None) -> int:
                    self.returncode = 0
                    return 0

                def kill(self) -> None:
                    self.returncode = -9

            def fake_launch_mame(**kwargs: object) -> FakeProc:
                lua_path = Path(kwargs["lua_path"])
                input_path = lua_path.with_name("input.txt")
                output_path = lua_path.with_name("screen.txt")
                proc = FakeProc()

                def writer() -> None:
                    output_path.write_text("Ready\n", encoding="ascii")
                    while True:
                        current = input_path.read_text(encoding="ascii")
                        if 'PRINT "CBATCHBEGIN"\nRUN\n' not in current:
                            time.sleep(0.01)
                            continue
                        output_path.write_text('PRINT "CBATCHBEGIN"\nCBATCHBEGIN\nReady\nRUN\n', encoding="ascii")
                        time.sleep(0.35)
                        output_path.write_text(
                            'PRINT "CBATCHBEGIN"\nCBATCHBEGIN\nReady\nRUN\nFOUND 16\nReady\n',
                            encoding="ascii",
                        )
                        return

                thread = threading.Thread(target=writer, daemon=True)
                thread.start()
                proc.thread = thread
                return proc

            with (
                mock.patch("fbasic_batch.launch_mame", side_effect=fake_launch_mame),
                mock.patch("builtins.print") as print_mock,
            ):
                exit_code = run_batch(
                    mame_command="mame",
                    rompath=temp_path,
                    driver="fm77av",
                    disk_path=None,
                    program_path=program_path,
                    timeout_seconds=5.0,
                    extra_mame_args=[],
                )

            self.assertEqual(exit_code, 0)
            print_mock.assert_called_once_with("FOUND 16", flush=True)

    def test_run_batch_streams_fm77av_output_until_timeout_without_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            program_path = temp_path / "program.bas"
            program_path.write_text('10 PRINT "FOUND 16"\n20 GOTO 10\n', encoding="ascii")

            class FakeProc:
                def __init__(self) -> None:
                    self.returncode: int | None = None

                def poll(self) -> int | None:
                    return self.returncode

                def terminate(self) -> None:
                    self.returncode = 0

                def wait(self, timeout: float | None = None) -> int:
                    self.returncode = 0
                    return 0

                def kill(self) -> None:
                    self.returncode = -9

            def fake_launch_mame(**kwargs: object) -> FakeProc:
                lua_path = Path(kwargs["lua_path"])
                input_path = lua_path.with_name("input.txt")
                output_path = lua_path.with_name("screen.txt")
                proc = FakeProc()

                def writer() -> None:
                    output_path.write_text("Ready\n", encoding="ascii")
                    while True:
                        current = input_path.read_text(encoding="ascii")
                        if 'PRINT "CBATCHBEGIN"\nRUN\n' not in current:
                            time.sleep(0.01)
                            continue
                        output_path.write_text(
                            'PRINT "CBATCHBEGIN"\nCBATCHBEGIN\nReady\nRUN\nFOUND 16\n',
                            encoding="ascii",
                        )
                        return

                thread = threading.Thread(target=writer, daemon=True)
                thread.start()
                proc.thread = thread
                return proc

            with (
                mock.patch("fbasic_batch.launch_mame", side_effect=fake_launch_mame),
                mock.patch("builtins.print") as print_mock,
            ):
                with self.assertRaises(subprocess.TimeoutExpired):
                    run_batch(
                        mame_command="mame",
                        rompath=temp_path,
                        driver="fm77av",
                        disk_path=None,
                        program_path=program_path,
                        timeout_seconds=0.2,
                        extra_mame_args=[],
                    )

            print_mock.assert_called_once_with("FOUND 16", flush=True)

    def test_run_batch_waits_for_ready_across_long_fm77av_output_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            program_path = temp_path / "program.bas"
            program_path.write_text('10 PRINT "FOUND 16"\n20 END\n', encoding="ascii")

            class FakeProc:
                def __init__(self) -> None:
                    self.returncode: int | None = None

                def poll(self) -> int | None:
                    return self.returncode

                def terminate(self) -> None:
                    self.returncode = 0

                def wait(self, timeout: float | None = None) -> int:
                    self.returncode = 0
                    return 0

                def kill(self) -> None:
                    self.returncode = -9

            def fake_launch_mame(**kwargs: object) -> FakeProc:
                lua_path = Path(kwargs["lua_path"])
                input_path = lua_path.with_name("input.txt")
                output_path = lua_path.with_name("screen.txt")
                proc = FakeProc()

                def writer() -> None:
                    output_path.write_text("Ready\n", encoding="ascii")
                    while True:
                        current = input_path.read_text(encoding="ascii")
                        if 'PRINT "CBATCHBEGIN"\nRUN\n' not in current:
                            time.sleep(0.01)
                            continue
                        output_path.write_text(
                            'PRINT "CBATCHBEGIN"\nCBATCHBEGIN\nReady\nRUN\n355 / 113     3.141592920353982\n',
                            encoding="ascii",
                        )
                        time.sleep(2.2)
                        output_path.write_text(
                            "PRINT \"CBATCHBEGIN\"\nCBATCHBEGIN\nReady\nRUN\n"
                            "355 / 113     3.141592920353982\n"
                            "52163 / 16604 3.141592387376536\n"
                            "Ready\n",
                            encoding="ascii",
                        )
                        return

                thread = threading.Thread(target=writer, daemon=True)
                thread.start()
                proc.thread = thread
                return proc

            with (
                mock.patch("fbasic_batch.launch_mame", side_effect=fake_launch_mame),
                mock.patch("builtins.print") as print_mock,
            ):
                exit_code = run_batch(
                    mame_command="mame",
                    rompath=temp_path,
                    driver="fm77av",
                    disk_path=None,
                    program_path=program_path,
                    timeout_seconds=5.0,
                    extra_mame_args=[],
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                print_mock.mock_calls,
                [
                    mock.call("355 / 113     3.141592920353982", flush=True),
                    mock.call("52163 / 16604 3.141592387376536", flush=True),
                ],
            )

    def test_run_batch_ignores_transient_fm77av_redraw_fragments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            program_path = temp_path / "program.bas"
            program_path.write_text('10 PRINT "X"\n20 END\n', encoding="ascii")

            class FakeProc:
                def __init__(self) -> None:
                    self.returncode: int | None = None

                def poll(self) -> int | None:
                    return self.returncode

                def terminate(self) -> None:
                    self.returncode = 0

                def wait(self, timeout: float | None = None) -> int:
                    self.returncode = 0
                    return 0

                def kill(self) -> None:
                    self.returncode = -9

            def fake_launch_mame(**kwargs: object) -> FakeProc:
                lua_path = Path(kwargs["lua_path"])
                input_path = lua_path.with_name("input.txt")
                output_path = lua_path.with_name("screen.txt")
                proc = FakeProc()

                def writer() -> None:
                    output_path.write_text("Ready\n", encoding="ascii")
                    while True:
                        current = input_path.read_text(encoding="ascii")
                        if 'PRINT "CBATCHBEGIN"\nRUN\n' not in current:
                            time.sleep(0.01)
                            continue
                        output_path.write_text(
                            '2\n230\n23\n24\n240  NEX\n250 RE\nPRI\nPRINT "CB\nPRINT "CBATCHB\n',
                            encoding="ascii",
                        )
                        time.sleep(0.2)
                        output_path.write_text(
                            'PRINT "CBATCHBEGIN"\nCBATCHBEGIN\nReady\nRUN\nX\nReady\n',
                            encoding="ascii",
                        )
                        return

                thread = threading.Thread(target=writer, daemon=True)
                thread.start()
                proc.thread = thread
                return proc

            with (
                mock.patch("fbasic_batch.launch_mame", side_effect=fake_launch_mame),
                mock.patch("builtins.print") as print_mock,
            ):
                exit_code = run_batch(
                    mame_command="mame",
                    rompath=temp_path,
                    driver="fm77av",
                    disk_path=None,
                    program_path=program_path,
                    timeout_seconds=5.0,
                    extra_mame_args=[],
                )

            self.assertEqual(exit_code, 0)
            print_mock.assert_called_once_with("X", flush=True)

    def test_build_fm7_interactive_lua_posts_single_enter_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            lua_path = temp_path / "interactive.lua"

            _build_fm7_interactive_lua(
                lua_path,
                input_path=temp_path / "input.txt",
                exit_path=temp_path / "exit.txt",
            )

            lua_text = lua_path.read_text(encoding="ascii")
            self.assertIn("local frame = 0", lua_text)
            self.assertIn("frame = frame + 1", lua_text)
            self.assertIn("if frame < 240 then", lua_text)
            self.assertNotIn("os.time()", lua_text)
            self.assertIn("pending_enter_count = 1", lua_text)
            self.assertIn('local exit_path = "', lua_text)
            self.assertIn('manager.machine:exit()', lua_text)

    def test_build_fm7_interactive_lua_writes_changed_snapshots_after_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            lua_path = temp_path / "interactive.lua"

            _build_fm7_interactive_lua(
                lua_path,
                input_path=temp_path / "input.txt",
                output_path=temp_path / "screen.txt",
            )

            lua_text = lua_path.read_text(encoding="ascii")
            self.assertIn("local previous_snapshot = nil", lua_text)
            self.assertIn("if serialized == previous_snapshot then", lua_text)
            self.assertIn('handle:write("@@CLASSIC_BASIC_FM7_SNAPSHOT@@\\n")', lua_text)
            self.assertIn('handle:write("@@CLASSIC_BASIC_FM7_SNAPSHOT_END@@\\n")', lua_text)

    def test_build_fm7_console_capture_lua_writes_changed_snapshots_after_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            lua_path = temp_path / "capture.lua"

            _build_fm7_console_capture_lua(
                lua_path,
                driver="fm77av",
                program_line_count=2,
                console_output_path=temp_path / "screen.txt",
            )

            lua_text = lua_path.read_text(encoding="ascii")
            self.assertIn("local last_written_snapshot = nil", lua_text)
            self.assertIn("if serialized ~= last_written_snapshot then", lua_text)
            self.assertIn('f:write("@@CLASSIC_BASIC_FM7_SNAPSHOT@@\\n")', lua_text)
            self.assertIn('f:write("@@CLASSIC_BASIC_FM7_SNAPSHOT_END@@\\n")', lua_text)
