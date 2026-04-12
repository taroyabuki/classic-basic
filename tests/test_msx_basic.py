from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from msx_basic.bridge import OpenMSXBridge
from msx_basic.cli import main
from msx_basic.loop import (
    _BLINK_BLOCK_CURSOR,
    _INTERACTIVE_INPUT_TIMEOUT,
    _normalize_interactive_completed_line,
    _normalize_interactive_cursor_row,
    _post_run_prompt_visible,
    _RESET_CURSOR_STYLE,
    _screen_ready_for_input,
    _SHOW_CURSOR,
    _startup_terminal_render,
    _drain_until_prompt,
    _load_program_lines,
    _looks_like_source_listing,
    _print_batch_result,
    _type_program_line,
    _wait_for_line_entry,
    ScreenTracker,
    run_load,
    run_loaded_batch,
    run_interactive,
    run_loop,
    run_batch,
)


class FakeBridge:
    def __init__(
        self,
        screens: list[tuple[list[str], int]],
        *,
        booted: bool = True,
        vartab_values: list[int] | None = None,
        timeout_chunks: set[str] | None = None,
    ) -> None:
        self._screens = screens
        self._index = 0
        self._current_lines, self._current_cursor_row = self._screens[0]
        self.booted = booted
        self.type_calls: list[tuple[str, bool, float | None]] = []
        self._vartab_values = vartab_values or [0x8003]
        self._vartab_index = 0
        self._timeout_chunks = set(timeout_chunks or ())
        self.screen_state_calls = 0

    def wait_for_boot(self, timeout: float = 10.0) -> bool:
        return self.booted

    def _advance(self) -> None:
        if self._index < len(self._screens):
            self._current_lines, self._current_cursor_row = self._screens[self._index]
            self._index += 1

    def get_screen_state(self, timeout: float = 1.0) -> tuple[list[str], int]:
        del timeout
        self.screen_state_calls += 1
        self._advance()
        return list(self._current_lines), self._current_cursor_row

    def get_screen(self, timeout: float = 1.0) -> list[str]:
        del timeout
        self._advance()
        return list(self._current_lines)

    def get_cursor_row(self) -> int:
        return self._current_cursor_row

    def peek16(self, address: int, timeout: float = 1.0) -> int:
        del address, timeout
        value = self._vartab_values[min(self._vartab_index, len(self._vartab_values) - 1)]
        self._vartab_index += 1
        return value

    def type_text(
        self,
        text: str,
        *,
        via_keybuf: bool = False,
        timeout: float | None = None,
    ) -> None:
        self.type_calls.append((text, via_keybuf, timeout))
        if text in self._timeout_chunks:
            self._timeout_chunks.remove(text)
            raise TimeoutError(text)

    def command(self, text: str, timeout: float = 1.0) -> None:
        del timeout
        self.type_calls.append((f"COMMAND:{text}", False, None))

    def command_nowait(self, text: str) -> None:
        self.type_calls.append((f"NOWAIT:{text}", False, None))


class FakeTerminal:
    def __init__(self, bytes_in: list[int]) -> None:
        self._bytes = list(bytes_in)
        self.writes: list[str] = []

    def input_ready(self) -> bool:
        return bool(self._bytes)

    def read_byte(self) -> int:
        return self._bytes.pop(0)

    def write(self, text: str) -> None:
        self.writes.append(text)


class FakeTerminalContext:
    def __init__(self, terminal: FakeTerminal) -> None:
        self._terminal = terminal

    def __enter__(self) -> FakeTerminal:
        return self._terminal

    def __exit__(self, *_) -> None:
        return None


def _screen(cursor_row: int, *entries: tuple[int, str]) -> tuple[list[str], int]:
    lines = [""] * 24
    for row, text in entries:
        lines[row] = text
    return lines, cursor_row


