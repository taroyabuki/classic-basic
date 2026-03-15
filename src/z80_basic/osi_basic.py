from __future__ import annotations

import argparse
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


class UnsupportedMonitorCall(RuntimeError):
    """Raised when the emulated monitor call is not implemented."""


@dataclass(frozen=True)
class OsiBasicResult:
    reason: str
    steps: int
    pc: int


class SessionConsole:
    def __init__(self, staged_input: bytes = b"", terminal: RawTerminal | None = None) -> None:
        self._buffer = deque(staged_input)
        self._terminal = terminal

    def read_byte(self) -> int:
        if self._buffer:
            return self._normalize_input(self._buffer.popleft())

        if self._terminal is None:
            return 0x04

        return self._normalize_input(self._terminal.read_byte())

    def write_byte(self, value: int) -> None:
        byte_value = value & 0x7F

        if self._terminal is not None:
            self._terminal.write_byte(byte_value)
            return

        os.write(sys.stdout.fileno(), bytes([byte_value]))

    @staticmethod
    def _normalize_input(value: int) -> int:
        value &= 0x7F
        if value == 0x0A:
            return 0x0D
        if value in (0x08, 0x7F):
            return 0x5F
        return value


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.program is not None and args.exec_text is not None:
        parser.error("PROGRAM.bas cannot be combined with --exec")

    # -i/--interactive forces terminal mode (stdin must be a TTY)
    if args.interactive and not sys.stdin.isatty():
        print("error: --interactive requires a TTY (stdin is not a terminal)", file=sys.stderr)
        return 2

    stdin_bytes = b""
    terminal: RawTerminal | None = None
    terminal_manager: RawTerminal | None = None

    if args.interactive or sys.stdin.isatty():
        terminal_manager = RawTerminal()
        terminal = terminal_manager.__enter__()
    else:
        stdin_bytes = sys.stdin.buffer.read()

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
        )
        result = OsiBasicMachine(args.rom).run(console=console, max_steps=args.max_steps)
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
