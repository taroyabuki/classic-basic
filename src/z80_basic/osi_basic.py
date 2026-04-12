from __future__ import annotations

import argparse
import io
import os
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from py65.devices.mpu6502 import MPU
from py65.memory import ObservableMemory

from .terminal import RawTerminal

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_OSI_BASIC_ROM = ROOT_DIR / "third_party" / "msbasic" / "osi.bin"

OSI_BASIC_LOAD_ADDRESS = 0xA000
OSI_BASIC_COLD_START = 0xBD11
OSI_MONRDKEY = 0xFFEB
OSI_MONCOUT = 0xFFEE
OSI_MONISCNTC = 0xFFF1
OSI_LOAD = 0xFFF4
OSI_SAVE = 0xFFF7


class Console(Protocol):
    def read_byte(self) -> int: ...

    def write_byte(self, value: int) -> None: ...


class ByteWriter(Protocol):
    def write(self, data: bytes) -> object: ...


class UnsupportedMonitorCall(RuntimeError):
    """Raised when the emulated monitor call is not implemented."""


def _broken_pipe_exit_code() -> int:
    try:
        sys.stdout.flush()
    except BrokenPipeError:
        pass
    except OSError:
        return 141
    else:
        return 141
    try:
        devnull_fd = os.open(os.devnull, os.O_WRONLY)
    except OSError:
        return 141
    try:
        os.dup2(devnull_fd, sys.stdout.fileno())
    except OSError:
        pass
    finally:
        os.close(devnull_fd)
    return 141


@dataclass(frozen=True)
class OsiBasicResult:
    reason: str
    steps: int
    pc: int


class SessionConsole:
    def __init__(
        self,
        staged_input: bytes = b"",
        terminal: RawTerminal | None = None,
        output_buffer: ByteWriter | None = None,
    ) -> None:
        self._buffer = deque(staged_input)
        self._terminal = terminal
        self._output_buffer = output_buffer
        self._rubout_pending = False

    def read_byte(self) -> int:
        if self._buffer:
            raw = self._buffer.popleft()
        elif self._terminal is None:
            return 0x04
        else:
            raw = self._terminal.read_byte()

        is_backspace = (raw & 0x7F) in (0x08, 0x7F)
        value = self._normalize_input(raw)
        if is_backspace and self._terminal is not None:
            self._rubout_pending = True
        return value

    def write_byte(self, value: int) -> None:
        byte_value = value & 0x7F

        if self._terminal is not None:
            if byte_value == 0x5F and self._rubout_pending:
                self._rubout_pending = False
                self._terminal.write_byte(0x08)
                self._terminal.write_byte(0x20)
                self._terminal.write_byte(0x08)
                return
            self._rubout_pending = False
            self._terminal.write_byte(byte_value)
            return

        if self._output_buffer is not None:
            self._output_buffer.write(bytes([byte_value]))
            return

        os.write(sys.stdout.fileno(), bytes([byte_value]))

    @staticmethod
    def _normalize_input(value: int) -> int:
        value &= 0x7F
        if 0x61 <= value <= 0x7A:
            return value - 0x20
        if value == 0x0A:
            return 0x0D
        if value in (0x08, 0x7F):
            return 0x5F
        return value


class StreamingFileRunOutput:
    def __init__(self, stream: io.TextIOBase) -> None:
        self._stream = stream
        self._line = bytearray()
        self._saw_run = False
        self._pending_line: str | None = None

    def write(self, data: bytes) -> int:
        for value in data:
            if value in (0x0A, 0x0D):
                self._finish_line()
            else:
                self._line.append(value & 0x7F)
        return len(data)

    def finish(self) -> None:
        self._finish_line()
        self._flush_pending(final=True)

    def _finish_line(self) -> None:
        text = self._line.decode("ascii", errors="replace").rstrip()
        self._line.clear()
        if not self._saw_run:
            if text.strip().upper() == "RUN":
                self._saw_run = True
            return
        if not text.strip():
            return
        self._flush_pending()
        self._pending_line = text

    def _flush_pending(self, *, final: bool = False) -> None:
        if self._pending_line is None:
            return
        if final and self._pending_line.strip().upper() == "OK":
            self._pending_line = None
            return
        self._stream.write(f"{self._pending_line}\n")
        self._stream.flush()
        self._pending_line = None


class OsiBasicMachine:
    def __init__(self, rom_path: Path = DEFAULT_OSI_BASIC_ROM) -> None:
        resolved = rom_path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"OSI BASIC ROM not found: {resolved}")

        self.rom_path = resolved
        self.memory = ObservableMemory(subject=[0x00] * 0x10000)
        self.memory.write(OSI_BASIC_LOAD_ADDRESS, resolved.read_bytes())
        self.mpu = MPU(memory=self.memory, pc=OSI_BASIC_COLD_START)

    def run(self, console: Console, max_steps: int = 5_000_000) -> OsiBasicResult:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")

        for steps in range(1, max_steps + 1):
            result = self._handle_monitor_call(console, steps)
            if result is not None:
                return result
            self.mpu.step()

        return OsiBasicResult(reason="step_limit", steps=max_steps, pc=self.mpu.pc)

    def _handle_monitor_call(
        self,
        console: Console,
        steps: int,
    ) -> OsiBasicResult | None:
        pc = self.mpu.pc

        if pc == OSI_MONCOUT:
            console.write_byte(self.mpu.a)
            self._return_from_subroutine()
            return None

        if pc == OSI_MONRDKEY:
            value = console.read_byte()
            if value == 0x04:
                return OsiBasicResult(reason="eof", steps=steps, pc=pc)
            self.mpu.a = value
            self._return_from_subroutine()
            return None

        if pc == OSI_MONISCNTC:
            self.mpu.a = 0x00
            self._return_from_subroutine()
            return None

        if pc == OSI_LOAD:
            raise UnsupportedMonitorCall("OSI BASIC LOAD is not implemented")

        if pc == OSI_SAVE:
            raise UnsupportedMonitorCall("OSI BASIC SAVE is not implemented")

        return None

    def _return_from_subroutine(self) -> None:
        self.mpu.pc = (self.mpu.stPopWord() + 1) & 0xFFFF


