from __future__ import annotations

import argparse
import os
import re
import select
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
from pathlib import Path

import fbasic_batch
import n88basic_program_check
from run_control_bridge import (
    RUN_ERR_FILE_NOT_FOUND,
    RUN_ERR_HOOK_UNCONFIGURED,
    RUN_ERR_OK,
    RUN_ERR_PROTOCOL,
    RUN_ERR_TIMEOUT,
    RunControlSession,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_QUASI88_BIN = _PROJECT_ROOT / "vendor" / "quasi88" / "quasi88.sdl2"
_DEFAULT_ROM_DIR = _PROJECT_ROOT / "downloads" / "n88basic" / "roms"
_FUNCTION_KEY_GUIDE = 'load "        auto          go to         list          run'
_REQUIRED_ROM_ALIASES: tuple[tuple[str, ...], ...] = (
    ("N88.ROM", "n88.rom"),
    ("N88EXT0.ROM", "n88ext0.rom", "N88_0.ROM", "n88_0.rom"),
    ("N88EXT1.ROM", "n88ext1.rom", "N88_1.ROM", "n88_1.rom"),
    ("N88EXT2.ROM", "n88ext2.rom", "N88_2.ROM", "n88_2.rom"),
    ("N88EXT3.ROM", "n88ext3.rom", "N88_3.ROM", "n88_3.rom"),
    ("N88SUB.ROM", "n88sub.rom", "DISK.ROM", "disk.rom"),
)


def _resolve_quasi88_bin() -> Path:
    return Path(os.environ.get("CLASSIC_BASIC_N88_QUASI88_BIN", _DEFAULT_QUASI88_BIN))


def _resolve_rom_dir() -> Path:
    return Path(os.environ.get("CLASSIC_BASIC_N88_ROM_DIR", _DEFAULT_ROM_DIR))


def _missing_required_rom_groups(rom_dir: Path) -> list[tuple[str, ...]]:
    missing: list[tuple[str, ...]] = []
    for aliases in _REQUIRED_ROM_ALIASES:
        if not any((rom_dir / candidate).is_file() for candidate in aliases):
            missing.append(aliases)
    return missing


class N88BasicCLI:
    def __init__(self, *, quasi88_bin: Path | None = None, rom_dir: Path | None = None):
        self.quasi88_bin = _resolve_quasi88_bin() if quasi88_bin is None else quasi88_bin
        self.rom_dir = _resolve_rom_dir() if rom_dir is None else rom_dir
        self.quasi88_proc: subprocess.Popen[bytes] | None = None
        self.output_buffer: list[bytes] = []
        self.output_lock = threading.Lock()
        self.stop_reader = threading.Event()
        self.reader_thread: threading.Thread | None = None
        self.transcript: list[str] = []
        self.prefetched_stdin: bytes | None = None
        self.stdin_eof = False
        self.control_socket_path: str | None = None
        self._startup_screen_snapshot: list[str] | None = None
        self.interactive_bridge_session: RunControlSession | None = None
        self._last_rendered_screen_lines: list[str] = []
        self._interactive_line_open = False
        self._interactive_line_length = 0

    def _quasi88_command(self) -> list[str]:
        return [
            str(self.quasi88_bin),
            "-romdir",
            str(self.rom_dir),
            "-window",
            "-romboot",
            "-v2",
            "-monitor",
        ]

    def start_quasi88_monitor(self) -> None:
        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"
        env["SDL_AUDIODRIVER"] = "dummy"
        self._start_quasi88_process(self._quasi88_command(), env)

    def start_quasi88_run_host(self) -> None:
        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"
        env["SDL_AUDIODRIVER"] = "dummy"
        env["N88BASIC_BRIDGE_ONLY"] = "1"
        env["N88BASIC_CONTROL_SOCKET"] = self._ensure_control_socket_path()
        self._start_quasi88_process(self._quasi88_command(), env)

    def _start_quasi88_process(self, command: list[str], env: dict[str, str]) -> None:
        self.quasi88_proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            bufsize=0,
        )
        self.stop_reader.clear()
        self.output_buffer = []
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        self.prefetched_stdin = None
        self.stdin_eof = False
        time.sleep(0.5)

    def _ensure_control_socket_path(self) -> str:
        if self.control_socket_path:
            return self.control_socket_path
        tmp_dir = Path(tempfile.gettempdir())
        self.control_socket_path = str(
            tmp_dir / f"n88basic-control-{os.getpid()}-{time.time_ns()}.sock"
        )
        return self.control_socket_path

    def _cleanup_control_socket_path(self) -> None:
        if not self.control_socket_path:
            return
        try:
            os.unlink(self.control_socket_path)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _reader_loop(self) -> None:
        assert self.quasi88_proc is not None
        assert self.quasi88_proc.stdout is not None
        while not self.stop_reader.is_set():
            try:
                chunk = self.quasi88_proc.stdout.read(256)
                if chunk:
                    with self.output_lock:
                        self.output_buffer.append(chunk)
                elif self.quasi88_proc.poll() is not None:
                    break
                else:
                    time.sleep(0.01)
            except Exception:
                break

    def stop_quasi88(self) -> None:
        if self.quasi88_proc is not None:
            self.stop_reader.set()
            self.quasi88_proc.terminate()
            try:
                self.quasi88_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.quasi88_proc.kill()
                self.quasi88_proc.wait(timeout=3)
            self.quasi88_proc = None
        self._cleanup_control_socket_path()

    def drain_output(self, *, clear: bool = True) -> bytes:
        with self.output_lock:
            result = b"".join(self.output_buffer)
            if clear:
                self.output_buffer = []
            return result

    def has_prompt(self) -> bool:
        return b"QUASI88> " in self.drain_output(clear=False)

    def wait_for_prompt(self, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.has_prompt():
                return True
            if self.quasi88_proc is not None and self.quasi88_proc.poll() is not None:
                return False
            time.sleep(0.05)
        return False

    def send_command(self, command: str) -> bytes:
        if self.quasi88_proc is None or self.quasi88_proc.stdin is None:
            raise RuntimeError("QUASI88 is not running")
        self.drain_output(clear=True)
        self.quasi88_proc.stdin.write(command.encode("utf-8") + b"\n")
        self.quasi88_proc.stdin.flush()
        self.wait_for_prompt(timeout=2.0)
        return self.drain_output(clear=True)

    def get_screen(self) -> str:
        return self.send_command("textscr").decode("utf-8", errors="replace")

    def forward_stdin_to_quasi88(self, *, timeout: float = 0.1) -> str:
        if self.quasi88_proc is not None and self.quasi88_proc.poll() is not None:
            return "eof"

        if self.prefetched_stdin is not None:
            if self.prefetched_stdin:
                data = self.prefetched_stdin
                eof_index = data.find(b"\x04")
                if eof_index != -1:
                    if eof_index:
                        self._send_interactive_bytes(data[:eof_index])
                        self.prefetched_stdin = b""
                        self.stdin_eof = True
                        return "forwarded"
                    self.prefetched_stdin = b""
                    self.stdin_eof = True
                    return "eof"
                self._send_interactive_bytes(data)
                self.prefetched_stdin = b""
                return "forwarded"
            self.stdin_eof = True
            return "eof"

        ready, _, _ = select.select([sys.stdin.fileno()], [], [], timeout)
        if not ready:
            return "idle"
        try:
            data = os.read(sys.stdin.fileno(), 4096)
        except OSError:
            return "idle"
        if not data:
            self.stdin_eof = True
            return "eof"
        eof_index = data.find(b"\x04")
        if eof_index != -1:
            if eof_index:
                self._send_interactive_bytes(data[:eof_index])
                self.stdin_eof = True
                return "forwarded"
            self.stdin_eof = True
            return "eof"
        self._send_interactive_bytes(data)
        return "forwarded"

    def _send_interactive_bytes(self, data: bytes) -> None:
        if not data:
            return
        if self.interactive_bridge_session is not None:
            sequence = self._encode_bridge_sequence(data)
            if sequence:
                self.interactive_bridge_session.queue(sequence)
            return
        if self.quasi88_proc is None or self.quasi88_proc.stdin is None:
            return
        self.quasi88_proc.stdin.write(data)
        self.quasi88_proc.stdin.flush()

    def _encode_bridge_sequence(self, data: bytes) -> str:
        parts: list[str] = []
        for byte in data:
            if byte in (10, 13):
                parts.append("<CR>")
            elif 32 <= byte <= 126:
                parts.append(chr(byte))
        return "".join(parts)

    def _interactive_loop_step(self) -> bool:
        stdin_state = self.forward_stdin_to_quasi88(timeout=0.05)
        self.forward_quasi88_output()
        return stdin_state != "eof"

    def forward_quasi88_output(self) -> int:
        if self.interactive_bridge_session is not None:
            return self._forward_interactive_screen_output()
        output = self.drain_output(clear=True)
        if not output:
            return 0
        sys.stdout.write(output.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        return len(output)

    def _forward_interactive_screen_output(self) -> int:
        assert self.interactive_bridge_session is not None
        try:
            self.interactive_bridge_session.wait(50)
        except Exception:
            pass
        lines = self._capture_interactive_screen_lines(self.interactive_bridge_session)
        if not lines:
            return 0
        common = 0
        max_common = min(len(self._last_rendered_screen_lines), len(lines))
        while common < max_common and self._last_rendered_screen_lines[common] == lines[common]:
            common += 1
        if common == len(lines) and common == len(self._last_rendered_screen_lines):
            return 0

        rendered = self._render_interactive_screen_delta(lines, common)
        if not rendered:
            self._last_rendered_screen_lines = lines
            return 0
        sys.stdout.write(rendered)
        sys.stdout.flush()
        self._last_rendered_screen_lines = lines
        return len(rendered)

    def _render_interactive_screen_delta(self, lines: list[str], common: int) -> str:
        previous = self._last_rendered_screen_lines
        if (
            self._interactive_line_open
            and len(lines) == len(previous)
            and len(lines) > 0
            and common == len(lines) - 1
        ):
            return self._render_active_interactive_line(lines[-1], rewrite=True)

        if (
            len(lines) == len(previous) + 1
            and common == len(previous)
            and lines
            and lines[-1].lower() != "ok"
        ):
            return self._render_active_interactive_line(lines[-1], rewrite=False)

        # Close any in-progress input line at column 0 before printing settled output.
        prefix = "\r\n" if self._interactive_line_open else ""
        rendered = prefix + "\r\n".join(lines[common:]) + "\r\n"
        self._interactive_line_open = False
        self._interactive_line_length = 0
        return rendered

    def _render_active_interactive_line(self, line: str, *, rewrite: bool) -> str:
        clear = ""
        if rewrite and self._interactive_line_length > len(line):
            clear = " " * (self._interactive_line_length - len(line))
        prefix = "\r" if rewrite and self._interactive_line_open else ""
        rendered = f"{prefix}{line}{clear}"
        if clear:
            rendered += f"\r{line}"
        self._interactive_line_open = True
        self._interactive_line_length = len(line)
        return rendered

    def ensure_run_bridge(self, session: RunControlSession, *, timeout: float = 5.0) -> bool:
        is_real_bridge_session = hasattr(session, "control_socket_path")
        if is_real_bridge_session:
            session.control_socket_path = self._ensure_control_socket_path()

        if session.connect():
            if not is_real_bridge_session:
                return True
            ok, _ = session.state()
            if ok:
                return True
            session.disconnect()

        session.disconnect()
        self.stop_quasi88()
        self._cleanup_control_socket_path()
        self.start_quasi88_run_host()

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.quasi88_proc is not None and self.quasi88_proc.poll() is not None:
                break
            if session.connect():
                if not is_real_bridge_session:
                    return True
                ok, _ = session.state()
                if ok:
                    return True
            session.disconnect()
            time.sleep(0.05)
        return False

    def _capture_interactive_screen_lines(self, session: RunControlSession) -> list[str]:
        try:
            ok, response = session.textscr()
        except Exception:
            return []
        if not ok:
            return []
        text = response.get("fields", {}).get("text", "")
        lines: list[str] = []
        for raw_line in text.splitlines():
            line = raw_line.rstrip().replace("\ufffd", "")
            if not line.strip():
                continue
            if "How many files(0-15)?" in line:
                continue
            if self._is_function_key_guide_line(line):
                continue
            lines.append(line.strip())
        return lines

    @staticmethod
    def _is_function_key_guide_line(line: str) -> bool:
        stripped = line.strip()
        if stripped == _FUNCTION_KEY_GUIDE:
            return True
        if stripped == "cont":
            return True
        return stripped.startswith('save "') or stripped.startswith('load "')

    def _screen_has_basic_ready(self, screen_lines: list[str]) -> bool:
        return any(line.strip().lower() == "ok" for line in screen_lines)

    def _wait_for_basic_ready(self, session: RunControlSession, *, startup_timeout: float) -> bool:
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            if self._screen_has_basic_ready(self._capture_interactive_screen_lines(session)):
                return True
            session.wait(100)
            time.sleep(0.05)
        return False

    def _emit_startup_screen_snapshot(self) -> None:
        lines = self._startup_screen_snapshot or ["Ok"]
        sys.stdout.write("\r\n".join(lines) + "\r\n")
        sys.stdout.flush()
        self._last_rendered_screen_lines = list(lines)
        self._interactive_line_open = False
        self._interactive_line_length = 0
        self._startup_screen_snapshot = None

    def _enter_basic_interactive_mode(self, filepath: str | None = None) -> RunControlSession | None:
        session = RunControlSession(self._ensure_control_socket_path())
        self._startup_screen_snapshot = None
        self.interactive_bridge_session = None
        self._last_rendered_screen_lines = []

        if not self.ensure_run_bridge(session):
            print("error: control bridge unavailable", file=sys.stderr)
            session.disconnect()
            self.stop_quasi88()
            return None
        ok, _ = session.go()
        if not ok or not self._wait_for_basic_ready(session, startup_timeout=5.0):
            print("error: timeout waiting for BASIC startup prompt", file=sys.stderr)
            session.disconnect()
            self.stop_quasi88()
            return None
        if filepath is not None:
            ok, _ = session.load(filepath, "ASCII")
            if not ok or not self._wait_for_basic_ready(session, startup_timeout=2.0):
                print("error: failed to load BASIC file", file=sys.stderr)
                session.disconnect()
                self.stop_quasi88()
                return None

        self.drain_output(clear=True)
        self._startup_screen_snapshot = self._capture_interactive_screen_lines(session)
        self.interactive_bridge_session = session
        return session

    def _run_interactive_loop(self) -> None:
        old_settings = None
        try:
            self._emit_startup_screen_snapshot()
            if sys.stdin.isatty():
                old_settings = termios.tcgetattr(sys.stdin)
                tty.setraw(sys.stdin.fileno())
            else:
                self.prefetched_stdin = sys.stdin.buffer.read()
            while self.quasi88_proc is not None and self.quasi88_proc.poll() is None:
                if not self._interactive_loop_step():
                    break
        finally:
            if old_settings is not None:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    def run_interactive(self) -> int:
        session = self._enter_basic_interactive_mode()
        if session is None:
            return 1
        try:
            self._run_interactive_loop()
            return 0
        finally:
            session.disconnect()
            self.interactive_bridge_session = None
            self.forward_quasi88_output()
            self.stop_quasi88()

    def run_file_interactive(self, filepath: str) -> int:
        session = self._enter_basic_interactive_mode(filepath)
        if session is None:
            return 1
        try:
            self._run_interactive_loop()
            return 0
        finally:
            session.disconnect()
            self.interactive_bridge_session = None
            self.forward_quasi88_output()
            self.stop_quasi88()

    def _validate_line_numbers(self, filepath: str) -> None:
        lines = Path(filepath).read_text(encoding="ascii").splitlines()
        has_code = False
        for line_number, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            if stripped.startswith("'"):
                continue
            upper = stripped.upper()
            if upper.startswith("REM") and (len(stripped) == 3 or stripped[3] in (" ", "\t")):
                continue
            has_code = True
            if not stripped[0].isdigit():
                raise ValueError(f"line {line_number} missing line number: {stripped[:50]}")
        if not has_code:
            raise ValueError("no executable code found in file")

    def _capture_run_screen_lines(self, session: RunControlSession) -> list[str]:
        try:
            ok, response = session.textscr()
            screen = response.get("fields", {}).get("text", "") if ok else ""
        except Exception:
            screen = ""
        if not screen:
            try:
                session.disconnect()
                time.sleep(0.2)
                screen = self.get_screen()
            except Exception:
                screen = self.drain_output(clear=False).decode("utf-8", errors="replace")
        lines: list[str] = []
        for raw_line in screen.splitlines():
            line = raw_line.rstrip()
            if not line or line == "textscr":
                continue
            if line.startswith("QUASI88> "):
                line = line[len("QUASI88> ") :]
            if line == "QUASI88>":
                continue
            if line.strip() == _FUNCTION_KEY_GUIDE:
                continue
            if re.fullmatch(r"\*{5,}", line):
                continue
            if line.startswith("* QUASI88") or line.startswith("*    Enter"):
                continue
            lines.append(line.rstrip())
        while lines and not lines[-1].strip():
            lines.pop()
        return lines

    def _capture_run_screen_state(self, session: RunControlSession) -> dict[str, object]:
        lines: list[str] = []
        for raw_line in self._capture_run_screen_lines(session):
            normalized = raw_line.replace("\ufffd", "").strip()
            if normalized:
                lines.append(normalized)
        run_index = None
        for index, line in enumerate(lines):
            if line.lower() == "run":
                run_index = index
        if run_index is None:
            return {"lines": lines, "output_lines": [], "completed": False}
        post_run = lines[run_index + 1 :]
        output_lines = [line for line in post_run if line.lower() != "ok"]
        completed = any(line.lower() == "ok" for line in post_run)
        return {"lines": lines, "output_lines": output_lines, "completed": completed}

    def _drive_run_startup(self, session: RunControlSession, *, startup_timeout: float) -> bool:
        deadline = time.monotonic() + startup_timeout
        while time.monotonic() < deadline:
            ok, response = session.textscr()
            screen = response.get("fields", {}).get("text", "") if ok else ""
            if "Ok" in screen:
                return True
            session.wait(100)
            time.sleep(0.1)
        return False

    def _poll_run_completion(
        self,
        session: RunControlSession,
        *,
        timeout_seconds: float | None,
    ) -> tuple[bool, dict[str, object]]:
        deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
        last_state: dict[str, object] = {"lines": [], "output_lines": [], "completed": False}
        while deadline is None or time.monotonic() < deadline:
            session.wait(200)
            last_state = self._capture_run_screen_state(session)
            if bool(last_state["completed"]):
                return True, last_state
            time.sleep(0.2)
        return False, last_state

    def _emit_run_stdout(self, session: RunControlSession) -> None:
        lines = self._capture_run_screen_state(session)["output_lines"]
        if lines:
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()

    def _run_via_bridge(
        self,
        session: RunControlSession,
        *,
        filepath: str,
        timeout_seconds: float | None,
    ) -> int:
        ok, response = session.go()
        if not ok:
            code = response.get("code", RUN_ERR_PROTOCOL)
            print("error: failed to start execution", file=sys.stderr)
            return int(code)
        startup_timeout = max(timeout_seconds or 0.0, 5.0) if timeout_seconds is not None else 5.0
        if not self._drive_run_startup(session, startup_timeout=startup_timeout):
            print("error: n88basic batch run timed out", file=sys.stderr)
            return RUN_ERR_TIMEOUT
        ok, response = session.load(filepath, "ASCII")
        if not ok:
            return int(response.get("code", RUN_ERR_FILE_NOT_FOUND))
        ok, response = session.queue("RUN<CR>")
        if not ok:
            return int(response.get("code", RUN_ERR_PROTOCOL))
        completed, _ = self._poll_run_completion(session, timeout_seconds=timeout_seconds)
        if completed:
            self._emit_run_stdout(session)
            return RUN_ERR_OK
        print("error: n88basic batch run timed out", file=sys.stderr)
        return RUN_ERR_TIMEOUT

    def run_program(self, filepath: str, *, timeout_seconds: float | None) -> int:
        self._validate_line_numbers(filepath)
        n88basic_program_check.validate_program_path(Path(filepath))

        session = RunControlSession()
        if not self.ensure_run_bridge(session):
            print("error: control bridge unavailable", file=sys.stderr)
            session.disconnect()
            self.stop_quasi88()
            return RUN_ERR_HOOK_UNCONFIGURED
        try:
            return self._run_via_bridge(
                session,
                filepath=filepath,
                timeout_seconds=timeout_seconds,
            )
        finally:
            if self.quasi88_proc is not None and session.socket:
                try:
                    session.quit()
                except Exception:
                    pass
            session.disconnect()
            self.stop_quasi88()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="N88-BASIC interactive helper")
    parser.add_argument("--file")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--timeout")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    quasi88_bin = _resolve_quasi88_bin()
    rom_dir = _resolve_rom_dir()
    if not quasi88_bin.is_file():
        print(
            f"error: QUASI88 is not prepared: {quasi88_bin}\nRun ./setup/n88basic.sh first.",
            file=sys.stderr,
        )
        return 2
    if not rom_dir.is_dir():
        print(
            f"error: N88-BASIC ROMs are not prepared: {rom_dir}\nRun ./setup/n88basic.sh first.",
            file=sys.stderr,
        )
        return 2
    missing_rom_groups = _missing_required_rom_groups(rom_dir)
    if missing_rom_groups:
        missing_summary = ", ".join("/".join(group[:2]) for group in missing_rom_groups)
        print(
            f"error: required N88-BASIC ROMs are missing in {rom_dir}: {missing_summary}\n"
            "Run ./setup/n88basic.sh first.",
            file=sys.stderr,
        )
        return 2

    cli = N88BasicCLI(quasi88_bin=quasi88_bin, rom_dir=rom_dir)
    try:
        if args.run:
            if args.file is None:
                raise ValueError("--run requires --file PROGRAM.bas")
            timeout_spec = args.timeout
            if timeout_spec is None:
                timeout_spec = os.environ.get("CLASSIC_BASIC_N88_BATCH_TIMEOUT")
            timeout_seconds = fbasic_batch.parse_timeout_spec(timeout_spec)
            return cli.run_program(str(Path(args.file).resolve()), timeout_seconds=timeout_seconds)

        if args.timeout is not None:
            raise ValueError("--timeout requires --run --file PROGRAM.bas")

        if args.file is not None:
            return cli.run_file_interactive(str(Path(args.file).resolve()))
        return cli.run_interactive()
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        if args.timeout:
            print(f"error: n88basic batch run timed out after {args.timeout}", file=sys.stderr)
        else:
            print("error: n88basic batch run timed out", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
