from __future__ import annotations

import argparse
import os
import select
import signal
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import BinaryIO

import fbasic_batch


_FM77AV_FILTERED_STARTUP_LINES = frozenset(
    {
        "Warning: -video none doesn't make much sense without -seconds_to_run",
        "fm77av      : fbasic30.rom (31744 bytes) - NEEDS REDUMP",
        "fm77av      : subsys_c.rom (10240 bytes) - NEEDS REDUMP",
        "fbasic30.rom ROM NEEDS REDUMP",
        "subsys_c.rom ROM NEEDS REDUMP",
        "romset fm77av [fm7] is best available",
        "1 romsets found, 1 were OK.",
        "WARNING: the machine might not run correctly.",
    }
)
_IGNORED_RUNTIME_LINES = ("Average speed:",)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="FM-7 BASIC interactive helper")
    parser.add_argument("--mame-command", required=True)
    parser.add_argument("--driver", required=True, choices=("fm7", "fm77av"))
    parser.add_argument("--rompath", required=True, type=Path)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--timeout")
    parser.add_argument("mame_args", nargs=argparse.REMAINDER)
    return parser


def _terminate_process(proc: subprocess.Popen[bytes]) -> int:
    if proc.poll() is None:
        terminated = False
        pid = getattr(proc, "pid", None)
        if isinstance(pid, int):
            try:
                os.killpg(os.getpgid(pid), signal.SIGTERM)
                terminated = True
            except OSError:
                terminated = False
        if not terminated:
            proc.terminate()
        try:
            return proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            killed = False
            if isinstance(pid, int):
                try:
                    os.killpg(os.getpgid(pid), signal.SIGKILL)
                    killed = True
                except OSError:
                    killed = False
            if not killed:
                proc.kill()
            return proc.wait(timeout=5.0)
    return proc.returncode or 0


def _request_interactive_exit(temp_dir: Path | None) -> Path | None:
    if temp_dir is None:
        return None
    exit_path = temp_dir / "exit.txt"
    exit_path.write_text("", encoding="ascii")
    return exit_path


def _shutdown_interactive_process(
    proc: subprocess.Popen[bytes],
    *,
    temp_dir: Path | None,
    graceful_timeout: float = 1.0,
) -> int:
    _request_interactive_exit(temp_dir)
    if proc.poll() is None:
        try:
            return proc.wait(timeout=graceful_timeout)
        except subprocess.TimeoutExpired:
            pass
    return _terminate_process(proc)


class _StartupOutputFilter:
    def __init__(self, filtered_lines: frozenset[str]) -> None:
        self._filtered_lines = filtered_lines
        self._remaining_lines = set(filtered_lines)
        self._passthrough = not filtered_lines
        self._lock = threading.Lock()

    def should_emit(self, line: bytes) -> bool:
        normalized = line.rstrip(b"\r\n").decode("utf-8", errors="replace")
        with self._lock:
            if any(normalized.startswith(prefix) for prefix in _IGNORED_RUNTIME_LINES):
                return False
            if self._passthrough:
                return True
            if normalized in self._remaining_lines:
                self._remaining_lines.remove(normalized)
                if not self._remaining_lines:
                    self._passthrough = True
                return False
            self._passthrough = True
            return True