def build_boot_input(memory_size: int = 8191, terminal_width: int = 72) -> bytes:
    if memory_size <= 0:
        raise ValueError("memory_size must be positive")
    if terminal_width <= 0:
        raise ValueError("terminal_width must be positive")
    return f"{memory_size}\r{terminal_width}\r".encode("ascii")


def normalize_ascii_text(source: str) -> bytes:
    text = source.replace("\r\n", "\n").replace("\r", "\n")
    if text and not text.endswith("\n"):
        text += "\n"
    return text.replace("\n", "\r").encode("ascii")


def load_basic_program(program_path: Path) -> bytes:
    try:
        return normalize_ascii_text(program_path.read_text(encoding="ascii"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"program must be ASCII: {program_path}") from exc


def build_staged_input(
    *,
    program_path: Path | None,
    exec_text: str | None,
    stdin_bytes: bytes = b"",
    memory_size: int = 8191,
    terminal_width: int = 72,
    run: bool = True,
) -> bytes:
    staged = bytearray(build_boot_input(memory_size=memory_size, terminal_width=terminal_width))

    if program_path is not None:
        staged.extend(load_basic_program(program_path))
        if run:
            staged.extend(b"RUN\r")

    if exec_text is not None:
        staged.extend(normalize_ascii_text(exec_text))

    staged.extend(stdin_bytes)
    return bytes(staged)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m z80_basic.osi_basic",
        description="Run OSI Microsoft BASIC for 6502 on a terminal-oriented 6502 emulator",
    )
    parser.add_argument(
        "--rom",
        type=Path,
        default=DEFAULT_OSI_BASIC_ROM,
        help="Path to the linked OSI BASIC binary",
    )
    parser.add_argument(
        "--exec",
        dest="exec_text",
        help="Execute one or more direct-mode statements after boot",
    )
    parser.add_argument(
        "--memory-size",
        type=int,
        default=8191,
        help="Cold-start response for MEMORY SIZE?",
    )
    parser.add_argument(
        "--terminal-width",
        type=int,
        default=72,
        help="Cold-start response for TERMINAL WIDTH?",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=5_000_000,
        help="Instruction budget before aborting the emulation",
    )
    parser.add_argument(
        "program",
        nargs="?",
        type=Path,
        help="ASCII .bas file to type into OSI BASIC before RUN",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help=(
            "Interactive mode: load PROGRAM.bas into memory (no RUN) then drop "
            "into the interactive terminal. Without PROGRAM.bas, just start the "
            "terminal as normal."
        ),
    )
    return parser


def _filter_file_run_output(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    run_index = None
    for index, line in enumerate(lines):
        if line.strip() == "RUN":
            run_index = index
    if run_index is None:
        return text

    output_lines = lines[run_index + 1 :]
    output_lines = [line.rstrip() for line in output_lines if line.strip()]
    if output_lines and output_lines[-1].strip() == "OK":
        output_lines.pop()
    if not output_lines:
        return ""
    return "\n".join(output_lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.program is not None and args.exec_text is not None:
        parser.error("PROGRAM.bas cannot be combined with --exec")

    # -i/--interactive forces terminal mode (stdin must be a TTY)
    if args.interactive and not sys.stdin.isatty():
        print("error: --interactive requires a TTY (stdin is not a terminal)", file=sys.stderr)
        return 2

    use_terminal = args.interactive

    stdin_bytes = b""
    terminal: RawTerminal | None = None
    terminal_manager: RawTerminal | None = None
    batch_output: StreamingFileRunOutput | None = None

    if use_terminal:
        terminal_manager = RawTerminal()
        terminal = terminal_manager.__enter__()
    elif not sys.stdin.isatty():
        stdin_bytes = sys.stdin.buffer.read()

    if args.program is not None and not args.interactive and args.exec_text is None:
        batch_output = StreamingFileRunOutput(sys.stdout)

    try:
        console = SessionConsole(
            staged_input=build_staged_input(
                program_path=args.program,
                exec_text=args.exec_text,
                stdin_bytes=stdin_bytes,
                memory_size=args.memory_size,
                terminal_width=args.terminal_width,
                run=not args.interactive,
            ),
            terminal=terminal,
            output_buffer=batch_output,
        )
        result = OsiBasicMachine(args.rom).run(console=console, max_steps=args.max_steps)
    except BrokenPipeError:
        return _broken_pipe_exit_code()
    except KeyboardInterrupt:
        if isinstance(batch_output, StreamingFileRunOutput):
            batch_output.finish()
        return 130
    except (FileNotFoundError, UnsupportedMonitorCall, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if terminal_manager is not None:
            terminal_manager.__exit__(None, None, None)

    if result.reason == "step_limit":
        print(
            f"\nerror: OSI BASIC reached the instruction limit at 0x{result.pc:04X}",
            file=sys.stderr,
        )
        return 2

    if isinstance(batch_output, StreamingFileRunOutput):
        try:
            batch_output.finish()
        except BrokenPipeError:
            return _broken_pipe_exit_code()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
