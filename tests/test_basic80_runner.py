from __future__ import annotations

import os
import pty
import select
import signal
import shutil
import stat
import subprocess
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def _write_executable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _copy_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)


def _copy_repo_files(root: Path, relpaths: list[str]) -> None:
    for relpath in relpaths:
        _copy_file(ROOT_DIR / relpath, root / relpath)


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


def _prepare_fake_runcpm_tree(root: Path) -> None:
    upstream = root / "third_party/RunCPM"
    (upstream / ".git").mkdir(parents=True, exist_ok=True)
    (upstream / "CCP").mkdir(parents=True, exist_ok=True)
    (upstream / "RunCPM").mkdir(parents=True, exist_ok=True)
    (upstream / "DISK").mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(upstream / "DISK/A0.ZIP", "w") as archive:
        archive.writestr("A/0/README.TXT", "demo\n")

    (upstream / "CCP/CCP-TEST").write_text("ccp\n", encoding="ascii")
    _write_executable(
        upstream / "RunCPM/RunCPM",
        """#!/usr/bin/env bash
        set -euo pipefail
        if [[ -n "${RUNCPM_AUTOEXEC_LOG:-}" && -f AUTOEXEC.TXT ]]; then
          cp AUTOEXEC.TXT "${RUNCPM_AUTOEXEC_LOG}"
        fi
        if [[ -n "${RUNCPM_STDIN_LOG:-}" ]]; then
          cat > "${RUNCPM_STDIN_LOG}"
        else
          cat >/dev/null
        fi
        """,
    )


