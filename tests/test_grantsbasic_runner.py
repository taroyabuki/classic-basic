from __future__ import annotations

import os
import pty
import select
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
ROM_PATH = ROOT_DIR / "downloads" / "grants-basic" / "rom.bin"


def _runtime_env() -> dict[str, str]:
    env = os.environ.copy()
    pythonpath = str(ROOT_DIR / "src")
    env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"
    return env


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


class GrantsBasicRunnerTests(unittest.TestCase):
    def _spawn_pty(self, command: list[str]) -> tuple[subprocess.Popen[bytes], int]:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            command,
            cwd=ROOT_DIR,
            env=_runtime_env(),
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)
        return proc, master_fd

    def test_interactive_launch_exits_on_ctrl_d(self) -> None:
        if not ROM_PATH.is_file():
            self.skipTest(f"ROM not available: {ROM_PATH}")

        proc, master_fd = self._spawn_pty(["bash", "run/grantsbasic.sh"])
        try:
            output = _read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"Z80 BASIC", output)
        self.assertIn(b"\r\nMemory top? ", output)
        self.assertIn(b"\r\nZ80 BASIC Ver 4.7b", output)

    def test_file_launch_loads_program_and_runs_after_manual_run(self) -> None:
        if not ROM_PATH.is_file():
            self.skipTest(f"ROM not available: {ROM_PATH}")

        proc, master_fd = self._spawn_pty(["bash", "run/grantsbasic.sh", "--file", "demo/grantsbasic.bas"])
        try:
            _read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"RUN\r")
            output = _read_until(master_fd, b"PI", timeout=30)
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

    def test_interactive_backspace_renders_erase_sequence(self) -> None:
        if not ROM_PATH.is_file():
            self.skipTest(f"ROM not available: {ROM_PATH}")

        proc, master_fd = self._spawn_pty(["bash", "run/grantsbasic.sh"])
        try:
            _read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"AB")
            _read_until(master_fd, b"AB", timeout=10)
            os.write(master_fd, b"\x7f")
            output = _read_until(master_fd, b"\x08 \x08", timeout=10)
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"\x08 \x08", output)

    def test_interactive_at_uses_rom_line_kill_behavior(self) -> None:
        if not ROM_PATH.is_file():
            self.skipTest(f"ROM not available: {ROM_PATH}")

        proc, master_fd = self._spawn_pty(["bash", "run/grantsbasic.sh"])
        try:
            _read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"@A")
            output = _read_until(master_fd, b"@\r\nA", timeout=10)
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"@\r\nA", output)

    def test_run_file_rejects_programs_that_request_input(self) -> None:
        if not ROM_PATH.is_file():
            self.skipTest(f"ROM not available: {ROM_PATH}")

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "input.bas"
            program.write_text('10 INPUT "N";A\n20 END\n', encoding="ascii")

            result = subprocess.run(
                ["bash", "run/grantsbasic.sh", "--run", "--file", str(program)],
                cwd=ROOT_DIR,
                env=_runtime_env(),
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("interactive input", result.stderr)


if __name__ == "__main__":
    unittest.main()
