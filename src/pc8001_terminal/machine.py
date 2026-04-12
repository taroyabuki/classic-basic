from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
import sys

from z80_basic.cpu import ExecutionResult, UnsupportedInstruction, Z80CPU
from z80_basic.terminal import RawTerminal

from .display import TextGeometry
from .memory import PC8001Memory
from .ports import DISPLAY_DATA_PORT, KEYBOARD_DATA_PORT, KEYBOARD_STATUS_PORT, PC8001Ports


class InputRequestError(RuntimeError):
    """Raised when batch execution reaches interactive input."""


@dataclass(frozen=True, slots=True)
class RomSpec:
    path: Path
    start: int
    name: str


@dataclass(frozen=True, slots=True)
class PC8001Config:
    roms: tuple[RomSpec, ...] = ()
    entry_point: int | None = None
    startup_program: Path | None = None
    run_startup: bool = True  # If False, load startup_program but do not RUN it
    max_steps: int = 20000
    batch_rounds: int = 32
    vram_start: int = 0xF300
    vram_stride: int | None = 0x78
    vram_cell_width: int = 2
    rows: int = 20
    cols: int = 40
    port_log_limit: int = 0
    vram_log_limit: int = 0
    summary_limit: int = 32
    breakpoints: tuple[int, ...] = ()
    system_input_value: int = 0x06


