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
        compiled = _compile_n80_basic_program(_read_basic_source_text(path))
        if not compiled:
            return
        self._install_host_basic_placeholders(compiled)

    def _install_host_basic_placeholders(self, compiled: list[tuple[int, bytes]]) -> None:
        compiled = sorted(compiled, key=lambda item: item[0])
        for line_number, body in compiled:
            padding = max(16, len(body) + 8)
            placeholder = f"{line_number} REM " + ("X" * padding)
            self.inject_keys([ord(char) for char in placeholder] + [0x0D])
        addr = 0x8021
        for line_number, body in compiled:
            next_addr = addr + 4 + len(body) + 1
            self.memory.write_word(addr, next_addr)
            self.memory.write_word(addr + 2, line_number)
            base = addr + 4
            for offset, value in enumerate(body):
                self.memory.write_byte(base + offset, value)
            self.memory.write_byte(base + len(body), 0x00)
            addr = next_addr
        self.memory.write_word(addr, 0x0000)

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


_LINE_NUMBER_RE = re.compile(r"^\s*(\d+)\s*(.*)$")
_DIRECT_IF_GOTO_RE = re.compile(r"^\s*IF\s+((?:(?!\bTHEN\b).)+?)\s+GOTO\s+(.+?)\s*$", re.IGNORECASE)
_IF_THEN_LINENO_RE = re.compile(r"^\s*IF\s+(.+?)\s+THEN\s+(\d+)\s*$", re.IGNORECASE)
_IF_THEN_STMT_RE = re.compile(r"^\s*IF\s+((?:(?!\bTHEN\b).)+?)\s+THEN\s+(.*)\s*$", re.IGNORECASE)
_NUMERIC_HASH_RE = re.compile(r"(?<=\d)#")
_NEGATIVE_STEP_FOR_RE = re.compile(
    r"^\s*FOR\s+([A-Z][A-Z0-9$#]?)\s*=\s*(.+?)\s+TO\s+(.+?)\s+STEP\s+-1\s*$",
    re.IGNORECASE,
)
# Forward loop (STEP +1 or omitted) for untyped variable names.  When DEFDBL A-Z
# is active, these variables are double-precision, which the ROM cannot use as FOR
# loop control variables in tokenised mode.  The loop is rewritten to an explicit
# counter variable assignment + IF-GOTO guard, just like negative-step loops.
_FORWARD_FOR_RE = re.compile(
    r"^\s*FOR\s+([A-Z][A-Z0-9]*)\s*=\s*(.+?)\s+TO\s+((?:(?!\bSTEP\b).)+?)\s*$",
    re.IGNORECASE,
)
_DEFDBL_ALL_RE = re.compile(r"^\s*DEFDBL\s+A\s*-\s*Z\s*$", re.IGNORECASE)
_NEXT_RE = re.compile(r"^\s*NEXT\s+([A-Z][A-Z0-9$#]?)\s*$", re.IGNORECASE)
_KEYWORD_TOKENS: tuple[tuple[str, bytes], ...] = (
    ("DEFDBL", bytes([0xAE])),
    ("RETURN", bytes([0x8E])),
    ("GOSUB", bytes([0x8D])),
    ("VARPTR", bytes([0xE5])),
    ("PRINT", bytes([0x91])),
    ("INPUT", bytes([0x85])),
    ("HEX$", bytes([0xFF, 0x9A])),
    ("PEEK", bytes([0xFF, 0x97])),
    ("SQR", bytes([0xFF, 0x87])),
    ("ABS", bytes([0xFF, 0x86])),
    ("ATN", bytes([0xFF, 0x8E])),
    ("POKE", bytes([0x98])),
    ("NEXT", bytes([0x83])),
    ("INT", bytes([0xFF, 0x85])),
    ("END", bytes([0x81])),
    ("STEP", bytes([0xDA])),
    ("THEN", bytes([0xD8])),
    ("GOTO", bytes([0x89])),
    ("FOR", bytes([0x82])),
    ("MOD", bytes([0xFD])),
    ("REM", bytes([0x8F])),
    ("IF", bytes([0x8B])),
    ("TO", bytes([0xD7])),
)
_KEYWORD_PREFIX_TOKENS = {0x82, 0x8B, 0x89, 0x8D, 0x91, 0xAE, 0xD7, 0xD8, 0xDA, 0xFD, 0xE5}
_BINARY_OPERATOR_TOKENS = {
    "=": 0xF1,
    ">": 0xF0,
    "<": 0xF2,
    "+": 0xF3,
    "-": 0xF4,
    "*": 0xF5,
    "/": 0xF6,
    "^": 0xF7,
}


