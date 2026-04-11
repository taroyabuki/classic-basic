from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

import fm11basic
from fm11basic import preprocess_fm11_source


class FM11BasicPreprocessTests(unittest.TestCase):
    def test_preprocess_splits_long_colon_lines_and_rewrites_targets(self) -> None:
        source = "\n".join(
            [
                "10 DEFDBL A-Z",
                '20 B=3.1415926535897930#:PRINT "30 ";',
                '21 IF B=A THEN PRINT "EQ":GOTO 30',
                '22 IF B<A THEN PRINT "LT":GOTO 30',
                '23 PRINT "GT"',
                "30 END",
            ]
        ) + "\n"

        processed = preprocess_fm11_source(source, max_body_length=24).splitlines()

        self.assertEqual(
            processed,
            [
                "10 DEFDBL A-Z",
                "20 B=3.1415926535897930#",
                '30 PRINT "30 ";',
                '40 IF B=A THEN PRINT "EQ"',
                "50 IF B=A THEN GOTO 90",
                '60 IF B<A THEN PRINT "LT"',
                "70 IF B<A THEN GOTO 90",
                '80 PRINT "GT"',
                "90 END",
            ],
        )

    def test_preprocess_keeps_trailing_end_conditional(self) -> None:
        source = '10 IF X=1 THEN PRINT "A":END\n20 END\n'

        processed = preprocess_fm11_source(source, max_body_length=18).splitlines()

        self.assertEqual(
            processed,
            [
                '10 IF X=1 THEN PRINT "A"',
                "20 IF X=1 THEN END",
                "30 END",
            ],
        )

    def test_preprocess_keeps_trailing_return_conditional(self) -> None:
        source = "10 IF N%=4 THEN A=292:RETURN\n20 END\n"

        processed = preprocess_fm11_source(source, max_body_length=18).splitlines()

        self.assertEqual(
            processed,
            [
                "10 IF N%=4 THEN A=292",
                "20 IF N%=4 THEN RETURN",
                "30 END",
            ],
        )

    def test_preprocess_splits_long_if_then_print_statement(self) -> None:
        source = '10 IF Y<>T THEN PRINT "N15MISS"\n20 END\n'

        processed = preprocess_fm11_source(source, max_body_length=24).splitlines()

        self.assertEqual(
            processed,
            [
                "10 IF Y<>T THEN GOTO 30",
                "20 GOTO 40",
                '30 PRINT "N15MISS"',
                "40 END",
            ],
        )

    def test_preprocess_shortens_long_if_comparison_with_temp_variables(self) -> None:
        source = (
            "10 DEFDBL A-Z\n"
            "20 IF PEEK(VARPTR(X)+J%)<>PEEK(VARPTR(T)+J%) THEN F=0\n"
            "30 END\n"
        )

        processed = preprocess_fm11_source(source, max_body_length=24).splitlines()

        self.assertEqual(
            processed,
            [
                "10 DEFDBL A-Z",
                "20 Z#=PEEK(VARPTR(X)+J%)",
                "30 Y#=PEEK(VARPTR(T)+J%)",
                "40 IF Z#<>Y# THEN F=0",
                "50 END",
            ],
        )
        self.assertTrue(all(len(line.split(" ", 1)[1]) <= 24 for line in processed))

    def test_preprocess_rejects_generated_line_that_is_still_too_long(self) -> None:
        source = "10 IF THISISAVERYLONGVARIABLE<>T THEN F=0\n20 END\n"

        with self.assertRaisesRegex(fm11basic.BasicRuntimeError, "too long"):
            preprocess_fm11_source(source, max_body_length=24)


