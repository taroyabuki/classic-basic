from __future__ import annotations

import argparse
import os
import pty
import select
import signal
import subprocess
import sys
import termios
import time
import tty
from pathlib import Path
from typing import Sequence


_MIN_ROWS = 25
_MIN_COLS = 80
_STARTUP_SEQUENCE: tuple[tuple[bytes, float], ...] = (
    (b"\x1b", 0.35),
    (b"\t\r", 0.2),
    (b"\x1b[17~", 0.2),
)
_EXIT_SEQUENCE: tuple[tuple[bytes, float], ...] = (
    *_STARTUP_SEQUENCE,
    (b"SYSTEM\r", 0.0),
)
_STARTUP_TRIGGER = b"Immediate"
_READY_MARKERS: tuple[bytes, ...] = (b"Enter=Execute Line>", b"Enter=Execute")
_STARTUP_SETTLE_SECONDS = 0.35
_STARTUP_SCAN_LIMIT = 16384


def _get_winsize(fd: int) -> tuple[int, int] | None:
    try:
        rows, cols = termios.tcgetwinsize(fd)
    except (AttributeError, OSError):
        return None
    return rows, cols


def _copy_winsize(source_fd: int, target_fd: int) -> None:
    winsize = _get_winsize(source_fd)
    if winsize is None:
        return
    rows, cols = winsize
    try:
        termios.tcsetwinsize(target_fd, (max(rows, _MIN_ROWS), max(cols, _MIN_COLS)))
    except (AttributeError, OSError):
        pass


def _send_sequence(master_fd: int, sequence: Sequence[tuple[bytes, float]]) -> None:
    for payload, pause in sequence:
        os.write(master_fd, payload)
        if pause > 0:
            time.sleep(pause)


def _send_startup_sequence(master_fd: int) -> None:
    _send_sequence(master_fd, _STARTUP_SEQUENCE)


def _send_exit_sequence(master_fd: int) -> None:
    _send_sequence(master_fd, _EXIT_SEQUENCE)


def _validate_winsize(stdin_fd: int, stderr_fd: int) -> bool:
    winsize = _get_winsize(stdin_fd)
    if winsize is None:
        return True
    rows, cols = winsize
    if rows >= _MIN_ROWS and cols >= _MIN_COLS:
        return True
    os.write(
        stderr_fd,
        (
            f"error: QBasic interactive mode requires a terminal of at least "
            f"{_MIN_COLS}x{_MIN_ROWS}; resize the terminal and rerun.\n"
        ).encode("utf-8"),
    )
    return False


def _run_bridge(command: Sequence[str], *, home_dir: str, log_file: Path) -> int:
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stdin_is_tty = os.isatty(stdin_fd)
    stderr_fd = sys.stderr.fileno()
    if stdin_is_tty and not _validate_winsize(stdin_fd, stderr_fd):
        return 2

    original_attrs = termios.tcgetattr(stdin_fd) if stdin_is_tty else None
    master_fd, slave_fd = pty.openpty()
    _copy_winsize(stdin_fd, master_fd)
    _copy_winsize(stdin_fd, slave_fd)

    def _handle_sigwinch(signum: int, frame: object) -> None:
        del signum, frame
        _copy_winsize(stdin_fd, master_fd)

    previous_sigwinch = signal.getsignal(signal.SIGWINCH)
    if stdin_is_tty:
        tty.setraw(stdin_fd)
        signal.signal(signal.SIGWINCH, _handle_sigwinch)

    log_handle = log_file.open("ab")
    proc = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=log_handle,
        env={**os.environ, "HOME": home_dir},
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    exit_requested = False
    startup_ready = False
    startup_sent = False
    startup_due_at: float | None = None
    startup_scan = bytearray()
    pending_input = bytearray()
    try:
        while True:
            if proc.poll() is not None:
                break

            read_fds = [master_fd]
            if not exit_requested:
                read_fds.append(stdin_fd)
            timeout = 0.1
            if startup_due_at is not None:
                timeout = max(0.0, min(timeout, startup_due_at - time.monotonic()))
            ready, _, _ = select.select(read_fds, [], [], timeout)

            if startup_due_at is not None and time.monotonic() >= startup_due_at:
                _send_startup_sequence(master_fd)
                startup_sent = True
                startup_due_at = None

            if master_fd in ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if data:
                    os.write(stdout_fd, data)
                    if not startup_ready:
                        startup_scan.extend(data)
                        if len(startup_scan) > _STARTUP_SCAN_LIMIT:
                            del startup_scan[:-_STARTUP_SCAN_LIMIT]
                        if not startup_sent and startup_due_at is None and _STARTUP_TRIGGER in startup_scan:
                            startup_due_at = time.monotonic() + _STARTUP_SETTLE_SECONDS
                        if startup_sent and any(marker in startup_scan for marker in _READY_MARKERS):
                            startup_ready = True
                            if pending_input:
                                os.write(master_fd, pending_input)
                                pending_input.clear()

            if stdin_fd in ready:
                data = os.read(stdin_fd, 4096)
                if not data:
                    exit_requested = True
                    startup_due_at = None
                    _send_exit_sequence(master_fd)
                    continue
                eof_index = data.find(b"\x04")
                if eof_index != -1:
                    if eof_index:
                        if startup_ready:
                            os.write(master_fd, data[:eof_index])
                        else:
                            pending_input.extend(data[:eof_index])
                    exit_requested = True
                    startup_due_at = None
                    _send_exit_sequence(master_fd)
                    continue
                if startup_ready:
                    os.write(master_fd, data)
                else:
                    pending_input.extend(data)

        while True:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            os.write(stdout_fd, data)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait(timeout=5)
        os.close(master_fd)
        log_handle.close()
        if stdin_is_tty:
            signal.signal(signal.SIGWINCH, previous_sigwinch)
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_attrs)

    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QBasic interactive PTY bridge")
    parser.add_argument("--home", required=True)
    parser.add_argument("--log-file", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command")
    return _run_bridge(command, home_dir=args.home, log_file=Path(args.log_file))


if __name__ == "__main__":
    raise SystemExit(main())
