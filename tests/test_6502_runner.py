from __future__ import annotations

import io
import os
import pty
import select
import signal
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from z80_basic.osi_basic import StreamingFileRunOutput


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER = ROOT_DIR / "run/6502.sh"


def _read_until(master_fd: int, needle: bytes, *, timeout: float) -> bytes:
    end = time.time() + timeout
    output = bytearray()
    while time.time() < end:
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if not ready:
            continue
        chunk = os.read(master_fd, 4096)
        if not chunk:
            break
        output.extend(chunk)
        if needle in output:
            return bytes(output)
    raise AssertionError(f"timed out waiting for {needle!r}; got tail {bytes(output[-200:])!r}")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class Basic6502RunnerTests(unittest.TestCase):
    def test_streaming_file_run_output_preserves_basic_numeric_padding(self) -> None:
        stream = io.StringIO()
        output = StreamingFileRunOutput(stream)

        output.write(b"RUN\r 0 \r 0 \rOK\r")
        output.finish()

        self.assertEqual(stream.getvalue(), " 0 \n 0 \n")

    def test_no_args_starts_interactive_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                """,
            )

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", str(RUNNER)],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                (
                    f"-m z80_basic.osi_basic --rom {ROOT_DIR / 'third_party/msbasic/osi.bin'} "
                    f"--memory-size 8191 --terminal-width 80 --max-steps 5000000000 "
                    "--interactive"
                ),
            )

    def test_file_flag_loads_program_interactively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                """,
            )

            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", str(RUNNER), "--file", str(program)],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                (
                    f"-m z80_basic.osi_basic --rom {ROOT_DIR / 'third_party/msbasic/osi.bin'} "
                    f"--memory-size 8191 --terminal-width 80 --max-steps 5000000000 "
                    f"--interactive {program}"
                ),
            )

    def test_short_file_flag_loads_program_interactively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                """,
            )

            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", str(RUNNER), "-f", str(program)],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                (
                    f"-m z80_basic.osi_basic --rom {ROOT_DIR / 'third_party/msbasic/osi.bin'} "
                    f"--memory-size 8191 --terminal-width 80 --max-steps 5000000000 "
                    f"--interactive {program}"
                ),
            )

    def test_run_flag_executes_program_in_batch_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                """,
            )

            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", str(RUNNER), "--run", "--file", str(program)],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                (
                    f"-m z80_basic.osi_basic --rom {ROOT_DIR / 'third_party/msbasic/osi.bin'} "
                    f"--memory-size 8191 --terminal-width 80 --max-steps 5000000000 "
                    f"{program}"
                ),
            )

    def test_short_run_flag_executes_program_in_batch_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                """,
            )

            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                ["bash", str(RUNNER), "-r", "-f", str(program)],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                (
                    f"-m z80_basic.osi_basic --rom {ROOT_DIR / 'third_party/msbasic/osi.bin'} "
                    f"--memory-size 8191 --terminal-width 80 --max-steps 5000000000 "
                    f"{program}"
                ),
            )

    def test_interactive_ctrl_c_breaks_running_program_and_ctrl_d_exits(self) -> None:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            ["bash", str(RUNNER)],
            cwd=ROOT_DIR,
            env=os.environ.copy(),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)

        try:
            _read_until(master_fd, b"OK", timeout=30)
            os.write(master_fd, b"10 GOTO 10\rRUN\r")
            _read_until(master_fd, b"RUN", timeout=10)
            time.sleep(0.1)
            os.write(master_fd, b"\x03")
            output = _read_until(master_fd, b"BREAK", timeout=10)
            output += _read_until(master_fd, b"OK", timeout=10)
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"BREAK", output)
        self.assertIn(b"OK", output)


if __name__ == "__main__":
    unittest.main()
