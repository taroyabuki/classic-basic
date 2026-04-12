"""Run RunCPM until it returns to the CP/M prompt, then exit cleanly."""
from __future__ import annotations

import argparse
import errno
import os
import pty
import re
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


_PARENT_POLL_SECONDS = 0.5
_PARENT_EXIT_STATUS = 125
_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_OSC_RE = re.compile(r"\x1b\][^\x07]*(?:\x07|\x1b\\)")
_ESC_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\([0-9A-Za-z])")
_BASIC80_STARTUP_RE = re.compile(
    r"^(?:"
    r"CP/M Emulator v.*|"
    r"Built [A-Z][a-z]{2}\s+\d{1,2}\s+\d{4}.*|"
    r"-{8,}|"
    r"CPU is Model.*|"
    r"[0-9]+ T-states in [0-9]+ ms.*|"
    r"Estimated Z80 clock speed:.*|"
    r"BIOS at .*|"
    r"BIOS/BDOS using .*|"
    r"CCP .* at .*|"
    r"FILEBASE is .*|"
    r"RunCPM Version .*|"
    r"BASIC-85 Rev\..*|"
    r"\[CP/M Version\]|"
    r"Copyright .*Microsoft.*|"
    r"Created: .*|"
    r"[0-9]+ Bytes free"
    r")$"
)
_BASIC80_SHUTDOWN_RE = re.compile(r"^(?:SYSTEM|RunCPM Version .*)$")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", required=True, help="Prepared RunCPM runtime directory")
    parser.add_argument("--prompt", default="A0>", help="CP/M prompt to watch for")
    parser.add_argument("--exit-command", default="EXIT\r", help="Command sent once the prompt is seen")
    parser.add_argument("--intermediate-prompt", default="", help="Optional earlier prompt to handle before the final CP/M prompt")
    parser.add_argument(
        "--intermediate-command",
        default="",
        help="Command sent once when the intermediate prompt is seen",
    )
    parser.add_argument(
        "--timeout",
        default="",
        help="Maximum wall time (for example 30, 2.5, 90s, 2m). Empty or 0 disables the timeout.",
    )
    parser.add_argument(
        "--output-filter",
        choices=("basic80",),
        default="",
        help="Optional output cleanup mode for wrapper-specific batch noise.",
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
    trimmed_end = len(buffer)
    while trimmed_end > 0 and buffer[trimmed_end - 1] in b"\r\n ":
        trimmed_end -= 1
    if trimmed_end < len(prompt):
        return None
    start = trimmed_end - len(prompt)
    if buffer[start:trimmed_end] != prompt:
        return None
    if start > 0 and buffer[start - 1] not in b"\r\n":
        return None
    return start


def _prompt_starts(buffer: bytes, prompt: bytes) -> list[int]:
    starts: list[int] = []
    primary = _prompt_start(buffer, prompt)
    if primary is not None:
        starts.append(primary)
    # RunCPM can present the CCP prompt either as "A0>" or "A>" depending on
    # the active CCP build. Accept both when the caller asks for the canonical
    # "A0>" prompt.
    if prompt == b"A0>":
        fallback = _prompt_start(buffer, b"A>")
        if fallback is not None and fallback not in starts:
            starts.append(fallback)
    return starts


def _parent_death_preexec_fn() -> object | None:
    if not sys.platform.startswith("linux"):
        return None

    def _set_parent_death_signal() -> None:
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))

    return _set_parent_death_signal


def _spawn_child(runtime_dir: Path) -> tuple[subprocess.Popen[bytes], int, object | None]:
    use_pipes = os.environ.get("CLASSIC_BASIC_RUNCPM_BATCH_NO_PTY") == "1"
    preexec_fn = _parent_death_preexec_fn()
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
                preexec_fn=preexec_fn,
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
        preexec_fn=preexec_fn,
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


def _normalize_command(command: str) -> bytes:
    if not command:
        return b""
    if command.endswith(("\r", "\n")):
        return command.encode("ascii")
    return f"{command}\r".encode("ascii")


def _emit_chunk(chunk: bytes, captured_output: list[bytes] | None) -> None:
    if not chunk:
        return
    if captured_output is not None:
        captured_output.append(chunk)
        return
    sys.stdout.buffer.write(chunk)
    sys.stdout.buffer.flush()