def _preprocess_n80_basic_source(source: str) -> str:
    """Apply source-level rewrites to N-BASIC text (normalize + rewrite FOR loops).

    Returns modified BASIC source text suitable for interactive ROM line entry.
    Non-BASIC lines (e.g. "RUN") are passed through unchanged.
    """
    parsed: list[tuple[int, str]] = []
    non_basic_suffix: list[str] = []
    defdbl_az = False
    for raw_line in source.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        match = _LINE_NUMBER_RE.match(line)
        if match is None:
            non_basic_suffix.append(line)
            continue
        body = _normalize_n80_basic_body(match.group(2))
        parsed.append((int(match.group(1)), body))
        if _DEFDBL_ALL_RE.match(body):
            defdbl_az = True
    rewritten = _rewrite_for_loops(parsed, defdbl_az=defdbl_az)
    lines = [f"{ln} {body}" for ln, body in rewritten]
    lines.extend(non_basic_suffix)
    return "\n".join(lines)


def _compile_n80_basic_program(source: str) -> list[tuple[int, bytes]]:
    parsed: list[tuple[int, str]] = []
    defdbl_az = False
    for raw_line in source.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            continue
        match = _LINE_NUMBER_RE.match(line)
        if match is None:
            raise ValueError(f"missing BASIC line number: {line!r}")
        body = _normalize_n80_basic_body(match.group(2))
        parsed.append((int(match.group(1)), body))
        if _DEFDBL_ALL_RE.match(body):
            defdbl_az = True
    return [(line_number, _tokenize_n80_basic_body(body)) for line_number, body in _rewrite_for_loops(parsed, defdbl_az=defdbl_az)]


def _normalize_n80_basic_body(body: str) -> str:
    match = _DIRECT_IF_GOTO_RE.match(body)
    if match is not None:
        cond = _NUMERIC_HASH_RE.sub("", match.group(1))
        return f"IF {cond} THEN GOTO {match.group(2)}"
    # N-BASIC batch mode requires an explicit GOTO after THEN when jumping to a
    # line number. The ROM's own tokenizer normalises "THEN 250" → "THEN GOTO 250"
    # but direct byte injection must replicate this.
    match = _IF_THEN_LINENO_RE.match(body)
    if match is not None:
        cond = _NUMERIC_HASH_RE.sub("", match.group(1))
        return f"IF {cond} THEN GOTO {match.group(2)}"
    # The N80 ROM's IF-condition expression evaluator does not handle '#' as a
    # double-precision type suffix on numeric literals; the ROM gives "Syntax
    # error" when it encounters one.  Strip '#' from the condition only.
    # Assignments and other statements are left unchanged so that precision
    # suffixes such as '1/5#' continue to work outside IF conditions.
    match = _IF_THEN_STMT_RE.match(body)
    if match is not None:
        cond = _NUMERIC_HASH_RE.sub("", match.group(1))
        return f"IF {cond} THEN {match.group(2)}"
    return body


def _rewrite_for_loops(lines: list[tuple[int, str]], *, defdbl_az: bool) -> list[tuple[int, str]]:
    rewritten = list(lines)
    loop_stack: list[dict[str, int | str | bool]] = []
    occupied = {line_number for line_number, _ in rewritten}
    insertions: list[tuple[int, int, str]] = []

    for index, (line_number, body) in enumerate(rewritten):
        neg_match = _NEGATIVE_STEP_FOR_RE.match(body)
        fwd_match = _FORWARD_FOR_RE.match(body) if (defdbl_az and neg_match is None) else None

        if neg_match is not None or fwd_match is not None:
            match = neg_match if neg_match is not None else fwd_match
            is_forward = fwd_match is not None
            next_line_number = rewritten[index + 1][0] if index + 1 < len(rewritten) else line_number + 10
            guard_line_number = _allocate_line_number(line_number, next_line_number, occupied)
            var_name = match.group(1).upper()
            start_expr = match.group(2).strip()
            end_expr = match.group(3).strip()
            rewritten[index] = (line_number, f"{var_name}={start_expr}")
            if is_forward:
                # Forward loop: exit when var > end_expr (checked before body)
                guard_body = f"IF {var_name}>{end_expr} THEN GOTO __LOOP_EXIT_{line_number}__"
            else:
                # Negative step: exit when var < end_expr (checked before body)
                guard_body = f"IF {var_name}<{end_expr} THEN GOTO __LOOP_EXIT_{line_number}__"
            insertions.append((index + 1, guard_line_number, guard_body))
            loop_stack.append(
                {
                    "line_number": line_number,
                    "var_name": var_name,
                    "guard_line_number": guard_line_number,
                    "is_forward": is_forward,
                }
            )
            continue

        next_match = _NEXT_RE.match(body)
        if next_match is None:
            continue
        if not loop_stack:
            continue
        loop = loop_stack[-1]
        if next_match.group(1).upper() != loop["var_name"]:
            continue
        loop_stack.pop()
        exit_line_number = rewritten[index + 1][0] if index + 1 < len(rewritten) else line_number + 1
        if loop["is_forward"]:
            step_body = f"{loop['var_name']}={loop['var_name']}+1:GOTO {loop['guard_line_number']}"
        else:
            step_body = f"{loop['var_name']}={loop['var_name']}-1:GOTO {loop['guard_line_number']}"
        rewritten[index] = (line_number, step_body)
        placeholder = f"__LOOP_EXIT_{loop['line_number']}__"
        for insertion_index, (target_index, inserted_line_number, inserted_body) in enumerate(insertions):
            if placeholder not in inserted_body:
                continue
            insertions[insertion_index] = (
                target_index,
                inserted_line_number,
                inserted_body.replace(placeholder, str(exit_line_number)),
            )
            break

    for target_index, line_number, body in sorted(insertions, key=lambda item: (item[0], item[1]), reverse=True):
        rewritten.insert(target_index, (line_number, body))
    return rewritten


