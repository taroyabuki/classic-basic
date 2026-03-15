from __future__ import annotations

import stat
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class RunCpmBatchExitTests(unittest.TestCase):
    def test_exits_after_prompt_without_printing_shutdown_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            command_log = runtime_dir / "command.log"

            _write_executable(
                runtime_dir / "RunCPM",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf 'RunCPM banner\\r\\n'
                printf 'PROGRAM OUTPUT\\r\\n'
                printf 'A0>'
                IFS= read -r -d $'\\r' line
                printf '%s\\n' "$line" > {command_log!s}
                printf 'EXIT\\r\\n'
                printf 'Terminating RunCPM.\\r\\n'
                """,
            )

            result = subprocess.run(
                [
                    "python3",
                    str(ROOT_DIR / "src/runcpm_batch_exit.py"),
                    "--runtime",
                    str(runtime_dir),
                ],
                cwd=ROOT_DIR,
                env={"CLASSIC_BASIC_RUNCPM_BATCH_NO_PTY": "1"},
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "RunCPM banner\nPROGRAM OUTPUT\n")
            self.assertEqual(command_log.read_text(encoding="ascii").strip(), "EXIT")

    def test_passes_through_child_exit_without_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)

            _write_executable(
                runtime_dir / "RunCPM",
                """#!/usr/bin/env bash
                set -euo pipefail
                printf 'NO PROMPT\\n'
                exit 3
                """,
            )

            result = subprocess.run(
                [
                    "python3",
                    str(ROOT_DIR / "src/runcpm_batch_exit.py"),
                    "--runtime",
                    str(runtime_dir),
                ],
                cwd=ROOT_DIR,
                env={"CLASSIC_BASIC_RUNCPM_BATCH_NO_PTY": "1"},
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 3)
            self.assertEqual(result.stdout, "NO PROMPT\n")


if __name__ == "__main__":
    unittest.main()
