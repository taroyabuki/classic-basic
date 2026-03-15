from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from msx_basic.loop import (
    _post_run_prompt_visible,
    _screen_ready_for_input,
    _drain_until_prompt,
    _type_program_line,
    _wait_for_line_entry,
    run_batch,
)


class FakeBridge:
    def __init__(
        self,
        screens: list[tuple[list[str], int]],
        *,
        booted: bool = True,
        vartab_values: list[int] | None = None,
    ) -> None:
        self._screens = screens
        self._index = 0
        self._current_lines, self._current_cursor_row = self._screens[0]
        self.booted = booted
        self.type_calls: list[tuple[str, bool, float | None]] = []
        self._vartab_values = vartab_values or [0x8003]
        self._vartab_index = 0

    def wait_for_boot(self, timeout: float = 10.0) -> bool:
        return self.booted

    def get_screen(self, timeout: float = 1.0) -> list[str]:
        if self._index < len(self._screens):
            self._current_lines, self._current_cursor_row = self._screens[self._index]
            self._index += 1
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


def _screen(cursor_row: int, *entries: tuple[int, str]) -> tuple[list[str], int]:
    lines = [""] * 24
    for row, text in entries:
        lines[row] = text
    return lines, cursor_row


class MsxBasicLoopTests(unittest.TestCase):
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

    def test_type_program_line_chunks_long_input(self) -> None:
        bridge = FakeBridge([_screen(0, (0, "Ok"))] * 8)

        with patch("msx_basic.loop.time.sleep", return_value=None):
            with patch("msx_basic.loop._wait_for_line_entry", return_value=True):
                _type_program_line(bridge, "X" * 100)

        self.assertEqual(
            [call[0] for call in bridge.type_calls],
            ["X" * 48, "X" * 48, "X" * 4, "\r"],
        )
        self.assertTrue(all(call[1] for call in bridge.type_calls))

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