class FM11BasicRuntimeTests(unittest.TestCase):
    def test_filter_new_lines_keeps_ready_prompt_after_command_output(self) -> None:
        self.assertEqual(
            fm11basic.filter_new_lines([" 20", "Ready"], "PRINT 10*2\n"),
            [" 20", "Ready"],
        )

    def test_try_fast_startup_ready_sends_three_standard_answers(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "How many 1MB disk drives",
            "How many 320KB disk drives",
            "How many disk files",
            "Ready",
        ]

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            screen = fm11basic._try_fast_startup_ready(session)

        self.assertEqual(screen, "Ready")
        self.assertEqual(session.key.call_count, 3)
        session.key.assert_has_calls([mock.call("2", "Return", deadline=None)] * 3)

    def test_try_fast_startup_ready_stops_sending_answers_once_ready_is_visible(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "How many 1MB disk drives",
            "How many 320KB disk drives",
            "Ready",
        ]

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            screen = fm11basic._try_fast_startup_ready(session)

        self.assertEqual(screen, "Ready")
        self.assertEqual(session.key.call_count, 2)

    def test_handle_startup_prompts_resumes_from_visible_later_prompt(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "How many 320KB disk drives",
            "How many disk files",
            "Ready",
        ]
        session.wait_for_text.return_value = "How many disk files"

        with (
            mock.patch("fm11basic._try_fast_startup_ready", return_value=None),
            mock.patch("fm11basic._sleep_with_deadline", return_value=None),
            mock.patch("fm11basic.wait_for_ready", return_value="Ready"),
        ):
            screen = fm11basic.handle_startup_prompts(session)

        self.assertEqual(screen, "Ready")
        self.assertEqual(session.key.call_count, 2)
        session.wait_for_text.assert_not_called()

    def test_handle_startup_prompts_uses_short_answer_delay(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "How many 1MB disk drives",
            "How many 320KB disk drives",
            "How many disk files",
            "Ready",
        ]

        with (
            mock.patch("fm11basic._try_fast_startup_ready", return_value=None),
            mock.patch("fm11basic._sleep_with_deadline", return_value=None) as sleep_with_deadline,
            mock.patch("fm11basic.wait_for_ready", return_value="Ready"),
        ):
            screen = fm11basic.handle_startup_prompts(session)

        self.assertEqual(screen, "Ready")
        self.assertTrue(
            any(call.args and call.args[0] == fm11basic.FAST_STARTUP_ANSWER_DELAY for call in sleep_with_deadline.call_args_list)
        )

    def test_load_source_batched_groups_waits_by_three_lines(self) -> None:
        session = mock.Mock()
        source_lines = [f"{index * 10} PRINT {index}" for index in range(1, 8)]

        with (
            mock.patch("fm11basic._sleep_with_deadline", return_value=None),
            mock.patch("fm11basic.wait_for_ready", side_effect=["Ready"] * 3) as wait_for_ready,
        ):
            raw_screen, screen_lines = fm11basic._load_source_batched(session, source_lines, "Ready")

        self.assertEqual(raw_screen, "Ready")
        self.assertEqual(screen_lines, ["Ready"])
        self.assertEqual(wait_for_ready.call_count, 3)
        self.assertEqual(session.paste_text.call_count, 7)

    def test_load_source_batched_requests_fallback_on_syntax_error(self) -> None:
        session = mock.Mock()

        with mock.patch("fm11basic.wait_for_ready", return_value="Syntax Error In 20\nReady"):
            with self.assertRaises(fm11basic.FastLoadFallbackRequired):
                fm11basic._load_source_batched(session, ["10 PRINT 1"], "Ready")

    def test_load_source_conservative_syncs_every_line_for_large_run_programs(self) -> None:
        session = mock.Mock()
        source_lines = [f"{index * 10} PRINT {index}" for index in range(1, 14)]

        with (
            mock.patch("fm11basic._sleep_with_deadline", return_value=None),
            mock.patch("fm11basic.wait_for_ready", side_effect=["Ready"] * len(source_lines)) as wait_for_ready,
        ):
            raw_screen, screen_lines = fm11basic._load_source_conservative(
                session,
                source_lines,
                "Ready",
                optimize_for_run=True,
            )

        self.assertEqual(raw_screen, "Ready")
        self.assertEqual(screen_lines, ["Ready"])
        self.assertEqual(wait_for_ready.call_count, len(source_lines))
        self.assertEqual(session.paste_text.call_count, 13)

    def test_load_source_conservative_accepts_loaded_line_when_ready_scrolls_offscreen(self) -> None:
        session = mock.Mock()
        source_lines = [f"{index * 10} PRINT {index}" for index in range(1, 14)]
        offscreen_screen = "\n".join(source_lines[-3:])

        with (
            mock.patch("fm11basic._sleep_with_deadline", return_value=None),
            mock.patch("fm11basic.wait_for_ready", side_effect=["Ready"] * 12 + [fm11basic.BasicTimeoutError("timed out")]),
            mock.patch.object(session, "copy_screen", return_value=offscreen_screen),
        ):
            raw_screen, screen_lines = fm11basic._load_source_conservative(
                session,
                source_lines,
                "Ready",
                optimize_for_run=True,
            )

        self.assertEqual(raw_screen, offscreen_screen)
        self.assertEqual(screen_lines, source_lines[-3:])

    def test_copy_screen_caches_window_geometry(self) -> None:
        session = fm11basic.RuntimeSession()
        session.display = ":99"
        session.window_id = "window-1"
        calls: list[list[str]] = []

        def fake_run_command(args, **kwargs):
            del kwargs
            calls.append(list(args))
            if args[:2] == ["xdotool", "getwindowgeometry"]:
                return SimpleNamespace(stdout="X=10\nY=20\nWIDTH=100\nHEIGHT=80\n")
            if args[:2] == ["xclip", "-selection"] and args[-1] == "-o":
                return SimpleNamespace(stdout="Ready")
            return SimpleNamespace(stdout="")

        try:
            with (
                mock.patch("fm11basic.run_command", side_effect=fake_run_command),
                mock.patch("fm11basic._sleep_with_deadline", return_value=None),
            ):
                first = session.copy_screen()
                second = session.copy_screen()
        finally:
            session.close()

        self.assertEqual(first, "Ready")
        self.assertEqual(second, "Ready")
        geometry_calls = [args for args in calls if args[:2] == ["xdotool", "getwindowgeometry"]]
        self.assertEqual(len(geometry_calls), 1)

    def test_run_basic_retries_with_conservative_loader_after_fast_loader_failure(self) -> None:
        args = SimpleNamespace(file="demo.bas", run=True, timeout="20")

        with (
            mock.patch("fm11basic.require_tool"),
            mock.patch("fm11basic.bootstrap_assets"),
            mock.patch("fm11basic.read_basic_file", return_value='10 PRINT "HELLO"\n'),
            mock.patch(
                "fm11basic._run_basic_once",
                side_effect=[fm11basic.FastLoadFallbackRequired("Syntax Error In 20"), 0],
            ) as run_once,
        ):
            result = fm11basic.run_basic(args)

        self.assertEqual(result, 0)
        self.assertEqual(run_once.call_count, 2)
        self.assertTrue(run_once.call_args_list[0].kwargs["use_fast_loader"])
        self.assertFalse(run_once.call_args_list[1].kwargs["use_fast_loader"])

    def test_run_basic_uses_conservative_loader_for_large_sources(self) -> None:
        args = SimpleNamespace(file="demo.bas", run=True, timeout="20")
        source_text = "".join(f"{index * 10} PRINT {index}\n" for index in range(1, 15))

        with (
            mock.patch("fm11basic.require_tool"),
            mock.patch("fm11basic.bootstrap_assets"),
            mock.patch("fm11basic.read_basic_file", return_value=source_text),
            mock.patch("fm11basic._run_basic_once", return_value=0) as run_once,
        ):
            result = fm11basic.run_basic(args)

        self.assertEqual(result, 0)
        run_once.assert_called_once()
        self.assertFalse(run_once.call_args.kwargs["use_fast_loader"])

    def test_run_basic_closes_session_when_startup_times_out(self) -> None:
        session = mock.Mock()
        session.start.side_effect = fm11basic.BasicTimeoutError("timed out waiting for FM-11 startup prompt")
        args = SimpleNamespace(file=None, run=False, timeout="20")

        with (
            mock.patch("fm11basic.require_tool"),
            mock.patch("fm11basic.bootstrap_assets"),
            mock.patch("fm11basic.RuntimeSession", return_value=session),
        ):
            with self.assertRaises(fm11basic.BasicTimeoutError):
                fm11basic.run_basic(args)

        session.close.assert_called_once_with()

    def test_run_basic_closes_session_when_batch_times_out(self) -> None:
        session = mock.Mock()
        args = SimpleNamespace(file="demo.bas", run=True, timeout="20")

        with (
            mock.patch("fm11basic.require_tool"),
            mock.patch("fm11basic.bootstrap_assets"),
            mock.patch("fm11basic.RuntimeSession", return_value=session),
            mock.patch("fm11basic.read_basic_file", return_value='10 PRINT "HELLO"\n'),
            mock.patch("fm11basic.handle_startup_prompts", return_value="Ready"),
            mock.patch("fm11basic.wait_for_ready", side_effect=["Ready", "Ready"]),
            mock.patch(
                "fm11basic.collect_run_output",
                side_effect=fm11basic.BasicTimeoutError("timed out waiting for BASIC program completion"),
            ),
        ):
            with self.assertRaises(fm11basic.BasicTimeoutError):
                fm11basic.run_basic(args)

        session.close.assert_called_once_with()
        session.paste_text.assert_has_calls(
            [
                mock.call('10 PRINT "HELLO"\n', deadline=mock.ANY),
                mock.call("CLS\n", deadline=mock.ANY),
                mock.call("RUN\n", deadline=mock.ANY),
            ]
        )

    def test_bootstrap_assets_requires_all_staged_roms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            emulator_path = temp_path / "Fm11.exe"
            emulator_path.write_text("exe\n", encoding="ascii")
            disk_path = temp_path / "fb2hd00.img"
            disk_path.write_bytes(b"disk")
            rom_dir = temp_path / "roms"
            rom_dir.mkdir()
            for rom_name in fm11basic.REQUIRED_ROM_NAMES[:-1]:
                (rom_dir / rom_name).write_text("rom\n", encoding="ascii")

            with (
                mock.patch("fm11basic.run_command", return_value=SimpleNamespace(returncode=0, stderr="")),
                mock.patch.object(fm11basic, "EMULATOR_EXE", emulator_path),
                mock.patch.object(fm11basic, "FBASIC_DISK", disk_path),
                mock.patch.object(fm11basic, "ROM_STAGE_DIR", rom_dir),
            ):
                with self.assertRaisesRegex(fm11basic.BasicRuntimeError, "missing staged FM-11 ROM"):
                    fm11basic.bootstrap_assets()

    def test_main_returns_batch_timeout_exit_code(self) -> None:
        stderr = io.StringIO()
        args = SimpleNamespace(file="demo.bas", run=True, timeout="20")

        with (
            mock.patch("fm11basic.parse_args", return_value=args),
            mock.patch("fm11basic.run_basic", side_effect=fm11basic.BasicTimeoutError("timed out")),
            mock.patch("sys.stderr", stderr),
        ):
            result = fm11basic.main()

        self.assertEqual(result, 124)
        self.assertIn("error: fm11basic batch run timed out after 20", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