def _allocate_line_number(start: int, end: int, occupied: set[int]) -> int:
    for candidate in range(start + 1, end):
        if candidate in occupied:
            continue
        occupied.add(candidate)
        return candidate
    raise ValueError(f"no free line number between {start} and {end} for ROM batch rewrite")


def _tokenize_n80_basic_body(body: str) -> bytes:
    text = body.upper()
    out = bytearray()
    index = 0
    while index < len(text):
        matched = False
        if _is_keyword_start(text, index):
            for keyword, token in _KEYWORD_TOKENS:
                if not text.startswith(keyword, index):
                    continue
                end = index + len(keyword)
                if not _is_keyword_end(text, end):
                    continue
                out.extend(token)
                index = end
                matched = True
                if keyword == "REM":
                    out.extend(ord(ch) for ch in text[index:])
                    return bytes(out)
                break
        if matched:
            continue

        char = text[index]
        if char in _BINARY_OPERATOR_TOKENS:
            if char == "-" and _is_unary_minus(text, index, out):
                next_ch = text[index + 1] if index + 1 < len(text) else ""
                if next_ch.isdigit() or next_ch == ".":
                    # The N80 ROM cannot parse a unary minus directly before a
                    # numeric literal in tokenised form. Rewrite "-<number>" as
                    # "0-<number>" (binary subtract from zero), which the ROM
                    # handles correctly and is semantically equivalent.
                    out.append(0x30)  # '0'
                    out.append(_BINARY_OPERATOR_TOKENS["-"])  # binary minus
                else:
                    out.append(ord(char))  # unary '-' before variable/function
            else:
                out.append(_BINARY_OPERATOR_TOKENS[char])
            index += 1
            continue
        # Numeric literals starting with "." (e.g. ".2#") are valid BASIC but
        # the N80 ROM expression evaluator in tokenised form expects numbers to
        # start with a digit. Insert a leading "0" so ".2#" becomes "0.2#" in
        # the tokenised stream, matching what the ROM's own tokeniser produces.
        if char == "." and index + 1 < len(text) and text[index + 1].isdigit():
            prev = _last_significant_byte(out)
            if prev is None or not (0x30 <= prev <= 0x39 or prev == 0x2E):
                out.append(0x30)  # insert leading '0'
        out.append(ord(char))
        index += 1
    return bytes(out)


def _is_keyword_start(text: str, index: int) -> bool:
    if index <= 0:
        return True
    return not (text[index - 1].isalnum() or text[index - 1] in "$#")


def _is_keyword_end(text: str, index: int) -> bool:
    if index >= len(text):
        return True
    return not (text[index].isalnum() or text[index] in "$#")


def _is_unary_minus(text: str, index: int, out: bytearray) -> bool:
    if index + 1 >= len(text) or text[index + 1] not in "0123456789.":
        return False
    prev = _last_significant_byte(out)
    if prev is None:
        return True
    return prev in b"(,:;" or prev in _KEYWORD_PREFIX_TOKENS or prev in _BINARY_OPERATOR_TOKENS.values()


def _last_significant_byte(out: bytearray) -> int | None:
    for value in reversed(out):
        if value != 0x20:
            return value
    return None