class _ConsoleEchoFilter:
    def __init__(self, *, suppress_terminal_echo: bool) -> None:
        self._suppress_terminal_echo = suppress_terminal_echo
        self._pending_inputs: deque[str] = deque()
        self._active_input: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _normalize_text(text: str) -> str:
        return text.strip().upper()

    def record_input(self, line: bytes, *, suppress_echo: bool | None = None) -> None:
        should_suppress = self._suppress_terminal_echo if suppress_echo is None else suppress_echo
        if not should_suppress:
            return
        normalized = line.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        for raw_line in normalized.split("\n"):
            text = self._normalize_text(raw_line)
            if not text:
                continue
            with self._lock:
                self._pending_inputs.append(text)

    def filter_lines(self, lines: list[str]) -> list[str]:
        filtered: list[str] = []
        with self._lock:
            if not self._suppress_terminal_echo and not self._pending_inputs and self._active_input is None:
                return lines
            for line in lines:
                text = self._normalize_text(line)
                if text and self._active_input is not None:
                    pending = self._active_input
                    if text == pending or pending.startswith(text):
                        continue
                    if self._pending_inputs and self._pending_inputs[0] == pending:
                        self._pending_inputs.popleft()
                    self._active_input = None
                if text and self._pending_inputs:
                    pending = self._pending_inputs[0]
                    if text == pending or pending.startswith(text):
                        self._active_input = pending
                        continue
                filtered.append(line)
        return filtered


def _prime_console_echo_filter_for_file_load(
    console_echo_filter: _ConsoleEchoFilter,
    file_path: Path | None,
) -> None:
    if file_path is None:
        return
    initial_lines = fbasic_batch.normalize_program_lines(file_path)
    if not initial_lines:
        return
    console_echo_filter.record_input(
        "".join(f"{line}\n" for line in initial_lines).encode("ascii"),
        suppress_echo=True,
    )


def _emit_loaded_program_listing(file_path: Path | None) -> None:
    if file_path is None:
        return
    for line in fbasic_batch.normalize_program_lines(file_path):
        sys.stdout.write(f"{line}\n")
    sys.stdout.flush()


def _wait_for_gui_ready_output(startup_ready_event: threading.Event, *, timeout: float = 5.0) -> None:
    startup_ready_event.wait(timeout=timeout)


