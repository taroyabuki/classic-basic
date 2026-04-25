from __future__ import annotations

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


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER = ROOT_DIR / "run/nbasic.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class NBasicRunnerTests(unittest.TestCase):
    def _runtime_env(self, extra_path: Path | None = None) -> dict[str, str]:
        env = os.environ.copy()
        pythonpath = str(ROOT_DIR / "src")
        env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"
        env["CLASSIC_BASIC_PYTHON"] = "python3"
        if extra_path is not None:
            env["PATH"] = f"{extra_path}:{env['PATH']}"
        return env

    def _spawn_pty(self, command: list[str]) -> tuple[subprocess.Popen[bytes], int]:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            command,
            cwd=ROOT_DIR,
            env=self._runtime_env(),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)
        return proc, master_fd

    def _read_until(self, master_fd: int, needle: bytes, *, timeout: float) -> bytes:
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

    def test_plain_launch_uses_interactive_terminal_route(self) -> None:
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

            result = subprocess.run(
                ["bash", str(RUNNER)],
                cwd=ROOT_DIR,
                env=self._runtime_env(bin_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                "-m pc8001_terminal",
            )

    def test_run_flag_uses_rom_batch_route(self) -> None:
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

            result = subprocess.run(
                ["bash", str(RUNNER), "--run", "--file", str(program)],
                cwd=ROOT_DIR,
                env=self._runtime_env(bin_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m pc8001_terminal --cols 80 --vram-stride 0xA0 {program}",
            )

    def test_runner_prefers_pypy3_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "pypy3.log"
            _write_executable(
                bin_dir / "pypy3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                """,
            )
            _write_executable(
                bin_dir / "python3",
                """#!/usr/bin/env bash
                set -euo pipefail
                echo "python3 should not be used" >&2
                exit 1
                """,
            )

            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            env = self._runtime_env(bin_dir)
            env.pop("CLASSIC_BASIC_PYTHON", None)

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
                f"-m pc8001_terminal --cols 80 --vram-stride 0xA0 {program}",
            )

    def test_short_run_flag_uses_rom_batch_route(self) -> None:
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

            result = subprocess.run(
                ["bash", str(RUNNER), "-r", "-f", str(program)],
                cwd=ROOT_DIR,
                env=self._runtime_env(bin_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m pc8001_terminal --cols 80 --vram-stride 0xA0 {program}",
            )

    def test_run_flag_respects_explicit_cols_and_vram_stride(self) -> None:
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

            result = subprocess.run(
                [
                    "bash",
                    str(RUNNER),
                    "--run",
                    "--cols",
                    "64",
                    "--vram-stride",
                    "0x80",
                    "--file",
                    str(program),
                ],
                cwd=ROOT_DIR,
                env=self._runtime_env(bin_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m pc8001_terminal --cols 64 --vram-stride 0x80 {program}",
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

            result = subprocess.run(
                ["bash", str(RUNNER), "--file", str(program)],
                cwd=ROOT_DIR,
                env=self._runtime_env(bin_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m pc8001_terminal --interactive {program}",
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

            result = subprocess.run(
                ["bash", str(RUNNER), "-f", str(program)],
                cwd=ROOT_DIR,
                env=self._runtime_env(bin_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m pc8001_terminal --interactive {program}",
            )

    def test_pc8001_flags_force_rom_route_for_keys(self) -> None:
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

            result = subprocess.run(
                ["bash", str(RUNNER), "--trace", "--keys", r"PRINT 12\r"],
                cwd=ROOT_DIR,
                env=self._runtime_env(bin_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                "-m pc8001_terminal --trace --keys PRINT 12\\r",
            )

    def test_run_requires_file(self) -> None:
        result = subprocess.run(
            ["bash", str(RUNNER), "--run"],
            cwd=ROOT_DIR,
            env=self._runtime_env(),
            capture_output=True,
            text=True,
            timeout=20,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("error: --run requires --file PROGRAM.bas", result.stderr)

    def test_interactive_launch_exits_on_ctrl_d(self) -> None:
        rom_path = ROOT_DIR / "downloads" / "pc8001" / "N80_11.rom"
        if not rom_path.is_file():
            self.skipTest(f"ROM not available: {rom_path}")

        proc, master_fd = self._spawn_pty(["bash", str(RUNNER)])
        try:
            output = self._read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"NEC PC-8001 BASIC", output)

    def test_file_launch_loads_program_and_runs_after_manual_run(self) -> None:
        rom_path = ROOT_DIR / "downloads" / "pc8001" / "N80_11.rom"
        if not rom_path.is_file():
            self.skipTest(f"ROM not available: {rom_path}")

        proc, master_fd = self._spawn_pty(["bash", str(RUNNER), "--file", "demo/nbasic.bas"])
        try:
            self._read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"RUN\r")
            output = self._read_until(master_fd, b"HELLO, WORLD", timeout=30)
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"HELLO, WORLD", output)
        self.assertIn(b"INTEGER 14", output)
        self.assertIn(b"PI", output)

    def test_run_file_rejects_programs_that_request_input(self) -> None:
        rom_path = ROOT_DIR / "downloads" / "pc8001" / "N80_11.rom"
        if not rom_path.is_file():
            self.skipTest(f"ROM not available: {rom_path}")

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "input.bas"
            program.write_text('10 INPUT "N";A\n20 PRINT A\n', encoding="ascii")

            result = subprocess.run(
                ["bash", str(RUNNER), "--run", "--file", str(program)],
                cwd=ROOT_DIR,
                env=self._runtime_env(),
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("interactive program input", result.stderr)
