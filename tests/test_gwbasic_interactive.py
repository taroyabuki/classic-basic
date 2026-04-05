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


class GwBasicInteractiveTests(unittest.TestCase):
    def test_plain_interactive_launch_exits_on_ctrl_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)

            proc, master_fd = self._spawn_pty(
                env=env,
                args=[
                    "bash",
                    "run/gwbasic.sh",
                    "--runtime",
                    str(temp_path / "runtime"),
                    "--home",
                    str(temp_path / "home"),
                ],
            )

            try:
                output = _read_until(master_fd, b"READY", timeout=10)
                os.write(master_fd, b"\x04")
                proc.wait(timeout=10)
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)

            self.assertEqual(proc.returncode, 0)
            self.assertIn(b"READY", output)

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
                    "run/gwbasic.sh",
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
        downloads_dir = temp_path / "downloads" / "gwbasic"
        downloads_dir.mkdir(parents=True)
        (downloads_dir / "gwbasic-3.23.7z").write_bytes(b"archive")
        (downloads_dir / "GWBASIC.EXE").write_bytes(b"exe")

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        env["CLASSIC_BASIC_DOSEMU_SEM_SHIM"] = "off"
        env["CLASSIC_BASIC_GWBASIC_ARCHIVE"] = str(downloads_dir / "gwbasic-3.23.7z")
        env["CLASSIC_BASIC_GWBASIC_EXE"] = str(downloads_dir / "GWBASIC.EXE")
        env["FAKE_GWBASIC_INTERACTIVE_LOG"] = str(temp_path / "interactive.log")
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
            """#!/usr/bin/env bash
            set -euo pipefail
            drive_c=''
            dos_command=''
            while [[ $# -gt 0 ]]; do
              case "$1" in
                --Fdrive_c)
                  drive_c="$2"
                  shift 2
                  ;;
                -E)
                  dos_command="$2"
                  shift 2
                  ;;
                *)
                  shift
                  ;;
              esac
            done
            printf '%s\\n' "${dos_command}" >>"${FAKE_GWBASIC_INTERACTIVE_LOG}"
            if [[ "${dos_command}" == "gwbasic" ]]; then
              printf 'READY\\r\\n'
            elif [[ "${dos_command}" == "gwbasic RUNFILE.BAS" ]]; then
              printf 'LOADED\\r\\n'
            fi
            while IFS= read -r line; do
              if [[ "${line}" == "RUN" ]]; then
                printf 'PROGRAM OUTPUT\\r\\n'
              fi
            done
            exit 0
            """,
        )
        _write_executable(
            bin_dir / "script",
            """#!/usr/bin/env bash
            set -euo pipefail
            cmd_arg=''
            while [[ $# -gt 0 ]]; do
              case "$1" in
                -c)
                  cmd_arg="$2"
                  shift 2
                  ;;
                *)
                  shift
                  ;;
              esac
            done
            exec bash -lc "${cmd_arg}"
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