def _wait_for_headless_ready_output(output_path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    previous_lines: list[str] | None = None
    stable_ready_polls = 0
    while time.monotonic() < deadline:
        current_lines = fbasic_batch._read_text_capture_lines(output_path)
        if _snapshot_looks_stable(current_lines):
            if current_lines == previous_lines:
                stable_ready_polls += 1
            else:
                stable_ready_polls = 1
            if stable_ready_polls >= 2:
                return
        else:
            stable_ready_polls = 0
        previous_lines = current_lines
        time.sleep(0.1)


def _copy_stream(
    source: BinaryIO | None,
    sink: BinaryIO,
    startup_filter: _StartupOutputFilter | None = None,
    startup_ready_event: threading.Event | None = None,
) -> threading.Thread | None:
    if source is None:
        return None

    def worker() -> None:
        try:
            while True:
                line = source.readline()
                if not line:
                    break
                if startup_filter is not None and not startup_filter.should_emit(line):
                    continue
                sink.write(line)
                sink.flush()
                if startup_ready_event is not None and line.decode("utf-8", errors="replace").strip().lower() == "ready":
                    startup_ready_event.set()
        finally:
            source.close()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def _launch_interactive_session(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    file_path: Path | None,
    extra_mame_args: list[str],
    startup_ready_event: threading.Event | None = None,
) -> tuple[subprocess.Popen[bytes], Path | None]:
    startup_filter = None
    stdout = None
    stderr = None
    if driver == "fm77av":
        startup_filter = _StartupOutputFilter(_FM77AV_FILTERED_STARTUP_LINES)
        stdout = subprocess.PIPE
        stderr = subprocess.PIPE

    temp_dir, lua_path, input_path = fbasic_batch.prepare_fm7_interactive_session(
        driver=driver,
        program_path=file_path,
    )
    proc = fbasic_batch.launch_mame(
        mame_command=mame_command,
        rompath=rompath,
        driver=driver,
        disk_path=None,
        lua_path=lua_path,
        extra_mame_args=extra_mame_args,
        headless=False,
        stdout=stdout,
        stderr=stderr,
    )

    stdout_thread = _copy_stream(proc.stdout, sys.stdout.buffer, startup_filter, startup_ready_event)
    stderr_thread = _copy_stream(proc.stderr, sys.stderr.buffer, startup_filter, startup_ready_event)
    proc._classic_basic_io_threads = tuple(thread for thread in (stdout_thread, stderr_thread) if thread is not None)
    return proc, temp_dir, input_path


def _forward_stdin_to_input_queue(
    input_path: Path,
    console_echo_filter: _ConsoleEchoFilter | None = None,
) -> None:
    _stream_stdin_to_input_queue(
        input_path,
        console_echo_filter=console_echo_filter,
    )


def _append_input_bytes(
    input_path: Path,
    chunk: bytes,
    console_echo_filter: _ConsoleEchoFilter | None = None,
) -> None:
    normalized = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if not normalized:
        return
    if console_echo_filter is not None:
        console_echo_filter.record_input(normalized)
    with input_path.open("ab") as queue_handle:
        queue_handle.write(normalized)
        queue_handle.flush()


def _stream_stdin_to_input_queue(
    input_path: Path,
    console_echo_filter: _ConsoleEchoFilter | None = None,
) -> None:
    stdin_fd = sys.stdin.fileno()
    while True:
        ready, _, _ = select.select([stdin_fd], [], [], 0.1)
        if not ready:
            continue
        chunk = os.read(stdin_fd, 4096)
        if not chunk:
            return
        eof_index = chunk.find(b"\x04")
        if eof_index != -1:
            if eof_index:
                _append_input_bytes(
                    input_path,
                    chunk[:eof_index],
                    console_echo_filter=console_echo_filter,
                )
            return
        _append_input_bytes(
            input_path,
            chunk,
            console_echo_filter=console_echo_filter,
        )


def _join_io_threads(proc: subprocess.Popen[bytes]) -> None:
    threads = getattr(proc, "_classic_basic_io_threads", None)
    if isinstance(threads, tuple):
        for thread in threads:
            thread.join(timeout=1.0)


def _snapshot_looks_stable(lines: list[str]) -> bool:
    if not lines:
        return False
    return fbasic_batch.is_ready_line(lines[-1])


def _copy_console_snapshot(
    output_path: Path,
    stop_event: threading.Event,
    console_echo_filter: _ConsoleEchoFilter | None = None,
) -> threading.Thread:
    def worker() -> None:
        previous_lines: list[str] = []
        while not stop_event.is_set():
            current_lines = fbasic_batch._read_text_capture_lines(output_path)
            diff_lines = fbasic_batch._emit_console_diff(previous_lines, current_lines)
            if console_echo_filter is not None:
                diff_lines = console_echo_filter.filter_lines(diff_lines)
            for line in diff_lines:
                sys.stdout.write(f"{line}\n")
                sys.stdout.flush()
            previous_lines = current_lines
            time.sleep(0.2)
        current_lines = fbasic_batch._read_text_capture_lines(output_path)
        diff_lines = fbasic_batch._emit_console_diff(previous_lines, current_lines)
        if console_echo_filter is not None:
            diff_lines = console_echo_filter.filter_lines(diff_lines)
        for line in diff_lines:
            sys.stdout.write(f"{line}\n")
            sys.stdout.flush()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


def run_interactive_session(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    file_path: Path | None,
    extra_mame_args: list[str],
) -> int:
    if not os.environ.get("DISPLAY"):
        return run_headless_interactive_session(
            mame_command=mame_command,
            rompath=rompath,
            driver=driver,
            file_path=file_path,
            extra_mame_args=extra_mame_args,
        )

    proc: subprocess.Popen[bytes] | None = None
    temp_dir: Path | None = None
    input_path: Path | None = None
    startup_ready_event = threading.Event()
    try:
        proc, temp_dir, input_path = _launch_interactive_session(
            mame_command=mame_command,
            rompath=rompath,
            driver=driver,
            file_path=file_path,
            extra_mame_args=extra_mame_args,
            startup_ready_event=startup_ready_event,
        )
        _wait_for_gui_ready_output(startup_ready_event)
        _emit_loaded_program_listing(file_path)
        try:
            _forward_stdin_to_input_queue(input_path)
        except KeyboardInterrupt:
            pass
        _shutdown_interactive_process(proc, temp_dir=temp_dir)
        _join_io_threads(proc)
        return 0
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def run_headless_interactive_session(
    *,
    mame_command: str,
    rompath: Path,
    driver: str,
    file_path: Path | None,
    extra_mame_args: list[str],
) -> int:
    proc: subprocess.Popen[bytes] | None = None
    temp_dir: Path | None = None
    input_path: Path | None = None
    stop_event = threading.Event()
    console_echo_filter = _ConsoleEchoFilter(
        suppress_terminal_echo=bool(getattr(sys.stdin, "isatty", lambda: False)())
    )
    _prime_console_echo_filter_for_file_load(console_echo_filter, file_path)
    try:
        startup_filter = None
        stdout = None
        stderr = None
        if driver == "fm77av":
            startup_filter = _StartupOutputFilter(_FM77AV_FILTERED_STARTUP_LINES)
            stdout = subprocess.PIPE
            stderr = subprocess.PIPE

        temp_dir, lua_path, input_path, output_path = fbasic_batch.prepare_fm7_headless_interactive_session(
            driver=driver,
            program_path=file_path,
        )
        proc = fbasic_batch.launch_mame(
            mame_command=mame_command,
            rompath=rompath,
            driver=driver,
            disk_path=None,
            lua_path=lua_path,
            extra_mame_args=extra_mame_args,
            headless=True,
            stdout=stdout,
            stderr=stderr,
        )

        stdout_thread = _copy_stream(proc.stdout, sys.stdout.buffer, startup_filter)
        stderr_thread = _copy_stream(proc.stderr, sys.stderr.buffer, startup_filter)
        console_thread = _copy_console_snapshot(output_path, stop_event, console_echo_filter)
        proc._classic_basic_io_threads = tuple(
            thread
            for thread in (stdout_thread, stderr_thread, console_thread)
            if thread is not None
        )
        _wait_for_headless_ready_output(output_path)
        _emit_loaded_program_listing(file_path)
        try:
            _forward_stdin_to_input_queue(input_path, console_echo_filter)
        except KeyboardInterrupt:
            pass
        stop_event.set()
        _shutdown_interactive_process(proc, temp_dir=temp_dir)
        _join_io_threads(proc)
        return 0
    finally:
        stop_event.set()
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    extra_mame_args = list(args.mame_args)
    if extra_mame_args and extra_mame_args[0] == "--":
        extra_mame_args = extra_mame_args[1:]

    try:
        if args.run:
            if args.file is None:
                raise ValueError("--run requires --file PROGRAM.bas")
            timeout_seconds = fbasic_batch.parse_timeout_spec(args.timeout)
            return fbasic_batch.run_batch(
                mame_command=args.mame_command,
                driver=args.driver,
                rompath=args.rompath,
                disk_path=None,
                program_path=args.file,
                timeout_seconds=timeout_seconds,
                extra_mame_args=extra_mame_args,
            )
        if args.timeout is not None:
            raise ValueError("--timeout requires --run --file PROGRAM.bas")
        return run_interactive_session(
            mame_command=args.mame_command,
            rompath=args.rompath,
            driver=args.driver,
            file_path=args.file,
            extra_mame_args=extra_mame_args,
        )
    except KeyboardInterrupt:
        return 130
    except (RuntimeError, ValueError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        if args.timeout:
            print(f"error: fm7basic batch run timed out after {args.timeout}", file=sys.stderr)
        else:
            print("error: fm7basic batch run timed out", file=sys.stderr)
        return 124


if __name__ == "__main__":
    raise SystemExit(main())
