from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "src"))

import runcpm_batch_exit


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

    def test_main_returns_130_on_keyboard_interrupt(self) -> None:
        with (
            mock.patch(
                "runcpm_batch_exit._parse_args",
                return_value=mock.Mock(
                    runtime="runtime",
                    prompt="A0>",
                    exit_command="EXIT\r",
                    timeout="",
                    intermediate_prompt="",
                    intermediate_command="",
                    output_filter="",
                ),
            ),
            mock.patch("runcpm_batch_exit.run_batch", side_effect=KeyboardInterrupt),
        ):
            result = runcpm_batch_exit.main()

        self.assertEqual(result, 130)

    def test_sends_intermediate_command_before_final_exit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            command_log = runtime_dir / "command.log"

            _write_executable(
                runtime_dir / "RunCPM",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf 'BASIC-85 Rev. 5.29 [CP/M Version]\\r\\n'
                printf 'PROGRAM OUTPUT\\r\\n'
                printf 'Ok\\r\\n'
                IFS= read -r -d $'\\r' line1
                printf '%s\\n' "$line1" > {command_log!s}
                printf 'A0>'
                IFS= read -r -d $'\\r' line2
                printf '%s\\n' "$line2" >> {command_log!s}
                """,
            )

            result = subprocess.run(
                [
                    "python3",
                    str(ROOT_DIR / "src/runcpm_batch_exit.py"),
                    "--runtime",
                    str(runtime_dir),
                    "--intermediate-prompt",
                    "Ok",
                    "--intermediate-command",
                    "SYSTEM",
                ],
                cwd=ROOT_DIR,
                env={"CLASSIC_BASIC_RUNCPM_BATCH_NO_PTY": "1"},
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "BASIC-85 Rev. 5.29 [CP/M Version]\nPROGRAM OUTPUT\n")
            self.assertEqual(command_log.read_text(encoding="ascii"), "SYSTEM\nEXIT\n")

    def test_accepts_a_prompt_as_fallback_for_a0_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            command_log = runtime_dir / "command.log"

            _write_executable(
                runtime_dir / "RunCPM",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf 'PROGRAM OUTPUT\\r\\n'
                printf 'A>'
                IFS= read -r -d $'\\r' line
                printf '%s\\n' "$line" > {command_log!s}
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
            self.assertEqual(result.stdout, "PROGRAM OUTPUT\n")
            self.assertEqual(command_log.read_text(encoding="ascii").strip(), "EXIT")

    def test_basic80_output_filter_removes_startup_and_shutdown_noise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            command_log = runtime_dir / "command.log"

            _write_executable(
                runtime_dir / "RunCPM",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '\\033[H\\033[2J'
                printf 'RunCPM Version 6.9 (CCP-INTERNAL v3.3)\\r\\n'
                printf 'BASIC-85 Rev. 5.29\\r\\n'
                printf '[CP/M Version]\\r\\n'
                printf '39224 Bytes free\\r\\n'
                printf 'COMMON SAMPLE\\r\\n'
                printf 'COUNT 1\\r\\n'
                printf 'Ok\\r\\n'
                IFS= read -r -d $'\\r' line1
                printf '%s\\n' "$line1" > {command_log!s}
                printf 'SYSTEM\\r\\n'
                printf 'RunCPM Version 6.9 (CCP-INTERNAL v3.3)\\r\\n'
                printf 'A0>'
                IFS= read -r -d $'\\r' line2
                printf '%s\\n' "$line2" >> {command_log!s}
                """,
            )

            result = subprocess.run(
                [
                    "python3",
                    str(ROOT_DIR / "src/runcpm_batch_exit.py"),
                    "--runtime",
                    str(runtime_dir),
                    "--intermediate-prompt",
                    "Ok",
                    "--intermediate-command",
                    "SYSTEM",
                    "--output-filter",
                    "basic80",
                ],
                cwd=ROOT_DIR,
                env={"CLASSIC_BASIC_RUNCPM_BATCH_NO_PTY": "1"},
                capture_output=True,
                text=True,
                timeout=10,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "COMMON SAMPLE\nCOUNT 1\n")
            self.assertEqual(command_log.read_text(encoding="ascii"), "SYSTEM\nEXIT\n")

    def test_aborts_and_terminates_child_when_parent_disappears(self) -> None:
        class FakeProc:
            def __init__(self) -> None:
                self.returncode: int | None = None

            def poll(self) -> int | None:
                return self.returncode

            def wait(self, timeout: float | None = None) -> int:
                if self.returncode is None:
                    self.returncode = 0
                return self.returncode

            def terminate(self) -> None:
                self.returncode = -signal.SIGTERM

            def kill(self) -> None:
                self.returncode = -signal.SIGKILL

        import signal

        read_fd, write_fd = os.pipe()
        proc = FakeProc()
        try:
            with (
                mock.patch.object(runcpm_batch_exit, "_spawn_child", return_value=(proc, read_fd, None)),
                mock.patch.object(runcpm_batch_exit.os, "getppid", side_effect=[1000, 1001]),
            ):
                result = runcpm_batch_exit.run_batch(Path("/tmp/runtime"), "A0>", "EXIT\r", None)
        finally:
            os.close(write_fd)

        self.assertEqual(result, 125)
        self.assertEqual(proc.returncode, -signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