class MsxBasicLoopTests(unittest.TestCase):
    def test_normalize_interactive_cursor_row_moves_cursor_below_ok_prompt(self) -> None:
        lines, cursor_row = _screen(3, (3, "Ok"))
        self.assertEqual(_normalize_interactive_cursor_row(lines, cursor_row), 4)

    def test_startup_terminal_render_shows_ok_prompt_on_blank_input_row(self) -> None:
        self.assertEqual(_startup_terminal_render(*_screen(5, (3, "Ok"))), "Ok\r\n")

    def test_screen_tracker_uses_line_above_blank_cursor_for_interactive_echo(self) -> None:
        tracker = ScreenTracker()
        tracker.seed(*_screen(5, (3, "Ok")))

        events = tracker.update(
            *_screen(
                5,
                (3, "Ok"),
                (4, "PRINT 2+2"),
            )
        )

        self.assertEqual(events, [("cursor", "PRINT 2+2")])

    def test_screen_tracker_preserves_trailing_space_in_interactive_echo(self) -> None:
        tracker = ScreenTracker()
        tracker.seed(*_screen(5, (3, "Ok")))

        events = tracker.update(
            *_screen(
                5,
                (3, "Ok"),
                (4, "PRINT "),
            )
        )

        self.assertEqual(events, [("cursor", "PRINT ")])

    def test_screen_tracker_treats_blank_input_row_as_empty_cursor_text(self) -> None:
        tracker = ScreenTracker()
        lines, cursor_row = _screen(5, (3, "Ok"))
        lines[5] = " " * 40
        tracker.seed(lines, cursor_row)

        next_lines, next_cursor_row = _screen(5, (3, "Ok"))
        next_lines[5] = " " * 40
        events = tracker.update(next_lines, next_cursor_row)

        self.assertEqual(events, [("cursor", "")])

    def test_screen_tracker_treats_function_key_guide_cursor_row_as_empty(self) -> None:
        tracker = ScreenTracker()
        tracker.seed(*_screen(8, (4, "  run"), (5, "  COUNT 1"), (6, "  COUNT 2"), (7, "  COUNT 3"), (8, "Ok")))

        events = tracker.update(
            *_screen(
                9,
                (4, "  run"),
                (5, "  COUNT 1"),
                (6, "  COUNT 2"),
                (7, "  COUNT 3"),
                (8, "Ok"),
                (9, "  color  auto   goto   list   run"),
            )
        )

        self.assertEqual(
            events,
            [
                ("line", "  COUNT 3"),
                ("line", "Ok"),
                ("cursor", ""),
            ],
        )

    def test_screen_tracker_captures_completed_lines_after_seeded_prompt(self) -> None:
        tracker = ScreenTracker()
        tracker.seed(*_screen(5, (3, "Ok"), (4, "PRINT 2+2")))

        events = tracker.update(
            *_screen(
                8,
                (3, "Ok"),
                (4, "PRINT 2+2"),
                (5, " 4"),
                (6, "Ok"),
            )
        )

        self.assertEqual(
            events,
            [
                ("line", "PRINT 2+2"),
                ("line", " 4"),
                ("line", "Ok"),
                ("cursor", ""),
            ],
        )

    def test_screen_tracker_captures_second_direct_statement_after_prompt_reuse(self) -> None:
        tracker = ScreenTracker()
        tracker.seed(*_screen(8, (3, "Ok"), (4, "PRINT 2+2"), (5, " 4"), (6, "Ok"), (7, "PRINT 2-3")))

        events = tracker.update(
            *_screen(
                11,
                (3, "Ok"),
                (4, "PRINT 2+2"),
                (5, " 4"),
                (6, "Ok"),
                (7, "PRINT 2-3"),
                (8, " -1"),
                (9, "Ok"),
            )
        )

        self.assertEqual(
            events,
            [
                ("line", "PRINT 2-3"),
                ("line", " -1"),
                ("line", "Ok"),
                ("cursor", ""),
            ],
        )

    def test_screen_ready_for_input_accepts_blank_cursor_below_ok(self) -> None:
        lines, cursor_row = _screen(5, (3, "Ok"))
        self.assertTrue(_screen_ready_for_input(lines, cursor_row))

    def test_post_run_prompt_visible_uses_last_non_empty_line(self) -> None:
        lines, cursor_row = _screen(9, (7, "Ok"))
        self.assertTrue(_post_run_prompt_visible(lines, cursor_row))

    def test_post_run_prompt_visible_ignores_function_key_guide(self) -> None:
        lines, cursor_row = _screen(
            23,
            (21, "Ok"),
            (23, "  color  auto   goto   list   run"),
        )
        self.assertTrue(_post_run_prompt_visible(lines, cursor_row))

    def test_normalize_interactive_completed_line_ignores_function_key_guide(self) -> None:
        self.assertIsNone(_normalize_interactive_completed_line("  color  auto   goto   list   run"))

    def test_numeric_program_output_is_not_treated_as_source_listing(self) -> None:
        self.assertFalse(_looks_like_source_listing("4131415926535898"))
        self.assertFalse(_looks_like_source_listing("0"))

    def test_type_program_line_chunks_long_input(self) -> None:
        bridge = FakeBridge([_screen(0, (0, "Ok"))] * 8)

        with patch("msx_basic.loop.time.sleep", return_value=None):
            with patch("msx_basic.loop._wait_for_line_entry", return_value=True):
                _type_program_line(bridge, "X" * 100)

        self.assertEqual(
            [call[0] for call in bridge.type_calls],
            ["X" * 24, "X" * 24, "X" * 24, "X" * 24, "X" * 4, "\r"],
        )
        self.assertEqual([call[1] for call in bridge.type_calls], [True, True, True, True, True, True])

    def test_type_program_line_retries_timed_out_chunk_with_smaller_pieces(self) -> None:
        bridge = FakeBridge([_screen(0, (0, "Ok"))] * 8, timeout_chunks={"X" * 24})

        with patch("msx_basic.loop.time.sleep", return_value=None):
            with patch("msx_basic.loop._wait_for_line_entry", return_value=True):
                _type_program_line(bridge, "X" * 60)

        self.assertEqual(
            [call[0] for call in bridge.type_calls],
            ["X" * 24, "X" * 12, "X" * 12, "X" * 24, "X" * 12, "\r"],
        )
        self.assertEqual([call[1] for call in bridge.type_calls], [True, True, True, True, True, True])

    def test_wait_for_line_entry_accepts_vartab_growth_without_screen_motion(self) -> None:
        bridge = FakeBridge(
            [_screen(5, (3, "Ok"))] * 4,
            vartab_values=[0x8003, 0x8003, 0x8010],
        )

        with patch("msx_basic.loop.time.sleep", return_value=None):
            result = _wait_for_line_entry(
                bridge,
                *_screen(5, (3, "Ok")),
                0x8003,
                timeout=1.0,
            )

        self.assertTrue(result)

    def test_drain_until_prompt_waits_for_post_run_prompt(self) -> None:
        initial_lines, initial_cursor_row = _screen(5, (5, "Ok"))
        bridge = FakeBridge(
            [
                _screen(5, (5, "Ok")),
                _screen(5, (5, "RUN")),
                _screen(7, (5, "RUN"), (6, "HELLO")),
                _screen(8, (5, "RUN"), (6, "HELLO"), (7, "Ok")),
            ]
        )

        with patch("msx_basic.loop.time.sleep", return_value=None):
            output = _drain_until_prompt(
                bridge,
                timeout=1.0,
                initial_lines=initial_lines,
                initial_cursor_row=initial_cursor_row,
                ignored_lines=set(),
            )

        self.assertEqual(output, ["HELLO"])

    def test_drain_until_prompt_uses_atomic_screen_state(self) -> None:
        initial_lines, initial_cursor_row = _screen(5, (5, "Ok"))
        bridge = FakeBridge(
            [
                _screen(5, (5, "Ok")),
                _screen(5, (5, "RUN")),
                _screen(7, (5, "RUN"), (6, "HELLO")),
                _screen(8, (5, "RUN"), (6, "HELLO"), (7, "Ok")),
            ]
        )

        with patch.object(bridge, "get_cursor_row", side_effect=AssertionError("separate cursor read")):
            with patch("msx_basic.loop.time.sleep", return_value=None):
                output = _drain_until_prompt(
                    bridge,
                    timeout=1.0,
                    initial_lines=initial_lines,
                    initial_cursor_row=initial_cursor_row,
                    ignored_lines=set(),
                )

        self.assertEqual(output, ["HELLO"])
        self.assertGreaterEqual(bridge.screen_state_calls, 1)

    def test_drain_until_prompt_captures_lines_after_run_when_cursor_row_reused(self) -> None:
        initial_lines, initial_cursor_row = _screen(22, (18, '450 PRINT "NOT FOUND":END'))
        bridge = FakeBridge(
            [
                (initial_lines, initial_cursor_row),
                _screen(
                    22,
                    (18, '450 PRINT "NOT FOUND":END'),
                    (19, "RUN"),
                    (20, "FOUND 2"),
                    (21, "Ok"),
                ),
            ]
        )

        with patch("msx_basic.loop.time.sleep", return_value=None):
            output = _drain_until_prompt(
                bridge,
                timeout=1.0,
                initial_lines=initial_lines,
                initial_cursor_row=initial_cursor_row,
                ignored_lines={'450 PRINT "NOT FOUND":END'},
            )

        self.assertEqual(output, ["FOUND 2"])

    def test_run_batch_collects_output_even_after_prompt_returns(self) -> None:
        ready = _screen(0, (0, "Ok"))
        bridge = FakeBridge([ready, ready, ready, ready])

        with tempfile.TemporaryDirectory() as tmp:
            program_path = Path(tmp) / "hello.bas"
            program_path.write_text('10 PRINT "HELLO"\n20 END\n', encoding="utf-8")
            with patch("msx_basic.loop.time.sleep", return_value=None):
                with patch("msx_basic.loop._drain_until_prompt", return_value=["HELLO"]):
                    with patch("builtins.print") as mock_print:
                        result = run_batch(bridge, program_path)

        self.assertEqual(result, 0)
        self.assertEqual(
            [call.args[0] for call in mock_print.call_args_list],
            ["HELLO"],
        )

    def test_run_load_types_program_lines_and_waits_for_prompt(self) -> None:
        ready = _screen(0, (0, "Ok"))
        bridge = FakeBridge([ready, ready, ready, ready])

        with tempfile.TemporaryDirectory() as tmp:
            program_path = Path(tmp) / "hello.bas"
            program_path.write_text("10 PRINT 1\r\n\r20 END\r", encoding="ascii")
            with patch("msx_basic.loop.time.sleep", return_value=None):
                with patch("msx_basic.loop._wait_for_stable_ok_prompt", return_value=True):
                    with patch("msx_basic.loop._ensure_prompt_after_load", return_value=True) as ensure_prompt:
                        run_load(bridge, program_path)

        self.assertEqual(
            [call[0] for call in bridge.type_calls if not call[0].startswith("COMMAND:")],
            ["10 PRINT 1", "\r", "20 END", "\r"],
        )
        ensure_prompt.assert_called_once_with(bridge, timeout=4.0)

    def test_load_program_lines_accepts_all_newlines_and_skips_blanks(self) -> None:
        cases = {
            "lf.bas": "10 PRINT 1\n\n20 END\n",
            "crlf.bas": "10 PRINT 1\r\n\r\n20 END\r\n",
            "cr.bas": "10 PRINT 1\r\r20 END\r",
        }

        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            for name, source in cases.items():
                bridge = FakeBridge([_screen(0, (0, "Ok"))] * 6)
                program_path = temp_path / name
                program_path.write_text(source, encoding="ascii", newline="")
                with patch("msx_basic.loop.time.sleep", return_value=None):
                    with patch("msx_basic.loop._wait_for_line_entry", return_value=True):
                        loaded = _load_program_lines(bridge, program_path)
                self.assertEqual(loaded, ["10 PRINT 1", "20 END"])

    def test_run_loaded_batch_loads_then_runs_without_interactive_loop(self) -> None:
        ready = _screen(0, (0, "Ok"))
        bridge = FakeBridge([ready, ready, ready, ready])

        with tempfile.TemporaryDirectory() as tmp:
            program_path = Path(tmp) / "hello.bas"
            program_path.write_text('10 PRINT "HELLO"\n20 END\n', encoding="ascii")
            with patch("msx_basic.loop.run_load") as run_load_mock:
                with patch("msx_basic.loop._configure_batch_speed") as speed_mock:
                    with patch("msx_basic.loop._run_loaded_program", return_value=["HELLO"]) as run_mock:
                        with patch("msx_basic.loop._print_batch_result") as print_mock:
                            result = run_loaded_batch(bridge, program_path)

        self.assertEqual(result, 0)
        run_load_mock.assert_called_once_with(bridge, program_path)
        speed_mock.assert_called_once_with(bridge)
        run_mock.assert_called_once()
        print_mock.assert_called_once_with(["HELLO"], ['10 PRINT "HELLO"', "20 END"])

    def test_print_batch_result_writes_stdout_lines(self) -> None:
        stdout = io.StringIO()
        with patch("sys.stdout", stdout):
            _print_batch_result(["10 PRINT 1", "HELLO"], ["10 PRINT 1"])
        self.assertEqual(stdout.getvalue(), "HELLO\n")

    def test_print_batch_result_skips_wrapped_source_fragments(self) -> None:
        stdout = io.StringIO()
        loaded_lines = [
            "10 A#=4#*ATN(1#)",
            "20 FOR I=0 TO 7:PRINT HEX$(PEEK(VARPTR(A#)+I));:NEXT I:PRINT",
            "30 END",
        ]
        with patch("sys.stdout", stdout):
            _print_batch_result(
                ["R(A#)+I));:NEXT I:PRINT", "9858532659413141", "6.76E-15"],
                loaded_lines,
            )
        self.assertEqual(stdout.getvalue(), "9858532659413141\n6.76E-15\n")

    def test_run_loop_exits_on_ctrl_d_without_forwarding_input(self) -> None:
        bridge = FakeBridge([_screen(0, (0, "Ok"))])
        terminal = FakeTerminal([0x04])

        run_loop(bridge, terminal)

        self.assertEqual("".join(terminal.writes), "\r\n[Exit]\r\n")
        self.assertEqual(bridge.type_calls, [])

    def test_run_loop_uses_keybuf_path_for_interactive_text_input(self) -> None:
        bridge = FakeBridge([_screen(0, (0, "Ok"))] * 4)
        terminal = FakeTerminal([ord("A"), 0x0D, 0x08, 0x04])

        with patch("msx_basic.loop.time.sleep", return_value=None):
            run_loop(bridge, terminal)

        self.assertEqual(
            bridge.type_calls[:3],
            [
                ("A", True, _INTERACTIVE_INPUT_TIMEOUT),
                ("\r", True, _INTERACTIVE_INPUT_TIMEOUT),
                ("\b", True, _INTERACTIVE_INPUT_TIMEOUT),
            ],
        )

    def test_run_loop_locally_echoes_first_character_at_column_zero(self) -> None:
        bridge = FakeBridge([_screen(4, (3, "Ok"), (4, " " * 40))] * 4)
        terminal = FakeTerminal([ord("A"), 0x04])

        with patch("msx_basic.loop.time.sleep", return_value=None):
            run_loop(bridge, terminal)

        self.assertEqual("".join(terminal.writes), "A\r\n[Exit]\r\n")

    def test_run_loop_suppresses_padded_program_line_echo_from_screen(self) -> None:
        bridge = FakeBridge(
            [
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(6, (3, "Ok"), (4, "  10 a=1")),
            ]
        )
        terminal = FakeTerminal([ord(char) for char in "10 a=1"] + [0x0D, 0x04])

        with patch("msx_basic.loop.time.sleep", return_value=None):
            with patch("msx_basic.loop.time.monotonic", side_effect=(index / 10 for index in range(1, 16))):
                run_loop(bridge, terminal)

        self.assertEqual("".join(terminal.writes), "10 a=1\r\n\r\n[Exit]\r\n")

    def test_run_loop_suppresses_pending_cursor_echo_after_enter(self) -> None:
        bridge = FakeBridge(
            [
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok"), (4, "  10 a=1")),
                _screen(5, (3, "Ok")),
            ]
        )
        terminal = FakeTerminal([ord(char) for char in "10 a=1"] + [0x0D, 0x04])

        with patch("msx_basic.loop.time.sleep", return_value=None):
            with patch("msx_basic.loop.time.monotonic", side_effect=(index / 10 for index in range(1, 18))):
                run_loop(bridge, terminal)

        self.assertEqual("".join(terminal.writes), "10 a=1\r\n\r\n[Exit]\r\n")

    def test_run_loop_suppresses_padded_run_echo_and_normalizes_ok_prompt(self) -> None:
        bridge = FakeBridge(
            [
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(5, (3, "Ok")),
                _screen(8, (3, "Ok"), (4, "  run"), (5, "   1"), (6, "  Ok")),
            ]
        )
        terminal = FakeTerminal([ord("r"), ord("u"), ord("n"), 0x0D, 0x04])

        with patch("msx_basic.loop.time.sleep", return_value=None):
            with patch("msx_basic.loop.time.monotonic", side_effect=(index / 10 for index in range(1, 12))):
                run_loop(bridge, terminal)

        self.assertEqual("".join(terminal.writes), "run\r\n\r   1\r\n\rOk\r\n\r\n[Exit]\r\n")

    def test_run_interactive_renders_initial_ok_prompt_before_entering_loop(self) -> None:
        terminal = FakeTerminal([])
        bridge = FakeBridge([_screen(3, (3, "Ok"))])

        with patch("msx_basic.loop._get_raw_terminal", return_value=lambda: FakeTerminalContext(terminal)):
            with patch("msx_basic.loop.run_loop") as run_loop_mock:
                run_interactive(bridge)

        self.assertEqual(
            "".join(terminal.writes),
            f"{_SHOW_CURSOR}{_BLINK_BLOCK_CURSOR}MSX-BASIC ready.\r\nOk\r\n{_RESET_CURSOR_STYLE}{_SHOW_CURSOR}",
        )
        run_loop_mock.assert_called_once_with(bridge, terminal)

    def test_run_interactive_renders_loaded_program_listing_after_prompt(self) -> None:
        terminal = FakeTerminal([])
        bridge = FakeBridge([_screen(3, (3, "Ok"))])

        with patch("msx_basic.loop._get_raw_terminal", return_value=lambda: FakeTerminalContext(terminal)):
            with patch("msx_basic.loop.run_loop") as run_loop_mock:
                run_interactive(bridge, loaded_program_lines=["10 PRINT 1", "20 END"])

        self.assertEqual(
            "".join(terminal.writes),
            (
                f"{_SHOW_CURSOR}{_BLINK_BLOCK_CURSOR}MSX-BASIC ready.\r\n"
                "Ok\r\n10 PRINT 1\r\n20 END\r\n"
                f"{_RESET_CURSOR_STYLE}{_SHOW_CURSOR}"
            ),
        )
        run_loop_mock.assert_called_once_with(bridge, terminal)