class PC8001Machine:
    def __init__(self, config: PC8001Config) -> None:
        geometry = TextGeometry(rows=config.rows, cols=config.cols)
        self.config = config
        self.memory = PC8001Memory(
            vram_start=config.vram_start,
            vram_stride=config.vram_stride,
            vram_cell_width=config.vram_cell_width,
            geometry=geometry,
        )
        self.memory.set_vram_log_limit(config.vram_log_limit)
        self.ports = PC8001Ports(
            memory=self.memory,
            cursor_row=3,
            cursor_col=0,
            system_input_value=config.system_input_value,
            port_log_limit=config.port_log_limit,
        )
        self.cpu = Z80CPU(
            self.memory,
            entry_point=config.entry_point or 0x0000,
            ports=self.ports,
        )
        self.last_error: str | None = None
        self.last_result: ExecutionResult | None = None
        self.console_output: list[str] = []
        self.console_output_offset = 0

    def load_roms(self) -> None:
        for rom in self.config.roms:
            path = rom.path.expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"ROM file not found: {path}")
            self.memory.add_rom(name=rom.name, start=rom.start, data=path.read_bytes())
        if not self.config.roms:
            self.memory.load(0x0000, DEMO_PROGRAM)

    def boot_demo(self) -> None:
        self.memory.screen.clear()
        self.ports.cursor_row = 0
        self.ports.cursor_col = 0
        if not self.config.roms:
            self.run_firmware(max_steps=self.config.max_steps)
            return
        if self.config.entry_point is None:
            self.memory.screen.write_text(0, 0, f"Loaded ROM regions: {len(self.config.roms)}")
            self.memory.screen.write_text(1, 0, "Pass --entry-point to execute ROM code")
            self.memory.screen.write_text(2, 0, "Ctrl-D to exit")
            self.ports.cursor_row = 4
            return
        self.run_firmware(max_steps=self.config.max_steps)
        if self._use_host_terminal():
            self._bootstrap_host_basic()
        if self.last_error is not None:
            self.memory.screen.write_text(0, 0, self.last_error[: self.memory.geometry.cols])
        if self.config.startup_program is not None and self._use_host_terminal():
            self.load_basic_source(self.config.startup_program)
            # Let the ROM return to the prompt after line entry before auto-RUN.
            self._run_host_basic_until_settled()
            if self.config.run_startup:
                self.inject_keys([ord(char) for char in "RUN"] + [0x0D])
                # Let the ROM consume the queued RUN command before returning to the caller.
                self._run_host_basic_until_settled()
            else:
                self.inject_keys([ord(char) for char in "LIST"] + [0x0D])
                self._run_host_basic_until_settled()

    def handle_key(self, value: int) -> bool:
        value &= 0xFF
        if value == 0x04:
            return False
        self.ports.queue_key(value)
        if not self._use_host_terminal():
            if value in (0x0A, 0x0D):
                self.ports.pulse_system_input(0x00, repeat=8, fallback=0x06)
            else:
                self.ports.pulse_system_input(0x04, repeat=4, fallback=0x06)
        self.run_firmware(max_steps=self.config.max_steps)
        return True

    def inject_keys(self, values: list[int]) -> bool:
        for value in values:
            if not self.handle_key(value):
                return False
        return True

    def load_basic_source(self, path: Path) -> None:
        source_path = path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"BASIC source file not found: {source_path}")
        if self._use_host_terminal():
            self._load_host_basic_program(source_path)
            return
        for line in _read_basic_source_lines(source_path):
            if not line:
                continue
            self.inject_keys([ord(char) for char in line] + [0x0D])

    def run_terminal(self) -> int:
        self.load_roms()
        self.boot_demo()

        # Batch mode: when a startup program was loaded *and* run, print
        # accumulated output and exit without entering the interactive loop.
        if self.config.startup_program is not None and self.config.run_startup and self._use_host_terminal():
            self._raise_if_batch_requested_input()
            self._print_non_tty_output()
            return 0

        if self._use_host_terminal() and sys.stdin.isatty() and sys.stdout.isatty():
            return self._run_host_terminal()

        if not sys.stdin.isatty() or not sys.stdout.isatty():
            self._print_non_tty_output()
            return 0

        with RawTerminal() as terminal:
            self._redraw(terminal, force=True)
            while True:
                if terminal.input_ready():
                    if not self.handle_key(terminal.read_byte()):
                        terminal.write("\r\n")
                        return 0
                    self._redraw(terminal)
                else:
                    time.sleep(0.01)

    def run_firmware(self, *, max_steps: int) -> ExecutionResult:
        steps = 0
        self.last_error = None
        try:
            while steps < max_steps and not self.cpu.halted:
                hook_status = self._handle_rom_terminal_hook(steps)
                if hook_status == "stop":
                    return self.last_result
                if hook_status == "handled":
                    steps += 1
                    continue
                if self.cpu.pc in self.config.breakpoints:
                    self.last_result = ExecutionResult(
                        reason="breakpoint",
                        steps=steps,
                        pc=self.cpu.pc,
                    )
                    return self.last_result
                self.cpu.step()
                steps += 1
        except UnsupportedInstruction as exc:
            self.last_error = str(exc)
            self.last_result = ExecutionResult(
                reason="unsupported",
                steps=steps,
                pc=self.cpu.pc,
            )
            return self.last_result
        if self.cpu.halted:
            self.last_result = ExecutionResult(reason="halted", steps=steps, pc=self.cpu.pc)
            return self.last_result
        self.last_result = ExecutionResult(reason="step_limit", steps=steps, pc=self.cpu.pc)
        return self.last_result

    def consume_console_output(self) -> str:
        if self.console_output_offset >= len(self.console_output):
            return ""
        text = "".join(self.console_output[self.console_output_offset :])
        self.console_output_offset = len(self.console_output)
        return text

    def format_state_summary(self) -> str:
        if self.last_result is None:
            return "No firmware executed"
        preview_pc = self.last_result.pc
        if self.last_result.reason == "unsupported":
            preview_pc = (preview_pc - 1) & 0xFFFF
            if self.last_error is not None and "unsupported ED opcode" in self.last_error:
                preview_pc = (preview_pc - 1) & 0xFFFF
        preview = " ".join(f"{value:02X}" for value in self.memory.slice(preview_pc, 8))
        decoded = Z80CPU.decode_instruction(self.memory, preview_pc)
        summary = (
            f"reason={self.last_result.reason} steps={self.last_result.steps} "
            f"pc=0x{self.last_result.pc:04X} "
            f"a=0x{self.cpu.a:02X} bc=0x{self.cpu.bc:04X} "
            f"de=0x{self.cpu.de:04X} hl=0x{self.cpu.hl:04X} sp=0x{self.cpu.sp:04X} "
            f"alt_a=0x{self.cpu.alt_a:02X} alt_bc=0x{(self.cpu.alt_b << 8) | self.cpu.alt_c:04X} "
            f"alt_de=0x{(self.cpu.alt_d << 8) | self.cpu.alt_e:04X} "
            f"alt_hl=0x{(self.cpu.alt_h << 8) | self.cpu.alt_l:04X} "
            f"z={int(self.cpu.zero)} c={int(self.cpu.carry)} p={int(self.cpu.parity_even)} "
            f"alt_z={int(self.cpu.alt_zero)} alt_c={int(self.cpu.alt_carry)} alt_p={int(self.cpu.alt_parity_even)} "
            f"next@0x{preview_pc:04X}=[{preview}] "
            f"decoded=\"{decoded}\""
        )
        if self.last_error is not None:
            return f"{summary} error={self.last_error}"
        return summary

    def format_port_log(self) -> str:
        return self.ports.format_port_log()

    def format_port_summary(self) -> str:
        return self.ports.format_port_summary(limit=self.config.summary_limit)

    def format_vram_log(self) -> str:
        return self.memory.format_vram_log()

    def format_vram_summary(self) -> str:
        return self.memory.format_vram_summary(limit=self.config.summary_limit)

    def _redraw(self, terminal: RawTerminal, *, force: bool = False) -> None:
        if not force and not self.memory.screen.consume_dirty():
            return
        if force:
            self.memory.screen.consume_dirty()
        lines = self.memory.screen.render_lines()
        terminal.write("\x1b[2J\x1b[H")
        for index, line in enumerate(lines):
            suffix = "\r\n" if index + 1 < len(lines) else ""
            terminal.write(line + suffix)
        terminal.write(f"\x1b[{self.ports.cursor_row + 1};{self.ports.cursor_col + 1}H")

    def _use_host_terminal(self) -> bool:
        return bool(self.config.roms) and self.config.entry_point == 0x0000

    def _handle_rom_terminal_hook(self, steps: int) -> str | None:
        if not self._use_host_terminal():
            return None
        if self.cpu.pc == 0x1B7E:
            line = self.ports.consume_line()
            if line is None:
                self.last_result = ExecutionResult(reason="input_wait", steps=steps, pc=self.cpu.pc)
                return "stop"
            self._load_basic_line_buffer(line)
            self.cpu.hl = 0xEC95
            self.cpu.a = 0x00
            self.cpu.carry = False
            self.cpu.zero = len(line) == 0
            self.cpu.parity_even = True
            self.cpu.sign = False
            self.cpu.pc = self.cpu.pop_word()
            return "handled"
        if self.cpu.pc == 0x0F75:
            if not self.ports.has_queued_key():
                self.last_result = ExecutionResult(reason="input_wait", steps=steps, pc=self.cpu.pc)
                return "stop"
            value = self.ports.pop_queued_key()
            self.cpu.a = value
            self.cpu.zero = value == 0
            self.cpu.carry = False
            self.cpu.parity_even = (value.bit_count() % 2) == 0
            self.cpu.pc = self.cpu.pop_word()
            return "handled"
        if self.cpu.pc == 0x40A6:
            self._emit_console_char(self.cpu.a)
            self.cpu.pc = self.cpu.pop_word()
            return "handled"
        return None

    def _emit_console_char(self, value: int) -> None:
        value &= 0xFF
        if value in (0x0A, 0x0D):
            if not self.console_output or self.console_output[-1] != "\n":
                self.console_output.append("\n")
            return
        if value == 0x08:
            if self.console_output and self.console_output[-1] != "\n":
                self.console_output.pop()
            return
        if value == 0x09:
            self.console_output.append("\t")
            return
        if 0x20 <= value <= 0x7E:
            self.console_output.append(chr(value))

    def _load_basic_line_buffer(self, line: list[int]) -> None:
        base = 0xEC96
        normalized = [value - 0x20 if 0x61 <= value <= 0x7A else value for value in line]
        for offset, value in enumerate(normalized[:255]):
            self.memory.write_byte(base + offset, value)
        self.memory.write_byte(base + min(len(normalized), 255), 0x00)

    def _load_host_basic_program(self, path: Path) -> None:
        for line in _read_basic_source_lines(path):
            if not line:
                continue
            self.inject_keys([ord(char) for char in line] + [0x0D])
            self._run_host_basic_until_settled()

    def _bootstrap_host_basic(self) -> None:
        self.cpu.pc = 0x17AF
        self.console_output.clear()
        self.console_output_offset = 0
        self.last_result = self.run_firmware(max_steps=self.config.max_steps)

    def _run_host_basic_until_settled(self, *, max_rounds: int | None = None) -> None:
        rounds = max_rounds if max_rounds is not None else self.config.batch_rounds
        for _ in range(rounds):
            result = self.run_firmware(max_steps=self.config.max_steps)
            self._stream_batch_console_output()
            if result.reason != "step_limit":
                return

    def _raise_if_batch_requested_input(self) -> None:
        if (
            self.config.startup_program is None
            or not self.config.run_startup
            or not self._use_host_terminal()
            or self.last_result is None
            or self.last_result.reason != "input_wait"
        ):
            return
        text = "".join(self.console_output)
        if text.rstrip().endswith("Ok"):
            return
        raise InputRequestError("interactive program input is not supported in --run --file mode")

    def _stream_batch_console_output(self) -> None:
        if not self._should_stream_batch_console_output():
            return
        text = self.consume_console_output()
        if not text:
            return
        text = _filter_nbasic_batch_output(text)
        if not text:
            return
        sys.stdout.write(text)
        sys.stdout.flush()

    def _should_stream_batch_console_output(self) -> bool:
        return (
            self._use_host_terminal()
            and self.config.startup_program is not None
            and not sys.stdout.isatty()
        )

    def _print_non_tty_output(self) -> None:
        text = self.consume_console_output()
        if text:
            if self.config.startup_program is not None:
                text = _filter_nbasic_batch_output(text)
            print(text, end="")
            return
        if self._use_host_terminal():
            return
        for line in self.memory.screen.render_lines():
            print(line)

    def _run_host_terminal(self) -> int:
        text = self.consume_console_output()
        if text:
            sys.stdout.write(text)
            sys.stdout.flush()
        while True:
            if self.last_result is not None and self.last_result.reason not in {"input_wait", "step_limit"}:
                return 0
            try:
                line = input()
            except (EOFError, KeyboardInterrupt):
                return 0
            self.inject_keys([ord(char) for char in line] + [0x0D])
            text = self.consume_console_output()
            if text:
                sys.stdout.write(text)
                if not text.endswith("\n"):
                    sys.stdout.write("\n")
                sys.stdout.flush()


