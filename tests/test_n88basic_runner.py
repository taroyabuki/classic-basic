from __future__ import annotations

import os
import pty
import select
import signal
import stat
import io
import subprocess
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER = ROOT_DIR / "run" / "n88basic.sh"


def _write_executable(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="ascii")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class N88BasicRunnerTests(unittest.TestCase):
    def _runtime_env(self, extra_path: Path | None = None) -> dict[str, str]:
        env = os.environ.copy()
        pythonpath = str(ROOT_DIR / "src")
        env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"
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
        deadline = time.time() + timeout
        output = bytearray()
        while time.time() < deadline:
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

    def test_plain_launch_starts_interactive_cli(self) -> None:
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
            self.assertEqual(log_path.read_text(encoding="ascii").strip(), "-m n88basic_cli")

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
                f"-m n88basic_cli --file {program}",
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
                f"-m n88basic_cli --run --file {program}",
            )

    def test_run_flag_with_timeout_exports_batch_timeout_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            bin_dir = temp_path / "bin"
            bin_dir.mkdir()
            log_path = temp_path / "python3.log"
            env_log_path = temp_path / "python3.env.log"
            _write_executable(
                bin_dir / "python3",
                f"""#!/usr/bin/env bash
                set -euo pipefail
                printf '%s\\n' "$*" >"{log_path}"
                printf '%s\\n' "${{CLASSIC_BASIC_N88_BATCH_TIMEOUT:-}}" >"{env_log_path}"
                """,
            )
            program = temp_path / "prog.bas"
            program.write_text("10 PRINT 42\n", encoding="ascii")

            result = subprocess.run(
                ["bash", str(RUNNER), "--run", "--file", str(program), "--timeout", "17.5"],
                cwd=ROOT_DIR,
                env=self._runtime_env(bin_dir),
                capture_output=True,
                text=True,
                timeout=20,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                log_path.read_text(encoding="ascii").strip(),
                f"-m n88basic_cli --run --file {program}",
            )
            self.assertEqual(env_log_path.read_text(encoding="ascii").strip(), "17.5")

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
        self.assertIn("--run requires --file PROGRAM.bas", result.stderr)

    def test_timeout_requires_run_file(self) -> None:
        result = subprocess.run(
            ["bash", str(RUNNER), "--timeout", "12"],
            cwd=ROOT_DIR,
            env=self._runtime_env(),
            capture_output=True,
            text=True,
            timeout=20,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("--timeout requires --run --file PROGRAM.bas", result.stderr)

    def test_incomplete_rom_set_fails_before_startup_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = Path(tmp)
            rom_dir = temp_path / "roms"
            rom_dir.mkdir()
            (rom_dir / "n88.rom").write_bytes(b"rom")
            quasi88_bin = temp_path / "quasi88.sdl2"
            _write_executable(
                quasi88_bin,
                """#!/usr/bin/env bash
                exit 99
                """,
            )
            env = self._runtime_env()
            env["CLASSIC_BASIC_N88_ROM_DIR"] = str(rom_dir)
            env["CLASSIC_BASIC_N88_QUASI88_BIN"] = str(quasi88_bin)

            result = subprocess.run(
                ["python3", "-m", "n88basic_cli"],
                cwd=ROOT_DIR,
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("required N88-BASIC ROMs are missing", result.stderr)
        self.assertIn("Run ./setup/n88basic.sh first.", result.stderr)

    def test_interactive_loop_stops_on_prefetched_eof(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        cli.prefetched_stdin = b""
        cli.stdin_eof = False
        cli.quasi88_proc = type("P", (), {"poll": lambda self: None})()

        self.assertFalse(cli._interactive_loop_step())

    def test_start_quasi88_run_host_enables_bridge_only_mode(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        with patch("n88basic_cli.subprocess.Popen") as mock_popen:
            cli.start_quasi88_run_host()

        env = mock_popen.call_args.kwargs["env"]
        self.assertEqual(env["N88BASIC_BRIDGE_ONLY"], "1")

    def test_interactive_screen_output_rewrites_active_line(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def __init__(self, screens: list[str]) -> None:
                self._screens = iter(screens)

            def wait(self, _timeout_ms: int) -> None:
                return None

            def textscr(self) -> tuple[bool, dict[str, dict[str, str]]]:
                return True, {"fields": {"text": next(self._screens)}}

        cli = N88BasicCLI()
        cli.interactive_bridge_session = _Session(
            [
                "NEC N-88 BASIC Version 2.3\nOk\np\n",
                "NEC N-88 BASIC Version 2.3\nOk\npr\n",
                "NEC N-88 BASIC Version 2.3\nSyntax error\nOk\n",
            ]
        )
        cli._last_rendered_screen_lines = ["NEC N-88 BASIC Version 2.3", "Ok"]
        stdout = io.StringIO()

        with patch("sys.stdout", stdout):
            cli._forward_interactive_screen_output()
            cli._forward_interactive_screen_output()
            cli._forward_interactive_screen_output()

        self.assertEqual(stdout.getvalue(), "p\rpr\r\nSyntax error\r\nOk\r\n")

    def test_interactive_screen_output_closes_active_line_before_result_rows(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        cli._last_rendered_screen_lines = ["NEC N-88 BASIC Version 2.3", "Ok", "PRINT 2+2"]
        cli._interactive_line_open = True
        cli._interactive_line_length = len("PRINT 2+2")

        rendered = cli._render_interactive_screen_delta(
            ["NEC N-88 BASIC Version 2.3", "Ok", "PRINT 2+2", "4", "Ok"],
            3,
        )

        self.assertEqual(rendered, "\r\n4\r\nOk\r\n")
        self.assertFalse(cli._interactive_line_open)
        self.assertEqual(cli._interactive_line_length, 0)

    def test_startup_screen_snapshot_uses_crlf(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        cli._startup_screen_snapshot = ["NEC N-88 BASIC Version 2.3", "Ok"]
        stdout = io.StringIO()

        with patch("sys.stdout", stdout):
            cli._emit_startup_screen_snapshot()

        self.assertEqual(stdout.getvalue(), "NEC N-88 BASIC Version 2.3\r\nOk\r\n")

    def test_interactive_launch_keeps_prompt_and_next_input_at_column_zero(self) -> None:
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

        proc, master_fd = self._spawn_pty(["bash", str(RUNNER)])
        output = bytearray()
        try:
            output.extend(self._read_until(master_fd, b"Ok", timeout=30))
            os.write(master_fd, b"print 2+2\r")
            output.extend(self._read_until(master_fd, b"Ok", timeout=30))
            os.write(master_fd, b"p")
            time.sleep(1)
            while True:
                ready, _, _ = select.select([master_fd], [], [], 0.1)
                if not ready:
                    break
                output.extend(os.read(master_fd, 4096))
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"\r\n4\r\nOk\r\np", bytes(output))

    def test_interactive_launch_exits_on_ctrl_d(self) -> None:
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

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
        self.assertIn(b"NEC N-88 BASIC", output)

    def test_interactive_launch_accepts_shifted_ascii_input(self) -> None:
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

        proc, master_fd = self._spawn_pty(["bash", str(RUNNER)])
        try:
            self._read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b'PRINT "A"\r')
            output = self._read_until(master_fd, b"A", timeout=30)
            output += self._read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"A", output)
        self.assertNotIn(b"Syntax error", output)

    def test_file_launch_loads_program_and_runs_after_manual_run(self) -> None:
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

        proc, master_fd = self._spawn_pty(["bash", str(RUNNER), "--file", "demo/n88basic.bas"])
        try:
            self._read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"RUN\r")
            output = self._read_until(master_fd, b"PI 3.14159", timeout=30)
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
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

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
        self.assertIn("program input is not supported in --run mode", result.stderr)


if __name__ == "__main__":
    unittest.main()
