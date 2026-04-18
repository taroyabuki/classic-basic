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
    def test_preprocess_normalizes_crlf_and_cr_to_lf_only(self) -> None:
        source = "10 PRINT 1\r\n20 PRINT 2\r30 END"

        processed = preprocess_fm11_source(source)

        self.assertEqual(processed, "10 PRINT 1\n20 PRINT 2\n30 END\n")

    def test_preprocess_preserves_long_lines_and_line_numbers(self) -> None:
        source = "\n".join(
            [
                "100 REM ***** PAI 1986.9.1",
                "181 DEFDBL A:A=16*Z-4*Y:P=VARPTR(A)",
                '183 PRINT RIGHT$("0"+HEX$(PEEK(P+I)),2);" ";',
                "250 RETURN",
            ]
        )

        processed = preprocess_fm11_source(source)

        self.assertEqual(processed, source + "\n")


class FM11BasicRuntimeTests(unittest.TestCase):
    def test_parse_args_accepts_short_file_option(self) -> None:
        with mock.patch.object(sys, "argv", ["fm11basic", "-f", "demo.bas"]):
            args = fm11basic.parse_args()

        self.assertEqual(args.file, "demo.bas")
        self.assertFalse(args.run)

    def test_parse_args_accepts_short_run_and_file_options(self) -> None:
        with mock.patch.object(sys, "argv", ["fm11basic", "-r", "-f", "demo.bas"]):
            args = fm11basic.parse_args()

        self.assertEqual(args.file, "demo.bas")
        self.assertTrue(args.run)

    def test_filter_new_lines_keeps_ready_prompt_after_command_output(self) -> None:
        self.assertEqual(
            fm11basic.filter_new_lines([" 20", "Ready"], "PRINT 10*2\n"),
            [" 20", "Ready"],
        )

    def test_interactive_startup_lines_show_loaded_source_before_ready(self) -> None:
        self.assertEqual(
            fm11basic.interactive_startup_lines(["Ready"], "10 A=10\n20 PRINT A\n"),
            ["10 A=10", "20 PRINT A", "Ready"],
        )

    def test_submit_line_pastes_text_then_presses_return(self) -> None:
        session = mock.Mock()

        fm11basic.submit_line(session, '10 PRINT "HELLO"', deadline=123.0)

        session.paste_text.assert_called_once_with('10 PRINT "HELLO"', deadline=123.0)
        session.key.assert_called_once_with("Return", deadline=123.0)

    def test_submit_line_splits_long_text_into_safe_paste_chunks(self) -> None:
        session = mock.Mock()
        long_line = '20 PRINT RIGHT$("0"+HEX$(PEEK(P+I)),2);" ";'

        fm11basic.submit_line(session, long_line, deadline=321.0)

        self.assertEqual(
            session.paste_text.call_args_list,
            [
                mock.call(long_line[: fm11basic.INPUT_PASTE_CHUNK_SIZE], deadline=321.0),
                mock.call(
                    long_line[
                        fm11basic.INPUT_PASTE_CHUNK_SIZE : fm11basic.INPUT_PASTE_CHUNK_SIZE * 2
                    ],
                    deadline=321.0,
                ),
            ]
            + [
                mock.call(long_line[fm11basic.INPUT_PASTE_CHUNK_SIZE * 2 :], deadline=321.0),
            ],
        )
        session.key.assert_called_once_with("Return", deadline=321.0)

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

    def test_handle_startup_prompts_uses_full_remaining_deadline_for_wait(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "booting",
            "Ready",
        ]
        session.wait_for_text.return_value = "How many 1MB disk drives"

        with (
            mock.patch("fm11basic._try_fast_startup_ready", return_value=None),
            mock.patch("fm11basic._sleep_with_deadline", return_value=None),
            mock.patch("fm11basic.time.monotonic", return_value=100.0),
        ):
            screen = fm11basic.handle_startup_prompts(session, deadline=145.0)

        self.assertEqual(screen, "Ready")
        self.assertEqual(session.wait_for_text.call_args.kwargs["timeout"], 45.0)

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
        self.assertEqual(session.key.call_count, 7)

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
        self.assertEqual(session.key.call_count, 13)

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

    def test_load_source_conservative_allows_offscreen_prompt_for_large_interactive_loads(self) -> None:
        session = mock.Mock()
        source_lines = [f"{index * 10} PRINT {index}" for index in range(1, 14)]
        offscreen_screen = "\n".join(source_lines[-3:])

        with (
            mock.patch("fm11basic._sleep_with_deadline", return_value=None),
            mock.patch(
                "fm11basic.wait_for_ready",
                side_effect=["Ready"] * 12 + [fm11basic.BasicTimeoutError("timed out")],
            ),
            mock.patch.object(session, "copy_screen", return_value=offscreen_screen),
        ):
            raw_screen, screen_lines = fm11basic._load_source_conservative(
                session,
                source_lines,
                "Ready",
                optimize_for_run=False,
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

    def test_runtime_session_start_uses_parent_death_preexec_for_wine(self) -> None:
        session = fm11basic.RuntimeSession()
        sentinel = object()
        fake_proc = mock.Mock()

        with (
            mock.patch.object(session, "_start_xvfb"),
            mock.patch.object(session, "_prepare_wine_prefix"),
            mock.patch.object(session, "_seed_registry"),
            mock.patch.object(session, "_prepare_emulator"),
            mock.patch.object(session, "_wait_for_window", return_value="window-1"),
            mock.patch("fm11basic._parent_death_preexec_fn", return_value=sentinel),
            mock.patch("fm11basic.subprocess.Popen", return_value=fake_proc) as popen,
        ):
            session.start()

        self.assertIs(session.wine_proc, fake_proc)
        self.assertEqual(popen.call_args.kwargs["preexec_fn"], sentinel)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_start_xvfb_uses_parent_death_preexec(self) -> None:
        session = fm11basic.RuntimeSession()
        sentinel = object()
        fake_proc = mock.Mock()
        fake_proc.poll.return_value = None

        with (
            mock.patch("fm11basic._parent_death_preexec_fn", return_value=sentinel),
            mock.patch("fm11basic.subprocess.Popen", return_value=fake_proc) as popen,
            mock.patch("fm11basic._sleep_with_deadline", return_value=None),
            mock.patch(
                "fm11basic.run_command",
                return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
            ),
        ):
            session._start_xvfb()

        self.assertIs(session.xvfb_proc, fake_proc)
        self.assertEqual(popen.call_args.kwargs["preexec_fn"], sentinel)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_runtime_session_close_shuts_down_wineserver_for_prefix(self) -> None:
        session = fm11basic.RuntimeSession()
        session.display = ":99"
        session.wine_proc = mock.Mock()
        session.wine_proc.poll.return_value = 0
        session.xvfb_proc = mock.Mock()
        session.xvfb_proc.poll.return_value = 0

        with (
            mock.patch("fm11basic.run_command", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")) as run_command,
            mock.patch("fm11basic.shutil.rmtree"),
        ):
            session.close()

        run_command.assert_any_call(
            ["wineserver", "-k"],
            env=session.env,
            capture_output=True,
            check=False,
            timeout_seconds=5.0,
            timeout_error="timed out shutting down FM-11 wine services",
        )

    def test_runtime_session_close_terminates_live_process_groups(self) -> None:
        session = fm11basic.RuntimeSession()
        session.display = ":99"
        session.wine_proc = mock.Mock()
        session.wine_proc.pid = 111
        session.wine_proc.poll.return_value = None
        session.xvfb_proc = mock.Mock()
        session.xvfb_proc.pid = 222
        session.xvfb_proc.poll.return_value = None

        with (
            mock.patch("fm11basic.os.killpg") as killpg,
            mock.patch("fm11basic.run_command", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
            mock.patch("fm11basic.shutil.rmtree"),
        ):
            session.close()

        self.assertEqual(
            killpg.call_args_list,
            [
                mock.call(111, fm11basic.signal.SIGTERM),
                mock.call(222, fm11basic.signal.SIGTERM),
            ],
        )

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
                mock.call('10 PRINT "HELLO"', deadline=mock.ANY),
                mock.call("CLS", deadline=mock.ANY),
                mock.call('PRINT "CBATCHBEGIN"', deadline=mock.ANY),
                mock.call("RUN", deadline=mock.ANY),
            ]
        )
        session.key.assert_has_calls(
            [
                mock.call("Return", deadline=mock.ANY),
                mock.call("Return", deadline=mock.ANY),
                mock.call("Return", deadline=mock.ANY),
                mock.call("Return", deadline=mock.ANY),
            ]
        )

    def test_collect_run_output_continues_after_marker_scrolls_offscreen(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "CBATCHBEGIN\nRUN\n 3 / 1         3\n 13 / 4        3.25",
            " 13 / 4        3.25\n 16 / 5        3.2\n 19 / 6        3.16667",
            " 16 / 5        3.2\n 19 / 6        3.16667\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            lines,
            [
                "3 / 1         3",
                "13 / 4        3.25",
                "16 / 5        3.2",
                "19 / 6        3.16667",
            ],
        )
        self.assertEqual(
            emitted,
            [
                ["3 / 1         3", "13 / 4        3.25"],
                ["16 / 5        3.2", "19 / 6        3.16667"],
            ],
        )

    def test_collect_run_output_starts_even_after_run_scrolls_offscreen(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            " 1\n 2\n 3",
            " 3\n 4\n 5",
            " 5\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(lines, ["1", "2", "3", "4", "5"])
        self.assertEqual(
            emitted,
            [
                ["1", "2", "3"],
                ["4", "5"],
            ],
        )

    def test_collect_run_output_accepts_zero_overlap_after_large_jump(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "CBATCHBEGIN\nRUN\n 3 / 1         3\n 13 / 4        3.25",
            'LX  XU   8                 "',
            " 201 / 64      3.140625\n 223 / 71      3.140845070422535",
            " 223 / 71      3.140845070422535\n 245 / 78      3.141025641025641",
            " 245 / 78      3.141025641025641\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            lines,
            [
                "3 / 1         3",
                "13 / 4        3.25",
                "201 / 64      3.140625",
                "223 / 71      3.140845070422535",
                "245 / 78      3.141025641025641",
            ],
        )
        self.assertEqual(
            emitted,
            [
                ["3 / 1         3", "13 / 4        3.25"],
                ["201 / 64      3.140625", "223 / 71      3.140845070422535"],
                ["245 / 78      3.141025641025641"],
            ],
        )

    def test_collect_run_output_ignores_garbage_before_confirmed_jump(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "CBATCHBEGIN\nRUN\n 3 / 1         3\n 13 / 4        3.25",
            'LX  tU                     "',
            " 201 / 64      3.140625\n 223 / 71      3.140845070422535",
            " 223 / 71      3.140845070422535\n 245 / 78      3.141025641025641",
            " 245 / 78      3.141025641025641\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            lines,
            [
                "3 / 1         3",
                "13 / 4        3.25",
                "201 / 64      3.140625",
                "223 / 71      3.140845070422535",
                "245 / 78      3.141025641025641",
            ],
        )
        self.assertEqual(
            emitted,
            [
                ["3 / 1         3", "13 / 4        3.25"],
                ["201 / 64      3.140625", "223 / 71      3.140845070422535"],
                ["245 / 78      3.141025641025641"],
            ],
        )

    def test_collect_run_output_filters_garbage_tail_line(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "CBATCHBEGIN\nRUN\n 333 / 106     3.141509433962264\n 355 / 113     3.141592920353982",
            ' 355 / 113     3.141592920353982\nLX  rU   f                 "',
            " 377 / 120     3.141666666666667\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            lines,
            [
                "333 / 106     3.141509433962264",
                "355 / 113     3.141592920353982",
                "377 / 120     3.141666666666667",
            ],
        )
        self.assertEqual(
            emitted,
            [
                ["333 / 106     3.141509433962264", "355 / 113     3.141592920353982"],
                ["377 / 120     3.141666666666667"],
            ],
        )

    def test_collect_run_output_filters_mixed_case_garbage_tail_line(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "CBATCHBEGIN\nRUN\n 333 / 106     3.141509433962264\n 355 / 113     3.141592920353982",
            ' 355 / 113     3.141592920353982\nLX  qUr  4                 "',
            " 377 / 120     3.141666666666667\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            lines,
            [
                "333 / 106     3.141509433962264",
                "355 / 113     3.141592920353982",
                "377 / 120     3.141666666666667",
            ],
        )
        self.assertEqual(
            emitted,
            [
                ["333 / 106     3.141509433962264", "355 / 113     3.141592920353982"],
                ["377 / 120     3.141666666666667"],
            ],
        )

    def test_collect_run_output_filters_uppercase_garbage_tail_line(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "CBATCHBEGIN\nRUN\n 333 / 106     3.141509433962264\n 355 / 113     3.141592920353982",
            ' 355 / 113     3.141592920353982\nLX  JU                     "',
            " 377 / 120     3.141666666666667\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            lines,
            [
                "333 / 106     3.141509433962264",
                "355 / 113     3.141592920353982",
                "377 / 120     3.141666666666667",
            ],
        )
        self.assertEqual(
            emitted,
            [
                ["333 / 106     3.141509433962264", "355 / 113     3.141592920353982"],
                ["377 / 120     3.141666666666667"],
            ],
        )

    def test_collect_run_output_filters_punctuated_garbage_tail_line(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "CBATCHBEGIN\nRUN\n 333 / 106     3.141509433962264\n 355 / 113     3.141592920353982",
            ' 355 / 113     3.141592920353982\nLX  CU(                    "',
            " 377 / 120     3.141666666666667\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            lines,
            [
                "333 / 106     3.141509433962264",
                "355 / 113     3.141592920353982",
                "377 / 120     3.141666666666667",
            ],
        )
        self.assertEqual(
            emitted,
            [
                ["333 / 106     3.141509433962264", "355 / 113     3.141592920353982"],
                ["377 / 120     3.141666666666667"],
            ],
        )

    def test_collect_run_output_filters_unquoted_garbage_tail_line(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n11\n12\n13",
            "y QJ   J",
            "13\n14\n15",
            "15\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            _, lines = fm11basic.collect_run_output(
                session,
                ["Ready"],
                'PRINT "CBATCHBEGIN"\nRUN\n',
                marker="CBATCHBEGIN",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(lines, [str(i) for i in range(1, 16)])
        self.assertEqual(
            emitted,
            [
                [str(i) for i in range(1, 14)],
                ["14", "15"],
            ],
        )

    def test_run_basic_without_timeout_uses_unbounded_batch_collection(self) -> None:
        session = mock.Mock()
        args = SimpleNamespace(file="demo.bas", run=True, timeout=None)

        with (
            mock.patch("fm11basic.require_tool"),
            mock.patch("fm11basic.bootstrap_assets"),
            mock.patch("fm11basic.RuntimeSession", return_value=session),
            mock.patch("fm11basic.read_basic_file", return_value='10 PRINT "HELLO"\n'),
            mock.patch("fm11basic.handle_startup_prompts", return_value="Ready"),
            mock.patch("fm11basic.wait_for_ready", side_effect=["Ready", "Ready"]),
            mock.patch("fm11basic.collect_run_output", return_value=(["Ready"], ["HELLO"])) as collect_run_output,
        ):
            result = fm11basic.run_basic(args)

        self.assertEqual(result, 0)
        self.assertIsNone(collect_run_output.call_args.kwargs["timeout"])
        self.assertIs(collect_run_output.call_args.kwargs["on_lines"], fm11basic.emit_lines)

    def test_run_basic_with_timeout_uses_full_remaining_budget_for_batch_collection(self) -> None:
        session = mock.Mock()
        args = SimpleNamespace(file="demo.bas", run=True, timeout="300")

        with (
            mock.patch("fm11basic.require_tool"),
            mock.patch("fm11basic.bootstrap_assets"),
            mock.patch("fm11basic.RuntimeSession", return_value=session),
            mock.patch("fm11basic.read_basic_file", return_value='10 PRINT "HELLO"\n'),
            mock.patch("fm11basic.handle_startup_prompts", return_value="Ready"),
            mock.patch("fm11basic.wait_for_ready", side_effect=["Ready", "Ready"]),
            mock.patch("fm11basic.time.monotonic", return_value=100.0),
            mock.patch("fm11basic.collect_run_output", return_value=(["Ready"], ["HELLO"])) as collect_run_output,
        ):
            result = fm11basic.run_basic(args)

        self.assertEqual(result, 0)
        self.assertEqual(collect_run_output.call_args.kwargs["timeout"], 300.0)

    def test_collect_command_output_streams_lines_before_ready(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "Ready\nRUN\n 3 / 1         3",
            "RUN\n 3 / 1         3\n 13 / 4        3.25",
            " 3 / 1         3\n 13 / 4        3.25\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            raw_screen, lines = fm11basic.collect_command_output(
                session,
                ["Ready"],
                "run\n",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(raw_screen, " 3 / 1         3\n 13 / 4        3.25\nReady")
        self.assertEqual(lines, [" 3 / 1         3", " 13 / 4        3.25", "Ready"])
        self.assertEqual(
            emitted,
            [
                ["3 / 1         3"],
                ["13 / 4        3.25"],
                ["Ready"],
            ],
        )

    def test_collect_command_output_run_ignores_scrolled_source_listing(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "Ready\nRUN\n 3 / 1         3\n 13 / 4        3.25",
            "\n".join(
                [
                    '        LX   U   X                 "',
                    "40 L=A*N",
                    "50 M=INT(L)",
                    "60 IF M+1-L<L-M THEN M=M+1",
                    "70 E=ABS(A-M/N)",
                    "80 IF E>=D GOTO 110",
                    '90 PRINT M;"/";N,M/N',
                    "100 D=E",
                    "110 N=N+1",
                    "120 GOTO 40",
                    " 3 / 1         3",
                    " 13 / 4        3.25",
                    " 16 / 5        3.2",
                ]
            ),
            "\n".join(
                [
                    '        LX F U                     "',
                    "50 M=INT(L)",
                    "60 IF M+1-L<L-M THEN M=M+1",
                    "70 E=ABS(A-M/N)",
                    "80 IF E>=D GOTO 110",
                    '90 PRINT M;"/";N,M/N',
                    "100 D=E",
                    "110 N=N+1",
                    "120 GOTO 40",
                    " 3 / 1         3",
                    " 13 / 4        3.25",
                    " 16 / 5        3.2",
                    " 19 / 6        3.16667",
                    "Ready",
                ]
            ),
        ]
        emitted: list[list[str]] = []
        ignored_source_lines = {
            "40 L=A*N",
            "50 M=INT(L)",
            "60 IF M+1-L<L-M THEN M=M+1",
            "70 E=ABS(A-M/N)",
            "80 IF E>=D GOTO 110",
            '90 PRINT M;"/";N,M/N',
            "100 D=E",
            "110 N=N+1",
            "120 GOTO 40",
        }

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            fm11basic.collect_command_output(
                session,
                ["Ready"],
                "run\n",
                on_lines=lambda chunk: emitted.append(list(chunk)),
                ignored_source_lines=ignored_source_lines,
            )

        self.assertEqual(
            emitted,
            [
                ["3 / 1         3", "13 / 4        3.25"],
                ["16 / 5        3.2"],
                ["19 / 6        3.16667"],
                ["Ready"],
            ],
        )

    def test_collect_command_output_run_starts_even_after_run_line_scrolls_offscreen(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            " 3 / 1         3\n 13 / 4        3.25",
            " 13 / 4        3.25\n 16 / 5        3.2",
            " 16 / 5        3.2\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            fm11basic.collect_command_output(
                session,
                ["Ready"],
                "run\n",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            emitted,
            [
                ["3 / 1         3", "13 / 4        3.25"],
                ["16 / 5        3.2"],
                ["Ready"],
            ],
        )

    def test_collect_command_output_run_ignores_no_overlap_redraw(self) -> None:
        session = mock.Mock()
        session.copy_screen.side_effect = [
            "Ready\nRUN\n 3 / 1         3\n 13 / 4        3.25",
            'LX  XU   8                 "\n',
            '\n'.join(
                [
                    'LX  EU                     "',
                    " 3 / 1         3",
                    " 13 / 4        3.25",
                    " 16 / 5        3.2",
                    "Ready",
                ]
            ),
            " 13 / 4        3.25\n 16 / 5        3.2\nReady",
        ]
        emitted: list[list[str]] = []

        with mock.patch("fm11basic._sleep_with_deadline", return_value=None):
            fm11basic.collect_command_output(
                session,
                ["Ready"],
                "run\n",
                on_lines=lambda chunk: emitted.append(list(chunk)),
            )

        self.assertEqual(
            emitted,
            [
                ["3 / 1         3", "13 / 4        3.25"],
                ["16 / 5        3.2"],
                ["Ready"],
            ],
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

    def test_main_returns_130_on_keyboard_interrupt(self) -> None:
        args = SimpleNamespace(file="demo.bas", run=True, timeout=None)

        with (
            mock.patch("fm11basic.parse_args", return_value=args),
            mock.patch("fm11basic.run_basic", side_effect=KeyboardInterrupt),
        ):
            result = fm11basic.main()

        self.assertEqual(result, 130)

    def test_main_returns_141_on_broken_pipe(self) -> None:
        args = SimpleNamespace(file="demo.bas", run=True, timeout=None)

        with (
            mock.patch("fm11basic.parse_args", return_value=args),
            mock.patch("fm11basic.run_basic", side_effect=BrokenPipeError),
        ):
            result = fm11basic.main()

        self.assertEqual(result, 141)


if __name__ == "__main__":
    unittest.main()
