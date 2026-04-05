from __future__ import annotations

import os
import pty
import select
import signal
import stat
import subprocess
import tempfile
import termios
import textwrap
import time
import tty
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _read_until(master_fd: int, needle: bytes, *, timeout: float) -> bytes:
    deadline = time.time() + timeout
    output = bytearray()
    while time.time() < deadline:
        ready, _, _ = select.select([master_fd], [], [], 0.1)
        if not ready:
            continue
        try:
            chunk = os.read(master_fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
        if needle in output:
            return bytes(output)
    raise AssertionError(f"timed out waiting for {needle!r}; got tail {bytes(output[-200:])!r}")


class QBasicInteractiveTests(unittest.TestCase):
    def test_plain_interactive_launch_exits_on_ctrl_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)

            proc, master_fd = self._spawn_pty(
                env=env,
                args=[
                    "bash",
                    "run/qbasic.sh",
                    "--runtime",
                    str(temp_path / "runtime"),
                    "--home",
                    str(temp_path / "home"),
                ],
            )

            try:
                output = _read_until(master_fd, b"Immediate", timeout=10)
                os.write(master_fd, b"\x04")
                proc.wait(timeout=10)
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)

            self.assertEqual(proc.returncode, 0)
            self.assertIn(b"Immediate", output)

    def test_loaded_interactive_launch_runs_program_then_exits_on_ctrl_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, runtime_dir = self._make_test_env(temp_path)
            program_path = temp_path / "demo.bas"
            program_path.write_bytes(b'10 PRINT "HELLO"\r20 END\r')

            proc, master_fd = self._spawn_pty(
                env=env,
                args=[
                    "bash",
                    "run/qbasic.sh",
                    "--runtime",
                    str(runtime_dir),
                    "--home",
                    str(temp_path / "home"),
                    "--file",
                    str(program_path),
                ],
            )

            try:
                _read_until(master_fd, b"LOADED", timeout=10)
                os.write(master_fd, b"RUN\r")
                output = _read_until(master_fd, b"PROGRAM OUTPUT", timeout=10)
                os.write(master_fd, b"\x04")
                proc.wait(timeout=10)
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)

            self.assertEqual(proc.returncode, 0)
            self.assertIn(b"PROGRAM OUTPUT", output)
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 PRINT "HELLO"\r\n20 END\r\n',
            )

    def _make_test_env(self, temp_path: Path) -> tuple[dict[str, str], Path]:
        bin_dir = temp_path / "bin"
        runtime_dir = temp_path / "runtime"
        bin_dir.mkdir()

        self._write_fake_commands(bin_dir)
        downloads_dir = temp_path / "downloads" / "qbasic"
        downloads_dir.mkdir(parents=True)
        (downloads_dir / "qbasic-1.1.zip").write_bytes(b"archive")
        (downloads_dir / "QBASIC.EXE").write_bytes(b"exe")

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["CLASSIC_BASIC_DOSEMU_SEM_SHIM"] = "off"
        env["CLASSIC_BASIC_QBASIC_ARCHIVE"] = str(downloads_dir / "qbasic-1.1.zip")
        env["CLASSIC_BASIC_QBASIC_EXE"] = str(downloads_dir / "QBASIC.EXE")
        return env, runtime_dir

    def _spawn_pty(
        self,
        *,
        env: dict[str, str],
        args: list[str],
    ) -> tuple[subprocess.Popen[bytes], int]:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            args,
            cwd=ROOT_DIR,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)
        return proc, master_fd

    def _write_fake_commands(self, bin_dir: Path) -> None:
        _write_executable(
            bin_dir / "dosemu",
            """#!/usr/bin/env python3
import os
import sys
import termios
import tty

dos_command = ""
args = sys.argv[1:]
index = 0
while index < len(args):
    if args[index] == "-E":
        dos_command = args[index + 1]
        index += 2
    else:
        index += 1

if dos_command == "qbasic RUNFILE.BAS":
    sys.stdout.write("LOADED\\r\\n")
sys.stdout.write("Immediate\\r\\n")
sys.stdout.flush()

original_attrs = termios.tcgetattr(sys.stdin.fileno())
tty.setraw(sys.stdin.fileno())

buffer = bytearray()
try:
    while True:
        chunk = os.read(sys.stdin.fileno(), 1)
        if not chunk:
            break
        buffer.extend(chunk)
        upper = bytes(buffer).upper()
        if b"RUN\\r" in upper:
            sys.stdout.write("PROGRAM OUTPUT\\r\\n")
            sys.stdout.flush()
            buffer.clear()
        elif b"SYSTEM\\r" in upper:
            break
finally:
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original_attrs)

sys.exit(0)
""",
        )
        for command_name in ("curl", "7z", "mcopy"):
            _write_executable(
                bin_dir / command_name,
                f"""#!/usr/bin/env bash
                set -euo pipefail
                echo "{command_name} should not be called in interactive tests" >&2
                exit 99
                """,
            )


if __name__ == "__main__":
    unittest.main()