class MsxBasicBridgeTests(unittest.TestCase):
    def test_get_screen_state_preserves_trailing_spaces_for_active_rows(self) -> None:
        bridge = OpenMSXBridge()
        with patch.object(
            bridge,
            "command",
            return_value="Ok\n\n\n\nPRINT \n@@CURSOR_ROW@@5",
        ):
            lines, cursor_row = bridge.get_screen_state()

        self.assertEqual(cursor_row, 5)
        self.assertEqual(lines[0], "Ok")
        self.assertEqual(lines[4], "PRINT ")


class MsxBasicCliTests(unittest.TestCase):
    def test_interactive_program_loads_then_runs_terminal(self) -> None:
        with patch("msx_basic.cli.OpenMSXBridge") as bridge_cls:
            with patch("msx_basic.cli._create_machine_config", return_value=("machine", Path("/tmp/config.xml"))):
                with patch("msx_basic.cli.run_load") as run_load_mock:
                    with patch("msx_basic.cli.run_interactive") as run_interactive_mock:
                        result = main(["--rom", __file__, "--interactive", "demo.bas"])

        self.assertEqual(result, 0)
        bridge = bridge_cls.return_value.__enter__.return_value
        run_load_mock.assert_called_once_with(bridge, Path("demo.bas"))
        run_interactive_mock.assert_called_once_with(bridge, loaded_program_lines=run_load_mock.return_value)

    def test_run_loaded_uses_loaded_batch_path_not_plain_batch(self) -> None:
        with patch("msx_basic.cli.OpenMSXBridge") as bridge_cls:
            with patch("msx_basic.cli._create_machine_config", return_value=("machine", Path("/tmp/config.xml"))):
                with patch("msx_basic.cli.run_loaded_batch", return_value=0) as run_loaded_mock:
                    with patch("msx_basic.cli.run_batch") as run_batch_mock:
                        result = main(["--rom", __file__, "--run-loaded", "demo.bas"])

        self.assertEqual(result, 0)
        bridge = bridge_cls.return_value.__enter__.return_value
        run_loaded_mock.assert_called_once_with(bridge, Path("demo.bas"))
        run_batch_mock.assert_not_called()

    def test_run_loaded_requires_program(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with patch("msx_basic.cli.OpenMSXBridge") as bridge_cls:
                with patch("msx_basic.cli._create_machine_config", return_value=("machine", Path("/tmp/config.xml"))):
                    result = main(["--rom", __file__, "--run-loaded"])

        self.assertEqual(result, 2)
        self.assertIn("--run-loaded requires PROGRAM.bas", stderr.getvalue())
        bridge_cls.assert_called_once()

    def test_run_loaded_cannot_be_combined_with_interactive(self) -> None:
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            with patch("msx_basic.cli.OpenMSXBridge") as bridge_cls:
                with patch("msx_basic.cli._create_machine_config", return_value=("machine", Path("/tmp/config.xml"))):
                    result = main(["--rom", __file__, "--run-loaded", "--interactive", "demo.bas"])

        self.assertEqual(result, 2)
        self.assertIn("--run-loaded cannot be combined with --interactive", stderr.getvalue())
        bridge_cls.assert_called_once()

    def test_main_returns_130_on_keyboard_interrupt(self) -> None:
        with patch("msx_basic.cli.OpenMSXBridge") as bridge_cls:
            with patch("msx_basic.cli._create_machine_config", return_value=("machine", Path("/tmp/config.xml"))):
                with patch("msx_basic.cli.run_loaded_batch", side_effect=KeyboardInterrupt):
                    result = main(["--rom", __file__, "--run-loaded", "demo.bas"])

        self.assertEqual(result, 130)
        bridge_cls.assert_called_once()
