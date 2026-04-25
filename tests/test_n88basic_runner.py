from __future__ import annotations

import os
import pty
import select
import signal
import stat
import io
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
RUNNER = ROOT_DIR / "run" / "n88basic.sh"
sys.path.insert(0, str(ROOT_DIR / "src"))


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

    def test_cli_main_returns_130_on_keyboard_interrupt(self) -> None:
        import n88basic_cli

        with patch.object(n88basic_cli.N88BasicCLI, "run_interactive", side_effect=KeyboardInterrupt):
            with patch.object(n88basic_cli, "_resolve_quasi88_bin", return_value=Path(__file__)):
                with patch.object(n88basic_cli, "_resolve_rom_dir", return_value=ROOT_DIR):
                    with patch.object(n88basic_cli, "_missing_required_rom_groups", return_value=[]):
                        result = n88basic_cli.main([])

        self.assertEqual(result, 130)

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

    def test_file_load_uses_extended_ready_timeout(self) -> None:
        import n88basic_cli

        cli = n88basic_cli.N88BasicCLI()

        with (
            patch.object(n88basic_cli, "RunControlSession") as run_control_session,
            patch.object(cli, "ensure_run_bridge", return_value=True),
            patch.object(cli, "_wait_for_basic_ready", return_value=True),
            patch.object(cli, "_load_program_with_fallback", return_value=(True, 0)) as load_program,
            patch.object(cli, "_capture_interactive_screen_lines", return_value=["Ok"]),
            patch.object(cli, "drain_output", return_value=b""),
        ):
            session = run_control_session.return_value
            session.go.return_value = (True, {})
            result = cli._enter_basic_interactive_mode("demo/mandelbrot/summary.bas")

        self.assertIs(result, session)
        self.assertIs(cli.interactive_bridge_session, session)
        self.assertEqual(load_program.call_args.kwargs["ready_timeout"], n88basic_cli._PROGRAM_LOAD_READY_TIMEOUT)

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
        command = mock_popen.call_args.args[0]
        self.assertIn("-nowait", command)
        self.assertIn("-hsbasic", command)

    def test_queue_program_lines_preserves_unsuffixed_decimal_literals(self) -> None:
        from n88basic_cli import N88BasicCLI
        from run_control_bridge import RUN_ERR_OK

        class _Session:
            def __init__(self) -> None:
                self.queued: list[str] = []

            def queue(self, sequence: str) -> tuple[bool, dict[str, int]]:
                self.queued.append(sequence)
                return True, {"code": RUN_ERR_OK}

        cli = N88BasicCLI()
        session = _Session()
        cli._capture_interactive_screen_lines = lambda _session: ["Ok"]  # type: ignore[method-assign]
        cli._wait_for_screen_change_and_ready = (  # type: ignore[method-assign]
            lambda _session, previous_lines, *, timeout, entered_line=None: [
                *previous_lines,
                f"step-{len(previous_lines)}",
            ]
        )

        ok, code = cli._queue_program_lines(
            session,
            ["10 DEFDBL A-Z", "20 A=.25", "30 PRINT A", "40 END"],
            ready_timeout=2.0,
        )

        self.assertTrue(ok)
        self.assertEqual(code, RUN_ERR_OK)
        self.assertEqual(
            session.queued,
            ["10 DEFDBL A-Z<CR>", "20 A=.25<CR>", "30 PRINT A<CR>", "40 END<CR>"],
        )

    def test_wait_for_screen_change_accepts_stable_ready_without_visible_delta(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _milliseconds: int) -> None:
                return None

        cli = N88BasicCLI()
        screens = iter([["Ok"], ["Ok"], ["Ok"]])
        cli._capture_interactive_screen_lines = lambda _session: next(screens)  # type: ignore[method-assign]

        with patch("n88basic_cli.time.monotonic", side_effect=[100.0, 100.0, 100.2, 100.4, 100.6]):
            lines = cli._wait_for_screen_change_and_ready(_Session(), ["Ok"], timeout=2.0)

        self.assertEqual(lines, ["Ok"])

    def test_wait_for_screen_change_accepts_stable_entered_program_line(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _milliseconds: int) -> None:
                return None

        cli = N88BasicCLI()
        screens = iter(
            [
                ["Ok", "10 PRINT"],
                ["Ok", "10 PRINT 42"],
                ["Ok", "10 PRINT 42"],
                ["Ok", "10 PRINT 42"],
            ]
        )
        cli._capture_interactive_screen_lines = lambda _session: next(screens)  # type: ignore[method-assign]

        with (
            patch("n88basic_cli.time.monotonic", side_effect=[100.0, 100.0, 100.0, 100.2, 100.4, 101.3, 101.3]),
            patch("n88basic_cli.time.sleep", return_value=None),
        ):
            lines = cli._wait_for_screen_change_and_ready(
                _Session(),
                ["Ok"],
                timeout=2.0,
                entered_line="10 PRINT 42",
            )

        self.assertEqual(lines, ["Ok", "10 PRINT 42"])

    def test_queue_with_retry_retries_queue_full(self) -> None:
        from n88basic_cli import N88BasicCLI
        from run_control_bridge import RUN_ERR_OK, RUN_ERR_QUEUE_FULL

        class _Session:
            def __init__(self) -> None:
                self.calls = 0

            def queue(self, _sequence: str) -> tuple[bool, dict[str, int]]:
                self.calls += 1
                if self.calls == 1:
                    return False, {"code": RUN_ERR_QUEUE_FULL}
                return True, {"code": RUN_ERR_OK}

            def wait(self, _milliseconds: int) -> None:
                return None

        cli = N88BasicCLI()

        with patch("n88basic_cli.time.sleep", return_value=None):
            ok, response = cli._queue_with_retry(_Session(), "10 PRINT 42<CR>", retry_timeout=1.0)

        self.assertTrue(ok)
        self.assertEqual(response["code"], RUN_ERR_OK)

    def test_pre_run_commands_adds_width_80_for_mandelbrot_asciiart(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()

        self.assertEqual(
            cli._pre_run_commands("demo/mandelbrot/asciiart.bas"),
            ["WIDTH 80"],
        )
        self.assertEqual(
            cli._pre_run_commands("demo/mandelbrot/asciiart-40col.bas"),
            [],
        )
        self.assertEqual(
            cli._pre_run_commands("demo/mandelbrot/summary.bas"),
            [],
        )

    def test_load_program_with_fallback_uses_typed_input_after_ascii_load_failure(self) -> None:
        from n88basic_cli import N88BasicCLI
        from run_control_bridge import RUN_ERR_OK, RUN_ERR_PROTOCOL

        class _Session:
            def load(self, _filepath: str, _fmt: str) -> tuple[bool, dict[str, int]]:
                return False, {"code": RUN_ERR_PROTOCOL}

        cli = N88BasicCLI()
        captured: dict[str, object] = {}

        def _queue_program_lines(
            _session: object,
            program_lines: list[str],
            *,
            ready_timeout: float,
        ) -> tuple[bool, int]:
            captured["lines"] = list(program_lines)
            captured["ready_timeout"] = ready_timeout
            return True, RUN_ERR_OK

        cli._queue_program_lines = _queue_program_lines  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "prog.bas"
            program.write_text("10 DEFDBL A-Z\r20 A=0.25\r\n30 PRINT A\n40 END\n", encoding="ascii")

            ok, code = cli._load_program_with_fallback(
                _Session(),
                filepath=str(program),
                ready_timeout=2.0,
                wait_for_ready_after_ascii_load=False,
            )

        self.assertTrue(ok)
        self.assertEqual(code, RUN_ERR_OK)
        self.assertEqual(
            captured["lines"],
            ["10 DEFDBL A-Z", "20 A=0.25", "30 PRINT A", "40 END"],
        )
        self.assertEqual(captured["ready_timeout"], 2.0)

    def test_load_program_with_fallback_uses_typed_input_after_file_not_found(self) -> None:
        from n88basic_cli import N88BasicCLI
        from run_control_bridge import RUN_ERR_FILE_NOT_FOUND, RUN_ERR_OK

        class _Session:
            def load(self, _filepath: str, _fmt: str) -> tuple[bool, dict[str, int]]:
                return False, {"code": RUN_ERR_FILE_NOT_FOUND}

        cli = N88BasicCLI()
        captured: dict[str, object] = {}

        def _queue_program_lines(
            _session: object,
            program_lines: list[str],
            *,
            ready_timeout: float,
        ) -> tuple[bool, int]:
            captured["lines"] = list(program_lines)
            captured["ready_timeout"] = ready_timeout
            return True, RUN_ERR_OK

        cli._queue_program_lines = _queue_program_lines  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "prog.bas"
            program.write_text("10 PRINT 42\n20 END\n", encoding="ascii")

            ok, code = cli._load_program_with_fallback(
                _Session(),
                filepath=str(program),
                ready_timeout=2.0,
                wait_for_ready_after_ascii_load=False,
            )

        self.assertTrue(ok)
        self.assertEqual(code, RUN_ERR_OK)
        self.assertEqual(captured["lines"], ["10 PRINT 42", "20 END"])
        self.assertEqual(captured["ready_timeout"], 2.0)

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

    def test_capture_interactive_screen_lines_preserves_trailing_spaces(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def textscr(self) -> tuple[bool, dict[str, dict[str, str]]]:
                return True, {"fields": {"text": "NEC N-88 BASIC Version 2.3\nOk\n10 \n"}}

        cli = N88BasicCLI()

        self.assertEqual(
            cli._capture_interactive_screen_lines(_Session()),
            ["NEC N-88 BASIC Version 2.3", "Ok", "10 "],
        )

    def test_send_interactive_bytes_batches_short_bridge_input(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def __init__(self) -> None:
                self.queued: list[str] = []

            def queue(self, sequence: str) -> tuple[bool, dict[str, int]]:
                self.queued.append(sequence)
                return True, {"code": 0}

        cli = N88BasicCLI()
        cli.interactive_bridge_session = _Session()

        cli._send_interactive_bytes(b'PRINT "A"\r')

        self.assertEqual(
            cli.interactive_bridge_session.queued,
            ['PRINT "A"<CR>'],
        )

    def test_send_interactive_bytes_splits_long_bridge_input_into_chunks(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def __init__(self) -> None:
                self.queued: list[str] = []

            def queue(self, sequence: str) -> tuple[bool, dict[str, int]]:
                self.queued.append(sequence)
                return True, {"code": 0}

        cli = N88BasicCLI()
        cli.interactive_bridge_session = _Session()

        cli._send_interactive_bridge_chunks(b"1234567890", max_chunk_tokens=4)

        self.assertEqual(cli.interactive_bridge_session.queued, ["1234", "5678", "90"])

    def test_encode_bridge_tokens_maps_backspace(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()

        self.assertEqual(cli._encode_bridge_tokens(b"AB\x7fC\r"), ["A", "B", "\x08", "C", "<CR>"])

    def test_send_interactive_bytes_retries_when_bridge_queue_is_full(self) -> None:
        from n88basic_cli import N88BasicCLI
        from run_control_bridge import RUN_ERR_OK, RUN_ERR_QUEUE_FULL

        class _Proc:
            def poll(self) -> None:
                return None

        class _Session:
            def __init__(self) -> None:
                self.calls = 0
                self.queued: list[str] = []

            def queue(self, sequence: str) -> tuple[bool, dict[str, int]]:
                self.calls += 1
                if self.calls == 1:
                    return False, {"code": RUN_ERR_QUEUE_FULL}
                self.queued.append(sequence)
                return True, {"code": RUN_ERR_OK}

        cli = N88BasicCLI()
        cli.quasi88_proc = _Proc()
        cli.interactive_bridge_session = _Session()

        with patch("n88basic_cli.time.sleep"):
            cli._send_interactive_bytes(b"A")

        self.assertEqual(cli.interactive_bridge_session.queued, ["A"])
        self.assertEqual(cli.interactive_bridge_session.calls, 2)

    def test_send_interactive_bytes_locally_erases_previous_character_on_backspace(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def __init__(self) -> None:
                self.queued: list[str] = []

            def queue(self, sequence: str) -> tuple[bool, dict[str, int]]:
                self.queued.append(sequence)
                return True, {"code": 0}

        cli = N88BasicCLI()
        cli.interactive_bridge_session = _Session()
        cli._interactive_line_open = True
        cli._interactive_line_length = len("10 AB")
        stdout = io.StringIO()

        with patch("sys.stdout", stdout):
            cli._send_interactive_bytes(b"\x7f")

        self.assertEqual(stdout.getvalue(), "\b \b")
        self.assertEqual(cli._interactive_line_length, len("10 A"))
        self.assertEqual(cli.interactive_bridge_session.queued, ["\x08"])

    def test_send_interactive_bytes_locally_closes_active_line_on_enter(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def __init__(self) -> None:
                self.queued: list[str] = []

            def queue(self, sequence: str) -> tuple[bool, dict[str, int]]:
                self.queued.append(sequence)
                return True, {"code": 0}

        cli = N88BasicCLI()
        cli.interactive_bridge_session = _Session()
        cli._interactive_line_open = True
        cli._interactive_line_length = len("10 A=1")
        stdout = io.StringIO()

        with patch("sys.stdout", stdout):
            cli._send_interactive_bytes(b"\r")

        self.assertEqual(stdout.getvalue(), "\r\n")
        self.assertFalse(cli._interactive_line_open)
        self.assertEqual(cli._interactive_line_length, 0)
        self.assertEqual(cli.interactive_bridge_session.queued, ["<CR>"])

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

    def test_poll_run_completion_accumulates_scrolled_output(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _timeout_ms: int) -> None:
                return None

        states = iter(
            [
                {"lines": ["RUN", "A", "B"], "output_lines": ["A", "B"], "completed": False},
                {"lines": ["RUN", "B", "C"], "output_lines": ["B", "C"], "completed": False},
                {"lines": ["RUN", "C", "D", "Ok"], "output_lines": ["C", "D"], "completed": True},
            ]
        )

        cli = N88BasicCLI()
        cli._capture_run_screen_state = lambda _session, *, saw_run_prompt=False, baseline_lines=None: next(states)  # type: ignore[method-assign]

        completed, output_lines = cli._poll_run_completion(_Session(), timeout_seconds=1.0)

        self.assertTrue(completed)
        self.assertEqual(output_lines, ["A", "B", "C", "D"])

    def test_poll_run_completion_can_finish_when_run_echo_is_already_gone(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _timeout_ms: int) -> None:
                return None

        states = iter(
            [
                {
                    "lines": ["42", "Ok"],
                    "output_lines": ["42"],
                    "completed": True,
                    "saw_run_prompt": True,
                },
            ]
        )

        cli = N88BasicCLI()
        cli._capture_run_screen_state = lambda _session, *, saw_run_prompt=False, baseline_lines=None: next(states)  # type: ignore[method-assign]

        completed, output_lines = cli._poll_run_completion(
            _Session(),
            timeout_seconds=1.0,
            saw_run_prompt=True,
        )

        self.assertTrue(completed)
        self.assertEqual(output_lines, ["42"])

    def test_capture_run_screen_state_uses_baseline_when_run_echo_is_gone(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        cli._capture_run_screen_lines = lambda _session: [  # type: ignore[method-assign]
            "How many files(0-15)? 2",
            "NEC N-88 BASIC Version 2.3",
            "Copyright (C) 1981 Microsoft",
            "56276 Bytes free",
            "Ok",
            "10 PRINT 42",
            "20 END",
            " 42",
            "Ok",
        ]

        state = cli._capture_run_screen_state(
            object(),
            saw_run_prompt=True,
            baseline_lines=[
                "How many files(0-15)? 2",
                "NEC N-88 BASIC Version 2.3",
                "Copyright (C) 1981 Microsoft",
                "56276 Bytes free",
                "Ok",
                "10 PRINT 42",
                "20 END",
            ],
        )

        self.assertTrue(state["completed"])
        self.assertEqual(state["output_lines"], [" 42"])

    def test_capture_run_screen_state_does_not_complete_on_baseline_ready_only(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        cli._capture_run_screen_lines = lambda _session: ["Ok"]  # type: ignore[method-assign]

        state = cli._capture_run_screen_state(
            object(),
            saw_run_prompt=True,
            baseline_lines=[],
        )

        self.assertFalse(state["completed"])
        self.assertEqual(state["output_lines"], [])

    def test_screen_has_basic_ready_requires_trailing_ok_prompt(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()

        self.assertFalse(
            cli._screen_has_basic_ready(
                [
                    "NEC N-88 BASIC Version 2.3",
                    "Ok",
                    "10 PRINT \"STILL TYPING",
                ]
            )
        )
        self.assertTrue(cli._screen_has_basic_ready(["10 PRINT \"DONE\"", "Ok"]))

    def test_poll_run_completion_skips_inflight_trailing_line_until_completed(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _timeout_ms: int) -> None:
                return None

        states = iter(
            [
                {
                    "lines": ["RUN", "3.14159265358979323#", "2.141592653589793", "3.14159"],
                    "output_lines": ["3.14159265358979323#", "2.141592653589793", "3.14159"],
                    "completed": False,
                },
                {
                    "lines": [
                        "RUN",
                        "3.14159265358979323#",
                        "2.141592653589793",
                        "3.14159265358979323846#",
                        "2.141592653589793",
                        "Ok",
                    ],
                    "output_lines": [
                        "3.14159265358979323#",
                        "2.141592653589793",
                        "3.14159265358979323846#",
                        "2.141592653589793",
                    ],
                    "completed": True,
                },
            ]
        )

        cli = N88BasicCLI()
        cli._capture_run_screen_state = lambda _session, *, saw_run_prompt=False, baseline_lines=None: next(states)  # type: ignore[method-assign]

        completed, output_lines = cli._poll_run_completion(_Session(), timeout_seconds=1.0)

        self.assertTrue(completed)
        self.assertEqual(
            output_lines,
            [
                "3.14159265358979323#",
                "2.141592653589793",
                "3.14159265358979323846#",
                "2.141592653589793",
            ],
        )

    def test_poll_run_completion_completes_after_run_scrolls_off_screen(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _timeout_ms: int) -> None:
                return None

        states = iter(
            [
                {
                    "lines": ["RUN", "BEST", "0", "3.14159265358979323#", "1.110223024625157D-16", "3.14"],
                    "output_lines": ["BEST", "0", "3.14159265358979323#", "1.110223024625157D-16", "3.14"],
                    "completed": False,
                    "saw_run_prompt": True,
                },
                {
                    "lines": [
                        "0",
                        "3.14159265358979323#",
                        "1.110223024625157D-16",
                        "3.14159265358979323846#",
                        "1.110223024625157D-16",
                        "Ok",
                    ],
                    "output_lines": [
                        "0",
                        "3.14159265358979323#",
                        "1.110223024625157D-16",
                        "3.14159265358979323846#",
                        "1.110223024625157D-16",
                    ],
                    "completed": True,
                    "saw_run_prompt": True,
                },
                {
                    "lines": [
                        "0",
                        "3.14159265358979323#",
                        "1.110223024625157D-16",
                        "3.14159265358979323846#",
                        "1.110223024625157D-16",
                        "Ok",
                    ],
                    "output_lines": [
                        "0",
                        "3.14159265358979323#",
                        "1.110223024625157D-16",
                        "3.14159265358979323846#",
                        "1.110223024625157D-16",
                    ],
                    "completed": True,
                    "saw_run_prompt": True,
                },
            ]
        )

        cli = N88BasicCLI()
        cli._capture_run_screen_state = lambda _session, *, saw_run_prompt=False, baseline_lines=None: next(states)  # type: ignore[method-assign]

        completed, output_lines = cli._poll_run_completion(_Session(), timeout_seconds=1.0)

        self.assertTrue(completed)
        self.assertEqual(
            output_lines,
            [
                "BEST",
                "0",
                "3.14159265358979323#",
                "1.110223024625157D-16",
                "3.14159265358979323846#",
                "1.110223024625157D-16",
            ],
        )

    def test_poll_run_completion_ignores_redrawn_older_window(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _timeout_ms: int) -> None:
                return None

        states = iter(
            [
                {
                    "lines": ["RUN", "B3", "B4", "B5", "B6"],
                    "output_lines": ["B3", "B4", "B5", "B6"],
                    "completed": False,
                },
                {
                    "lines": ["RUN", "B3", "B4", "B5", "B6", "B7"],
                    "output_lines": ["B3", "B4", "B5", "B6", "B7"],
                    "completed": False,
                },
                {
                    "lines": ["RUN", "B4", "B5", "B6", "B7", "B8"],
                    "output_lines": ["B4", "B5", "B6", "B7", "B8"],
                    "completed": False,
                },
                {
                    "lines": ["RUN", "B3", "B4", "B5", "B6", "B7"],
                    "output_lines": ["B3", "B4", "B5", "B6", "B7"],
                    "completed": False,
                },
                {
                    "lines": ["RUN", "B5", "B6", "B7", "B8", "B9", "Ok"],
                    "output_lines": ["B5", "B6", "B7", "B8", "B9"],
                    "completed": True,
                },
            ]
        )

        cli = N88BasicCLI()
        cli._capture_run_screen_state = lambda _session, *, saw_run_prompt=False, baseline_lines=None: next(states)  # type: ignore[method-assign]

        completed, output_lines = cli._poll_run_completion(_Session(), timeout_seconds=1.0)

        self.assertTrue(completed)
        self.assertEqual(output_lines, ["B3", "B4", "B5", "B6", "B7", "B8", "B9"])

    def test_capture_run_screen_state_keeps_completion_after_run_scrolls_off(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        cli._capture_run_screen_lines = lambda _session: [  # type: ignore[method-assign]
            "0",
            "3.14159265358979323#",
            "1.110223024625157D-16",
            "3.14159265358979323846#",
            "1.110223024625157D-16",
            "Ok",
        ]

        state = cli._capture_run_screen_state(object(), saw_run_prompt=True)

        self.assertTrue(state["completed"])
        self.assertEqual(
            state["output_lines"],
            [
                "0",
                "3.14159265358979323#",
                "1.110223024625157D-16",
                "3.14159265358979323846#",
                "1.110223024625157D-16",
            ],
        )

    def test_poll_run_completion_waits_for_stable_mandelbrot_block(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _timeout_ms: int) -> None:
                return None

        art = [
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
            "000001111111111111111122222222333557BF75433222211111000000000000000000000000000",
            "000111111111111111112222222233445C      643332222111110000000000000000000000000",
            "011111111111111111222222233444556C      654433332211111100000000000000000000000",
            "11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000",
            "111111111111122223333334469                 D   6322111111000000000000000000000",
            "1111111111222333333334457DB                    85332111111100000000000000000000",
            "11111122234B744444455556A                      96532211111110000000000000000000",
            "122222233347BAA7AB776679                         A32211111110000000000000000000",
            "2222233334567        9A                         A532221111111000000000000000000",
            "222333346679                                    9432221111111000000000000000000",
            "234445568  F                                   B5432221111111000000000000000000",
            "                                              864332221111111000000000000000000",
            "234445568  F                                   B5432221111111000000000000000000",
            "222333346679                                    9432221111111000000000000000000",
            "2222233334567        9A                         A532221111111000000000000000000",
            "122222233347BAA7AB776679                         A32211111110000000000000000000",
            "11111122234B744444455556A                      96532211111110000000000000000000",
            "1111111111222333333334457DB                    85332111111100000000000000000000",
            "111111111111122223333334469                 D   6322111111000000000000000000000",
            "11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000",
            "011111111111111111222222233444556C      654433332211111100000000000000000000000",
            "000111111111111111112222222233445C      643332222111110000000000000000000000000",
            "000001111111111111111122222222333557BF75433222211111000000000000000000000000000",
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
        ]
        states = iter(
            [
                {"lines": art[:12], "output_lines": art[:12], "completed": False, "art_fragments": True, "art_block": []},
                {"lines": art, "output_lines": art, "completed": False, "art_fragments": True, "art_block": art},
                {"lines": art, "output_lines": art, "completed": False, "art_fragments": True, "art_block": art},
            ]
        )

        cli = N88BasicCLI()
        emitted: list[list[str]] = []
        cli._capture_run_screen_state = lambda _session, *, saw_run_prompt=False, baseline_lines=None: next(states)  # type: ignore[method-assign]

        completed, output_lines = cli._poll_run_completion(
            _Session(),
            timeout_seconds=1.0,
            emit_output=lambda chunk: emitted.append(list(chunk)),
        )

        self.assertTrue(completed)
        self.assertEqual(output_lines, art)
        self.assertEqual(emitted, [art])

    def test_poll_run_completion_reconstructs_mandelbrot_from_scrolling_history(self) -> None:
        from n88basic_cli import N88BasicCLI

        class _Session:
            def wait(self, _timeout_ms: int) -> None:
                return None

        art = [
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
            "000001111111111111111122222222333557BF75433222211111000000000000000000000000000",
            "000111111111111111112222222233445C      643332222111110000000000000000000000000",
            "011111111111111111222222233444556C      654433332211111100000000000000000000000",
            "11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000",
            "111111111111122223333334469                 D   6322111111000000000000000000000",
            "1111111111222333333334457DB                    85332111111100000000000000000000",
            "11111122234B744444455556A                      96532211111110000000000000000000",
            "122222233347BAA7AB776679                         A32211111110000000000000000000",
            "2222233334567        9A                         A532221111111000000000000000000",
            "222333346679                                    9432221111111000000000000000000",
            "234445568  F                                   B5432221111111000000000000000000",
            "                                              864332221111111000000000000000000",
            "234445568  F                                   B5432221111111000000000000000000",
            "222333346679                                    9432221111111000000000000000000",
            "2222233334567        9A                         A532221111111000000000000000000",
            "122222233347BAA7AB776679                         A32211111110000000000000000000",
            "11111122234B744444455556A                      96532211111110000000000000000000",
            "1111111111222333333334457DB                    85332111111100000000000000000000",
            "111111111111122223333334469                 D   6322111111000000000000000000000",
            "11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000",
            "011111111111111111222222233444556C      654433332211111100000000000000000000000",
            "000111111111111111112222222233445C      643332222111110000000000000000000000000",
            "000001111111111111111122222222333557BF75433222211111000000000000000000000000000",
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
        ]
        states = iter(
            [
                {"lines": art[:10], "output_lines": art[:10], "completed": False, "art_fragments": True, "art_block": []},
                {"lines": art[8:18], "output_lines": art[8:18], "completed": False, "art_fragments": True, "art_block": []},
                {"lines": art[16:], "output_lines": art[16:], "completed": False, "art_fragments": True, "art_block": []},
                {"lines": art[16:] + ["Ok"], "output_lines": art[16:], "completed": True, "art_fragments": True, "art_block": []},
            ]
        )

        cli = N88BasicCLI()
        emitted: list[list[str]] = []
        cli._capture_run_screen_state = lambda _session, *, saw_run_prompt=False, baseline_lines=None: next(states)  # type: ignore[method-assign]

        completed, output_lines = cli._poll_run_completion(
            _Session(),
            timeout_seconds=1.0,
            emit_output=lambda chunk: emitted.append(list(chunk)),
        )

        self.assertTrue(completed)
        self.assertEqual(output_lines, art)
        self.assertEqual(emitted, [art])

    def test_capture_run_screen_state_detects_mandelbrot_block(self) -> None:
        from n88basic_cli import N88BasicCLI

        art = [
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
            "000001111111111111111122222222333557BF75433222211111000000000000000000000000000",
            "000111111111111111112222222233445C      643332222111110000000000000000000000000",
            "011111111111111111222222233444556C      654433332211111100000000000000000000000",
            "11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000",
            "111111111111122223333334469                 D   6322111111000000000000000000000",
            "1111111111222333333334457DB                    85332111111100000000000000000000",
            "11111122234B744444455556A                      96532211111110000000000000000000",
            "122222233347BAA7AB776679                         A32211111110000000000000000000",
            "2222233334567        9A                         A532221111111000000000000000000",
            "222333346679                                    9432221111111000000000000000000",
            "234445568  F                                   B5432221111111000000000000000000",
            "                                              864332221111111000000000000000000",
            "234445568  F                                   B5432221111111000000000000000000",
            "222333346679                                    9432221111111000000000000000000",
            "2222233334567        9A                         A532221111111000000000000000000",
            "122222233347BAA7AB776679                         A32211111110000000000000000000",
            "11111122234B744444455556A                      96532211111110000000000000000000",
            "1111111111222333333334457DB                    85332111111100000000000000000000",
            "111111111111122223333334469                 D   6322111111000000000000000000000",
            "11111111111111112222233346 D978 BCF    DF9 6556F4221111110000000000000000000000",
            "011111111111111111222222233444556C      654433332211111100000000000000000000000",
            "000111111111111111112222222233445C      643332222111110000000000000000000000000",
            "000001111111111111111122222222333557BF75433222211111000000000000000000000000000",
            "000000011111111111111111122222233347E7AB322222111100000000000000000000000000000",
            "Ok",
        ]

        cli = N88BasicCLI()
        cli._capture_run_screen_lines = lambda _session: art  # type: ignore[method-assign]

        state = cli._capture_run_screen_state(object(), saw_run_prompt=True)

        self.assertEqual(state["art_block"], art[:-1])
        self.assertTrue(state["art_fragments"])

    def test_startup_screen_snapshot_uses_crlf(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        cli._startup_screen_snapshot = ["NEC N-88 BASIC Version 2.3", "Ok"]
        stdout = io.StringIO()

        with patch("sys.stdout", stdout):
            cli._emit_startup_screen_snapshot()

        self.assertEqual(stdout.getvalue(), "NEC N-88 BASIC Version 2.3\r\nOk\r\n")

    def test_startup_screen_snapshot_includes_preloaded_program_listing(self) -> None:
        from n88basic_cli import N88BasicCLI

        cli = N88BasicCLI()
        cli._preloaded_program_lines = ["10 A=10", "20 PRINT A"]
        cli._startup_screen_snapshot = ["NEC N-88 BASIC Version 2.3", "Ok"]
        stdout = io.StringIO()

        with patch("sys.stdout", stdout):
            cli._emit_startup_screen_snapshot()

        self.assertEqual(stdout.getvalue(), "NEC N-88 BASIC Version 2.3\r\nOk\r\n10 A=10\r\n20 PRINT A\r\n")
        self.assertEqual(cli._last_rendered_screen_lines, ["NEC N-88 BASIC Version 2.3", "Ok"])

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
            if b"Ok" not in output:
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

    def test_interactive_launch_backspace_removes_previous_character(self) -> None:
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

        proc, master_fd = self._spawn_pty(["bash", str(RUNNER)])
        try:
            self._read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"10 AB\x7fC\r")
            time.sleep(2)
            os.write(master_fd, b"LIST\r")
            output = self._read_until(master_fd, b"Ok", timeout=30)
            os.write(master_fd, b"\x04")
            proc.wait(timeout=10)
        finally:
            os.close(master_fd)
            if proc.poll() is None:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b"10 AC", output)
        self.assertNotIn(b"10 ABC", output)

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

    def test_file_launch_falls_back_for_unsuffixed_decimal_literals(self) -> None:
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

        with tempfile.TemporaryDirectory() as tmp:
            program = Path(tmp) / "frac.bas"
            program.write_text("10 DEFDBL A-Z\n20 A=0.25\n30 PRINT A\n40 END\n", encoding="ascii")

            proc, master_fd = self._spawn_pty(["bash", str(RUNNER), "--file", str(program)])
            try:
                self._read_until(master_fd, b"Ok", timeout=60)
                os.write(master_fd, b"RUN\r")
                output = self._read_until(master_fd, b".25", timeout=30)
                if b"Ok" not in output:
                    output += self._read_until(master_fd, b"Ok", timeout=30)
                os.write(master_fd, b"\x04")
                proc.wait(timeout=10)
            finally:
                os.close(master_fd)
                if proc.poll() is None:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    proc.wait(timeout=10)

        self.assertEqual(proc.returncode, 0)
        self.assertIn(b".25", output)
        self.assertNotIn(b"failed to load BASIC file", output)

    def test_run_file_falls_back_for_unsuffixed_decimal_literals(self) -> None:
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            for literal in ("0.25", ".25"):
                program = tmp_path / f"frac-{literal.replace('.', 'dot')}.bas"
                program.write_text(
                    f"10 DEFDBL A-Z\n20 A={literal}\n30 PRINT A\n40 END\n",
                    encoding="ascii",
                )

                with self.subTest(literal=literal):
                    result = subprocess.run(
                        ["bash", str(RUNNER), "--run", "--file", str(program)],
                        cwd=ROOT_DIR,
                        env=self._runtime_env(),
                        capture_output=True,
                        text=True,
                        timeout=90,
                    )

                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), ".25")
                    self.assertNotIn(f"A={literal}", result.stdout)

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

    def test_run_file_completes_after_run_scrolls_off_screen(self) -> None:
        rom_dir = ROOT_DIR / "downloads" / "n88basic" / "roms"
        quasi88_bin = ROOT_DIR / "vendor" / "quasi88" / "quasi88.sdl2"
        if not rom_dir.is_dir() or not quasi88_bin.is_file():
            self.skipTest("N88-BASIC runtime is not available")

        program = ROOT_DIR / "tests" / "data" / "n88basic_run_scroll_completion_probe.bas"
        result = subprocess.run(
            ["bash", str(RUNNER), "--run", "--file", str(program)],
            cwd=ROOT_DIR,
            env=self._runtime_env(),
            capture_output=True,
            text=True,
            timeout=90,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("3.14159265358979323#", result.stdout)
        self.assertIn("3.14159265358979323846#", result.stdout)


if __name__ == "__main__":
    unittest.main()
