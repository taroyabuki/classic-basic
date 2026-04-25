from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

from basic_batch_exit import rewrite_file, rewrite_program_for_system_exit


class BasicBatchExitTests(unittest.TestCase):
    def test_rewrite_program_replaces_terminal_end(self) -> None:
        self.assertEqual(
            rewrite_program_for_system_exit(["10 PRINT 1", "20 END"]),
            ["10 PRINT 1", "20 SYSTEM"],
        )

    def test_rewrite_program_replaces_terminal_stop_after_colon(self) -> None:
        self.assertEqual(
            rewrite_program_for_system_exit(["10 PRINT 1: STOP"]),
            ["10 PRINT 1: SYSTEM"],
        )

    def test_rewrite_program_appends_system_when_last_line_is_not_terminal_end(self) -> None:
        self.assertEqual(
            rewrite_program_for_system_exit(["10 PRINT 1", "20 PRINT 2"]),
            ["10 PRINT 1", "20 PRINT 2", "21 SYSTEM"],
        )

    def test_rewrite_file_preserves_crlf_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source_path = Path(tmp) / "source.bas"
            destination_path = Path(tmp) / "dest.bas"
            source_path.write_bytes(b"10 PRINT 1\r\n20 END\r\n")

            rewrite_file(source_path, destination_path)

            self.assertEqual(destination_path.read_bytes(), b"10 PRINT 1\r\n20 SYSTEM\r\n")


if __name__ == "__main__":
    unittest.main()
