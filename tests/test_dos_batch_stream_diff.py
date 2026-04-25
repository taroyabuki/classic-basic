from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dos_batch_stream_diff import emit_stream_diff


class DosBatchStreamDiffTests(unittest.TestCase):
    def test_holds_back_unterminated_last_line_until_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "CBATCH.TXT"
            state = tmp_path / "state.json"

            source.write_text("first line\nsecond", encoding="utf-8")
            self.assertEqual(emit_stream_diff(source, state), ["first line"])

            source.write_text("first line\nsecond line\n", encoding="utf-8")
            self.assertEqual(emit_stream_diff(source, state), ["second line"])

    def test_final_flush_emits_unterminated_last_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "CBATCH.TXT"
            state = tmp_path / "state.json"

            source.write_text("first line\nsecond", encoding="utf-8")
            self.assertEqual(emit_stream_diff(source, state), ["first line"])
            self.assertEqual(emit_stream_diff(source, state, final=True), ["second"])


if __name__ == "__main__":
    unittest.main()
