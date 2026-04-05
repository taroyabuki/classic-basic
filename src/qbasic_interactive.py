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


_EXIT_SEQUENCE: tuple[tuple[bytes, float], ...] = (
    (b"\x1b", 0.35),
    (b"\t\r", 0.2),
    (b"\x1b[17~", 0.2),
    (b"SYSTEM\r", 0.0),
)


def _copy_winsize(source_fd: int, target_fd: int) -> None:
    try:
        rows, cols = termios.tcgetwinsize(source_fd)
        termios.tcsetwinsize(target_fd, (rows, cols))
    except (AttributeError, OSError):
        pass


def _send_exit_sequence(master_fd: int) -> None:
    for payload, pause in _EXIT_SEQUENCE:
        os.write(master_fd, payload)
        if pause > 0:
            time.sleep(pause)


def _run_bridge(command: Sequence[str], *, home_dir: str, log_file: Path) -> int:
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    stdin_is_tty = os.isatty(stdin_fd)
    original_attrs = termios.tcgetattr(stdin_fd) if stdin_is_tty else None
    master_fd, slave_fd = pty.openpty()
    _copy_winsize(stdin_fd, slave_fd)

    def _handle_sigwinch(signum: int, frame: object) -> None:
        del signum, frame
        _copy_winsize(stdin_fd, slave_fd)

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
    try:
        while True:
            if proc.poll() is not None:
                break

            read_fds = [master_fd]
            if not exit_requested:
                read_fds.append(stdin_fd)
            ready, _, _ = select.select(read_fds, [], [], 0.1)

            if master_fd in ready:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    data = b""
                if data:
                    os.write(stdout_fd, data)

            if stdin_fd in ready:
                data = os.read(stdin_fd, 4096)
                if not data:
                    exit_requested = True
                    _send_exit_sequence(master_fd)
                    continue
                eof_index = data.find(b"\x04")
                if eof_index != -1:
                    if eof_index:
                        os.write(master_fd, data[:eof_index])
                    exit_requested = True
                    _send_exit_sequence(master_fd)
                    continue
                os.write(master_fd, data)

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
