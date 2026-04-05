from __future__ import annotations

import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from fbasic_batch import (
    _emit_console_diff,
    _read_text_capture_lines,
    _run_mame_headful_capture,
    _build_fm7_interactive_lua,
    extract_output_lines,
    is_input_prompt_line,
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
            self.assertEqual(post_run_settle_frames(), 1800)
        with mock.patch.dict("os.environ", {"CLASSIC_BASIC_FM_BATCH_SETTLE_FRAMES": "4200"}, clear=False):
            self.assertEqual(post_run_settle_frames(), 4200)

    def test_post_run_settle_frames_rejects_negative_values(self) -> None:
        with mock.patch.dict("os.environ", {"CLASSIC_BASIC_FM_BATCH_SETTLE_FRAMES": "-1"}, clear=False):
            with self.assertRaisesRegex(ValueError, "non-negative"):
                post_run_settle_frames()

    def test_is_input_prompt_line_detects_basic_input_prompts(self) -> None:
        for text in ("INPUT", "?", "IN?", "INPUT A$", "INPUT B"):
            self.assertTrue(is_input_prompt_line(text), text)
        self.assertFalse(is_input_prompt_line("HELLO"))

    def test_read_text_capture_lines_skips_blank_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screen.txt"
            path.write_text("READY\n\nHELLO\n  \n", encoding="ascii")
            self.assertEqual(_read_text_capture_lines(path), ["READY", "HELLO"])

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

    def test_run_batch_reads_fm77av_output_from_console_ram_dump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            program_path = temp_path / "program.bas"
            program_path.write_text('10 PRINT "HELLO"\n20 END\n', encoding="ascii")

            def fake_run_mame(**kwargs: object) -> int:
                lua_text = Path(kwargs["lua_path"]).read_text(encoding="ascii")
                self.assertIn('local sub_program = manager.machine.devices[":sub"].spaces["program"]', lua_text)
                output_path = Path(kwargs["lua_path"]).with_name("screen.txt")
                output_path.write_text("RUN\nHELLO\nREADY\n", encoding="ascii")
                return 0

            with (
                mock.patch("fbasic_batch._run_mame", side_effect=fake_run_mame),
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
            print_mock.assert_called_once_with("HELLO")

    def test_build_fm7_interactive_lua_posts_single_enter_per_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            lua_path = temp_path / "interactive.lua"

            _build_fm7_interactive_lua(lua_path, input_path=temp_path / "input.txt")

            lua_text = lua_path.read_text(encoding="ascii")
            self.assertIn("pending_enter_count = 1", lua_text)
