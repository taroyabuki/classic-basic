from __future__ import annotations

import io
import os
import pty
import re
import select
import signal
import stat
import subprocess
import tempfile
import termios
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch


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
    def test_direct_statements_preserve_state_across_commands(self) -> None:
        import gwbasic_interactive

        shell = gwbasic_interactive.GwBasicTextShell(
            archive_path="archive.7z",
            runtime_dir="/tmp/runtime",
            home_dir="/tmp/home",
            file_path=None,
        )

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            del kwargs
            source_path = Path(args[-1])
            source_text = source_path.read_text(encoding="ascii")
            begin_match = re.search(r'PRINT "(__CLASSIC_BASIC_BEGIN_[0-9]+__)"', source_text)
            end_match = re.search(r'PRINT "(__CLASSIC_BASIC_END_[0-9]+__)"', source_text)
            begin_marker = begin_match.group(1) if begin_match else ""
            end_marker = end_match.group(1) if end_match else ""
            if "PRINT A" in source_text:
                self.assertIn("A=4*ATN(1)", source_text)
                stdout_text = f"{begin_marker}\n 3.14159265358979 \n{end_marker}\n"
            else:
                stdout_text = f"{begin_marker}\n{end_marker}\n"
            return subprocess.CompletedProcess(
                args=["bash", "run/gwbasic.sh"],
                returncode=0,
                stdout=stdout_text,
                stderr="",
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("dos_text_shell.subprocess.run", side_effect=fake_run), patch("sys.stdout", stdout), patch(
            "sys.stderr", stderr
        ):
            shell._handle_command("A=4*ATN(1)")
            shell._handle_command("PRINT A")

        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("3.14159265358979", stdout.getvalue())
        self.assertNotIn("__CLASSIC_BASIC_BEGIN_", stdout.getvalue())
        self.assertEqual(shell.direct_history, ["A=4*ATN(1)", "PRINT A"])

    def test_plain_interactive_launch_uses_text_shell_and_exits_on_ctrl_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, _ = self._make_test_env(temp_path)
            env["FAKE_DOS_CAPTURE_CONTENT"] = " 4 \r\n"

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
                rows=20,
                cols=60,
            )

            try:
                startup = _read_until(master_fd, b"Ctrl-D exits.", timeout=10)
                os.write(master_fd, b"PRINT 2+2\r")
                output = _read_until(master_fd, b"4\r\n", timeout=10)
                os.write(master_fd, b"\x04")
                proc.wait(timeout=10)
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)

            self.assertEqual(proc.returncode, 0)
            self.assertIn(b"GW-BASIC text shell", startup)
            self.assertIn(b"4\r\n", output)
            self.assertNotIn(b"GW-BASIC> ", startup + output)
            self.assertNotIn(b"Microsoft", output)
            self.assertNotIn(b"640K", output)

    def test_loaded_interactive_launch_runs_program_then_exits_on_ctrl_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            env, runtime_dir = self._make_test_env(temp_path)
            env["FAKE_DOS_CAPTURE_CONTENT"] = "PROGRAM OUTPUT\r\n"
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
                startup = _read_until(master_fd, b"Loaded 2 line(s)", timeout=10)
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
            self.assertIn(b"Loaded 2 line(s)", startup)
            self.assertIn(b"PROGRAM OUTPUT", output)
            self.assertNotIn(b"GW-BASIC> ", startup + output)
            self.assertNotIn(b"Microsoft", output)
            self.assertEqual(
                (runtime_dir / "drive_c" / "RUNFILE.BAS").read_bytes(),
                b'10 PRINT "HELLO"\r\n20 END\r\n',
            )

    def test_direct_statement_tolerates_non_utf8_batch_output(self) -> None:
        import gwbasic_interactive

        shell = gwbasic_interactive.GwBasicTextShell(
            archive_path="archive.7z",
            runtime_dir="/tmp/runtime",
            home_dir="/tmp/home",
            file_path=None,
        )

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
            del args, kwargs
            return subprocess.CompletedProcess(
                args=["bash", "run/gwbasic.sh"],
                returncode=0,
                stdout=b"__CLASSIC_BASIC_BEGIN_1__\n\xff4\n__CLASSIC_BASIC_END_1__\n",
                stderr=b"",
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("dos_text_shell.subprocess.run", side_effect=fake_run), patch("sys.stdout", stdout), patch(
            "sys.stderr", stderr
        ):
            shell._handle_command("PRINT 2+2")

        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("4", stdout.getvalue())
        self.assertNotIn("__CLASSIC_BASIC_BEGIN_", stdout.getvalue())

    def test_direct_statement_strips_trailing_ok_noise(self) -> None:
        import gwbasic_interactive

        shell = gwbasic_interactive.GwBasicTextShell(
            archive_path="archive.7z",
            runtime_dir="/tmp/runtime",
            home_dir="/tmp/home",
            file_path=None,
        )

        def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            del args, kwargs
            return subprocess.CompletedProcess(
                args=["bash", "run/gwbasic.sh"],
                returncode=0,
                stdout="__CLASSIC_BASIC_BEGIN_1__\n15\nOk\uFFFD\n__CLASSIC_BASIC_END_1__\n",
                stderr="",
            )

        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("dos_text_shell.subprocess.run", side_effect=fake_run), patch("sys.stdout", stdout), patch(
            "sys.stderr", stderr
        ):
            shell._handle_command("PRINT 10+5")

        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(stdout.getvalue(), "15\nOk\n")

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
        env["PYTHONPATH"] = str(ROOT_DIR / "src")
        return env, runtime_dir

    def _spawn_pty(
        self,
        *,
        env: dict[str, str],
        args: list[str],
        rows: int = 25,
        cols: int = 80,
    ) -> tuple[subprocess.Popen[bytes], int]:
        master_fd, slave_fd = pty.openpty()
        self._set_winsize(slave_fd, rows, cols)
        self._set_winsize(master_fd, rows, cols)
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

    def _set_winsize(self, fd: int, rows: int, cols: int) -> None:
        try:
            termios.tcsetwinsize(fd, (rows, cols))
        except (AttributeError, OSError):
            pass

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
            if [[ -n "${FAKE_DOS_CAPTURE_CONTENT:-}" ]]; then
              capture_name=''
              if [[ -n "${drive_c}" && -f "${drive_c}/${dos_command}.BAT" ]]; then
                capture_name=$(grep -oi '> [A-Z0-9.]*' "${drive_c}/${dos_command}.BAT" | head -1 | cut -c3-)
              fi
              if [[ -n "${capture_name}" ]]; then
                printf '%s' "${FAKE_DOS_CAPTURE_CONTENT}" >"${drive_c}/${capture_name}"
              fi
            fi
            exit 0
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
