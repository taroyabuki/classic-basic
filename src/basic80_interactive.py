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


_EXIT_SEQUENCE: tuple[tuple[bytes, float], ...] = (
    (b"SYSTEM\r", 0.5),
    (b"EXIT\r", 0.2),
)
_EXIT_GRACE_SECONDS = 1.0


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


def _terminate_subprocess(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.kill()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _run_bridge(*, runtime_dir: Path, startup_command: bytes | None) -> int:
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

    proc = subprocess.Popen(
        ["./RunCPM"],
        cwd=runtime_dir,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)

    exit_requested = False
    forced_exit = False
    exit_deadline: float | None = None
    startup_pending = startup_command

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
                    if not exit_requested:
                        os.write(stdout_fd, data)
                    if startup_pending is not None:
                        os.write(master_fd, startup_pending)
                        startup_pending = None

            if stdin_fd in ready:
                data = os.read(stdin_fd, 4096)
                if not data:
                    exit_requested = True
                    _send_exit_sequence(master_fd)
                    exit_deadline = time.monotonic() + _EXIT_GRACE_SECONDS
                    continue
                eof_index = data.find(b"\x04")
                if eof_index != -1:
                    if eof_index:
                        os.write(master_fd, data[:eof_index])
                    exit_requested = True
                    _send_exit_sequence(master_fd)
                    exit_deadline = time.monotonic() + _EXIT_GRACE_SECONDS
                    continue
                os.write(master_fd, data)

            if (
                exit_requested
                and exit_deadline is not None
                and proc.poll() is None
                and time.monotonic() >= exit_deadline
            ):
                forced_exit = True
                _terminate_subprocess(proc)
                break

        while True:
            try:
                data = os.read(master_fd, 4096)
            except OSError:
                break
            if not data:
                break
            if not exit_requested:
                os.write(stdout_fd, data)
    finally:
        if proc.poll() is None:
            _terminate_subprocess(proc)
        os.close(master_fd)
        if stdin_is_tty:
            signal.signal(signal.SIGWINCH, previous_sigwinch)
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_attrs)

    if exit_requested and (forced_exit or (proc.returncode is not None and proc.returncode != 0)):
        return 0
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BASIC-80 interactive PTY bridge")
    parser.add_argument("--runtime", required=True)
    parser.add_argument("--startup-command")
    args = parser.parse_args(argv)

    startup_command = args.startup_command.encode("ascii") if args.startup_command else None
    return _run_bridge(runtime_dir=Path(args.runtime), startup_command=startup_command)


if __name__ == "__main__":
    raise SystemExit(main())
