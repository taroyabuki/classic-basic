"""Run RunCPM until it returns to the CP/M prompt, then exit cleanly."""
from __future__ import annotations

import argparse
import errno
import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, help="Prepared RunCPM runtime directory")
    parser.add_argument("--prompt", default="A0>", help="CP/M prompt to watch for")
    parser.add_argument("--exit-command", default="EXIT\r", help="Command sent once the prompt is seen")
    parser.add_argument(
        "--timeout",
        default="",
        help="Maximum wall time (for example 30, 2.5, 90s, 2m). Empty or 0 disables the timeout.",
    )
    return parser.parse_args()


def _parse_timeout_spec(spec: str) -> float | None:
    text = spec.strip()
    if text in {"", "0"}:
        return None

    import re

    match = re.fullmatch(r"([0-9]+(?:\.[0-9]*)?|[0-9]*\.[0-9]+)\s*([smhdSMHD]?)", text)
    if match is None:
        raise ValueError(f"invalid timeout spec: {spec}")

    value = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    factor = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}[unit]
    return value * factor


def _prompt_start(buffer: bytes, prompt: bytes) -> int | None:
    if len(buffer) < len(prompt):
        return None
    start = len(buffer) - len(prompt)
    if buffer[start:] != prompt:
        return None
    if start > 0 and buffer[start - 1] not in b"\r\n":
        return None
    return start


def _spawn_child(runtime_dir: Path) -> tuple[subprocess.Popen[bytes], int, object | None]:
    use_pipes = os.environ.get("CLASSIC_BASIC_RUNCPM_BATCH_NO_PTY") == "1"
    if not use_pipes:
        try:
            master_fd, slave_fd = pty.openpty()
        except OSError:
            use_pipes = True
        else:
            proc = subprocess.Popen(
                [str(runtime_dir / "RunCPM")],
                cwd=runtime_dir,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
            )
            os.close(slave_fd)
            return proc, master_fd, master_fd

    proc = subprocess.Popen(
        [str(runtime_dir / "RunCPM")],
        cwd=runtime_dir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        close_fds=True,
    )
    assert proc.stdout is not None
    return proc, proc.stdout.fileno(), proc.stdin


def _send_exit(write_target: int | object | None, exit_bytes: bytes) -> None:
    if write_target is None:
        return
    if isinstance(write_target, int):
        os.write(write_target, exit_bytes)
        return
    write_target.write(exit_bytes)
    write_target.flush()


def run_batch(runtime_dir: Path, prompt: str, exit_command: str, timeout_seconds: float | None) -> int:
    proc, read_fd, write_target = _spawn_child(runtime_dir)

    prompt_bytes = prompt.encode("ascii")
    exit_bytes = exit_command.encode("ascii")
    lookback = max(len(prompt_bytes) + 2, 16)
    pending = b""
    exit_sent = False
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds

    try:
        while True:
            wait_timeout = None
            if deadline is not None:
                wait_timeout = deadline - time.monotonic()
                if wait_timeout <= 0:
                    proc.kill()
                    proc.wait()
                    return 124

            ready, _, _ = select.select([read_fd], [], [], wait_timeout)
            if not ready:
                continue

            try:
                chunk = os.read(read_fd, 4096)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    chunk = b""
                else:
                    raise

            if not chunk:
                break

            if exit_sent:
                continue

            pending += chunk
            prompt_start = _prompt_start(pending, prompt_bytes)
            if prompt_start is not None:
                if prompt_start > 0:
                    sys.stdout.buffer.write(pending[:prompt_start])
                    sys.stdout.buffer.flush()
                _send_exit(write_target, exit_bytes)
                exit_sent = True
                pending = b""
                continue

            flush_upto = max(0, len(pending) - lookback)
            if flush_upto > 0:
                sys.stdout.buffer.write(pending[:flush_upto])
                sys.stdout.buffer.flush()
                pending = pending[flush_upto:]

        if not exit_sent and pending:
            sys.stdout.buffer.write(pending)
            sys.stdout.buffer.flush()
        return proc.wait()
    finally:
        if write_target is not None and not isinstance(write_target, int):
            write_target.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        os.close(read_fd)


def main() -> int:
    args = _parse_args()
    try:
        timeout_seconds = _parse_timeout_spec(args.timeout)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return run_batch(Path(args.runtime), args.prompt, args.exit_command, timeout_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