class Basic80RunnerTests(unittest.TestCase):
    maxDiff = None

    def test_plain_interactive_launch_exits_on_ctrl_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "run/basic80.sh",
                    "scripts/basic-file-common.sh",
                    "scripts/runcpm-common.sh",
                    "src/basic80_interactive.py",
                    "src/runcpm_batch_exit.py",
                ],
            )
            _prepare_fake_runcpm_tree(temp_path)
            self._write_interactive_fake_runcpm(temp_path / "third_party/RunCPM/RunCPM/RunCPM")

            bin_dir = temp_path / "bin"
            _write_executable(bin_dir / "make", "#!/usr/bin/env bash\nexit 0\n")

            runtime_dir = temp_path / "runtime"
            mbasic_path = temp_path / "MBASIC.COM"
            mbasic_path.write_bytes(b"mbasic")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["PYTHONPATH"] = str(temp_path / "src")

            proc, master_fd = self._spawn_pty(
                env=env,
                args=[
                    "bash",
                    "run/basic80.sh",
                    "--runtime",
                    str(runtime_dir),
                    "--mbasic",
                    str(mbasic_path),
                ],
                cwd=temp_path,
            )

            try:
                output = _read_until(master_fd, b"BASIC-85", timeout=10)
                os.write(master_fd, b"\x04")
                proc.wait(timeout=10)
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)

            self.assertEqual(proc.returncode, 0)
            self.assertIn(b"BASIC-85", output)

    def test_file_flag_loaded_interactive_launch_exits_on_ctrl_d(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "run/basic80.sh",
                    "scripts/basic-file-common.sh",
                    "scripts/runcpm-common.sh",
                    "src/basic80_interactive.py",
                    "src/runcpm_batch_exit.py",
                ],
            )
            _prepare_fake_runcpm_tree(temp_path)
            self._write_interactive_fake_runcpm(temp_path / "third_party/RunCPM/RunCPM/RunCPM")

            bin_dir = temp_path / "bin"
            _write_executable(bin_dir / "make", "#!/usr/bin/env bash\nexit 0\n")

            runtime_dir = temp_path / "runtime"
            mbasic_path = temp_path / "MBASIC.COM"
            mbasic_path.write_bytes(b"mbasic")
            program_path = temp_path / "prog.bas"
            program_path.write_bytes(b"10 PRINT 1\r20 END\r")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["PYTHONPATH"] = str(temp_path / "src")

            proc, master_fd = self._spawn_pty(
                env=env,
                args=[
                    "bash",
                    "run/basic80.sh",
                    "--runtime",
                    str(runtime_dir),
                    "--mbasic",
                    str(mbasic_path),
                    "--file",
                    str(program_path),
                ],
                cwd=temp_path,
            )

            try:
                output = _read_until(master_fd, b'LOADED RUNFILE.BAS', timeout=10)
                os.write(master_fd, b"\x04")
                proc.wait(timeout=10)
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)

            self.assertEqual(proc.returncode, 0)
            self.assertIn(b"LOADED RUNFILE.BAS", output)
            self.assertEqual(
                (runtime_dir / "A/0/RUNFILE.BAS").read_bytes(),
                b"10 PRINT 1\r\n20 END\r\n",
            )

    def test_file_flag_loads_program_interactively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "run/basic80.sh",
                    "scripts/basic-file-common.sh",
                    "scripts/runcpm-common.sh",
                    "src/runcpm_batch_exit.py",
                ],
            )
            _prepare_fake_runcpm_tree(temp_path)

            bin_dir = temp_path / "bin"
            _write_executable(bin_dir / "make", "#!/usr/bin/env bash\nexit 0\n")

            runtime_dir = temp_path / "runtime"
            mbasic_path = temp_path / "MBASIC.COM"
            mbasic_path.write_bytes(b"mbasic")
            program_path = temp_path / "prog.bas"
            program_path.write_bytes(b"10 PRINT 1\r20 END\r",)
            stdin_log = temp_path / "stdin.log"
            autoexec_log = temp_path / "autoexec.log"

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["RUNCPM_STDIN_LOG"] = str(stdin_log)
            env["RUNCPM_AUTOEXEC_LOG"] = str(autoexec_log)

            result = subprocess.run(
                [
                    "bash",
                    "run/basic80.sh",
                    "--runtime",
                    str(runtime_dir),
                    "--mbasic",
                    str(mbasic_path),
                    "--file",
                    str(program_path),
                ],
                cwd=temp_path,
                env=env,
                input="RUN\r",
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(autoexec_log.read_text(encoding="ascii"), "MBASIC\n")
            self.assertEqual(stdin_log.read_bytes(), b'LOAD "RUNFILE.BAS"\rRUN\r')
            self.assertEqual(
                (runtime_dir / "A/0/RUNFILE.BAS").read_bytes(),
                b"10 PRINT 1\r\n20 END\r\n",
            )

    def test_file_flag_accepts_lf_crlf_and_cr_line_endings(self) -> None:
        cases = {
            "lf.bas": b"10 PRINT 1\n20 END\n",
            "crlf.bas": b"10 PRINT 1\r\n20 END\r\n",
            "cr.bas": b"10 PRINT 1\r20 END\r",
        }

        for name, source in cases.items():
            with self.subTest(path=name), tempfile.TemporaryDirectory() as tmp:
                temp_path = Path(tmp)
                _copy_repo_files(
                    temp_path,
                    [
                        "run/basic80.sh",
                        "scripts/basic-file-common.sh",
                        "scripts/runcpm-common.sh",
                        "src/runcpm_batch_exit.py",
                    ],
                )
                _prepare_fake_runcpm_tree(temp_path)

                bin_dir = temp_path / "bin"
                _write_executable(bin_dir / "make", "#!/usr/bin/env bash\nexit 0\n")

                runtime_dir = temp_path / "runtime"
                mbasic_path = temp_path / "MBASIC.COM"
                mbasic_path.write_bytes(b"mbasic")
                program_path = temp_path / name
                program_path.write_bytes(source)

                env = os.environ.copy()
                env["PATH"] = f"{bin_dir}:{env['PATH']}"

                result = subprocess.run(
                    [
                        "bash",
                        "run/basic80.sh",
                        "--runtime",
                        str(runtime_dir),
                        "--mbasic",
                        str(mbasic_path),
                        "--file",
                        str(program_path),
                    ],
                    cwd=temp_path,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    (runtime_dir / "A/0/RUNFILE.BAS").read_bytes(),
                    b"10 PRINT 1\r\n20 END\r\n",
                )

    def test_run_flag_uses_batch_exit_and_prints_stdout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            _copy_repo_files(
                temp_path,
                [
                    "run/basic80.sh",
                    "scripts/basic-file-common.sh",
                    "scripts/runcpm-common.sh",
                    "src/runcpm_batch_exit.py",
                ],
            )
            _prepare_fake_runcpm_tree(temp_path)

            bin_dir = temp_path / "bin"
            _write_executable(bin_dir / "make", "#!/usr/bin/env bash\nexit 0\n")
            batch_log = temp_path / "python3.log"
            autoexec_log = temp_path / "autoexec.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                if [[ "${{1:-}}" == "{temp_path / 'src/runcpm_batch_exit.py'}" ]]; then
                  printf '%s\\n' "$*" >"{batch_log}"
                  cat "$3/AUTOEXEC.TXT" >"{autoexec_log}"
                  printf 'PRINT OUTPUT\\n'
                  exit 0
                fi
                exec /usr/bin/python3 "$@"
                """,
            )

            runtime_dir = temp_path / "runtime"
            mbasic_path = temp_path / "MBASIC.COM"
            mbasic_path.write_bytes(b"mbasic")
            program_path = temp_path / "prog.bas"
            program_path.write_text("10 PRINT 42\n20 END\n", encoding="ascii")

            env = os.environ.copy()
            env["PATH"] = f"{bin_dir}:{env['PATH']}"

            result = subprocess.run(
                [
                    "bash",
                    "run/basic80.sh",
                    "--runtime",
                    str(runtime_dir),
                    "--mbasic",
                    str(mbasic_path),
                    "--run",
                    "--file",
                    str(program_path),
                ],
                cwd=temp_path,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "PRINT OUTPUT\n")
            self.assertEqual(
                batch_log.read_text(encoding="ascii").strip(),
                f"{temp_path / 'src/runcpm_batch_exit.py'} --runtime {runtime_dir}",
            )
            self.assertEqual(
                autoexec_log.read_text(encoding="ascii"),
                "MBASIC RUNFILE.BAS\n",
            )
            self.assertEqual(
                (runtime_dir / "A/0/RUNFILE.BAS").read_bytes(),
                b"10 PRINT 42\r\n20 END\r\n",
            )

    def _spawn_pty(
        self,
        *,
        env: dict[str, str],
        args: list[str],
        cwd: Path,
    ) -> tuple[subprocess.Popen[bytes], int]:
        master_fd, slave_fd = pty.openpty()
        proc = subprocess.Popen(
            args,
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            start_new_session=True,
        )
        os.close(slave_fd)
        return proc, master_fd

    def _write_interactive_fake_runcpm(self, path: Path) -> None:
        _write_executable(
            path,
            """#!/usr/bin/env python3
import os
import sys
import termios
import tty

sys.stdout.write("BASIC-85 Rev. 5.29 [CP/M Version]\\r\\nOk\\r\\n")
sys.stdout.flush()

original_attrs = termios.tcgetattr(sys.stdin.fileno())
tty.setraw(sys.stdin.fileno())
buffer = bytearray()
in_basic = True

try:
    while True:
        chunk = os.read(sys.stdin.fileno(), 1)
        if not chunk:
            break
        buffer.extend(chunk)
        upper = bytes(buffer).upper()
        if in_basic and b'LOAD "RUNFILE.BAS"\\r' in upper:
            sys.stdout.write("LOADED RUNFILE.BAS\\r\\nOk\\r\\n")
            sys.stdout.flush()
            buffer.clear()
        elif in_basic and b"SYSTEM\\r" in upper:
            in_basic = False
            sys.stdout.write("A>\\r\\n")
            sys.stdout.flush()
            buffer.clear()
        elif (not in_basic) and b"EXIT\\r" in upper:
            break
finally:
    termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original_attrs)

sys.exit(0)
""",
        )


if __name__ == "__main__":
    unittest.main()