_NBASIC_NOISE_RE = re.compile(
    r"^(?:NEC PC-8001 BASIC|Copyright 1979.*Microsoft|Ok$)"
)


def _filter_nbasic_batch_output(text: str) -> str:
    """Remove ROM banner and bare 'Ok' prompts from N-BASIC batch output."""
    lines = text.splitlines(keepends=True)
    return "".join(line for line in lines if not _NBASIC_NOISE_RE.match(line))


def _read_basic_source_text(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii")
    except UnicodeDecodeError as exc:
        raise ValueError(f"BASIC source must be ASCII only: {path}") from exc


def _read_basic_source_lines(path: Path) -> list[str]:
    return [line.rstrip("\r") for line in _read_basic_source_text(path).splitlines()]


DEMO_PROGRAM = bytes(
    [
        0x21, 0x21, 0x00,        # 0000: LD HL,$0021
        0x7E,                    # 0003: LD A,(HL)
        0xFE, 0x00,              # 0004: CP $00
        0x20, 0x03,              # 0006: JR NZ,$000B
        0xC3, 0x11, 0x00,        # 0008: JP $0011
        0xD3, DISPLAY_DATA_PORT, # 000B: OUT ($10),A
        0x23,                    # 000D: INC HL
        0xC3, 0x03, 0x00,        # 000E: JP $0003
        0xDB, KEYBOARD_STATUS_PORT,  # 0011: IN A,($20)
        0xFE, 0x00,                  # 0013: CP $00
        0x20, 0x03,                  # 0015: JR NZ,$001A
        0xC3, 0x11, 0x00,            # 0017: JP $0011
        0xDB, KEYBOARD_DATA_PORT,    # 001A: IN A,($21)
        0xD3, DISPLAY_DATA_PORT,     # 001C: OUT ($10),A
        0xC3, 0x11, 0x00,            # 001E: JP $0011
        # 0021: message
        0x50, 0x43, 0x2D, 0x38, 0x30, 0x30, 0x31, 0x20,
        0x73, 0x63, 0x61, 0x66, 0x66, 0x6F, 0x6C, 0x64,
        0x0D, 0x0A,
        0x43, 0x74, 0x72, 0x6C, 0x2D, 0x44, 0x20, 0x74,
        0x6F, 0x20, 0x65, 0x78, 0x69, 0x74, 0x0D, 0x0A,
        0x00,
    ]
)