def _filter_basic80_output(text: str) -> str:
    text = _OSC_RE.sub("\n", text)
    text = _CSI_RE.sub("\n", text)
    text = _ESC_RE.sub("\n", text)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").splitlines()

    filtered: list[str] = []
    emitting = False
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if not emitting:
            if stripped == "Ok" or _BASIC80_STARTUP_RE.match(stripped):
                continue
            emitting = True
        if _BASIC80_SHUTDOWN_RE.match(stripped):
            continue
        filtered.append(line)

    if not filtered:
        return ""
    return "\n".join(filtered) + "\n"


def _emit_filtered_output(captured_output: list[bytes], output_filter: str) -> None:
    if not captured_output:
        return
    text = b"".join(captured_output).decode("utf-8", errors="replace")
    if output_filter == "basic80":
        text = _filter_basic80_output(text)
    if text:
        sys.stdout.write(text)
        sys.stdout.flush()


def _terminate_child(proc: subprocess.Popen[bytes]) -> int:
    if proc.poll() is not None:
        return proc.wait()
    proc.terminate()
    try:
        return proc.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait()


def run_batch(
    runtime_dir: Path,
    prompt: str,
    exit_command: str,
    timeout_seconds: float | None,
    *,
    intermediate_prompt: str = "",
    intermediate_command: str = "",
    output_filter: str = "",
) -> int:
    proc, read_fd, write_target = _spawn_child(runtime_dir)

    prompt_bytes = prompt.encode("ascii")
    exit_bytes = _normalize_command(exit_command)
    intermediate_prompt_bytes = intermediate_prompt.encode("ascii") if intermediate_prompt else b""
    intermediate_command_bytes = _normalize_command(intermediate_command)
    lookback = max(len(prompt_bytes) + 2, len(intermediate_prompt_bytes) + 2, 16)
    pending = b""
    intermediate_sent = False
    exit_sent = False
    original_parent_pid = os.getppid()
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    captured_output: list[bytes] | None = [] if output_filter else None

    try:
        while True:
            if os.getppid() != original_parent_pid:
                _terminate_child(proc)
                if captured_output is not None:
                    _emit_filtered_output(captured_output, output_filter)
                return _PARENT_EXIT_STATUS

            wait_timeout = _PARENT_POLL_SECONDS
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    proc.kill()
                    proc.wait()
                    return 124
                wait_timeout = min(wait_timeout, remaining)

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
            if intermediate_prompt_bytes and not intermediate_sent:
                intermediate_starts = _prompt_starts(pending, intermediate_prompt_bytes)
                if intermediate_starts:
                    intermediate_start = min(intermediate_starts)
                    if intermediate_start > 0:
                        _emit_chunk(pending[:intermediate_start], captured_output)
                    _send_exit(write_target, intermediate_command_bytes)
                    intermediate_sent = True
                    pending = b""
                    continue

            prompt_starts = _prompt_starts(pending, prompt_bytes)
            if prompt_starts:
                prompt_start = min(prompt_starts)
                if prompt_start > 0:
                    _emit_chunk(pending[:prompt_start], captured_output)
                _send_exit(write_target, exit_bytes)
                exit_sent = True
                pending = b""
                continue

            flush_upto = max(0, len(pending) - lookback)
            if flush_upto > 0:
                _emit_chunk(pending[:flush_upto], captured_output)
                pending = pending[flush_upto:]

        if not exit_sent and pending:
            _emit_chunk(pending, captured_output)
        returncode = proc.wait()
        if captured_output is not None:
            _emit_filtered_output(captured_output, output_filter)
        return returncode
    finally:
        if write_target is not None and not isinstance(write_target, int):
            write_target.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            os.close(read_fd)
        except OSError:
            pass


def main() -> int:
    args = _parse_args()
    try:
        timeout_seconds = _parse_timeout_spec(args.timeout)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return run_batch(
        Path(args.runtime),
        args.prompt,
        args.exit_command,
        timeout_seconds,
        intermediate_prompt=args.intermediate_prompt,
        intermediate_command=args.intermediate_command,
        output_filter=args.output_filter,
    )


if __name__ == "__main__":
    raise SystemExit(main())
