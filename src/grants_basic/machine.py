from __future__ import annotations

import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .acia import Acia6850
from .memory import Memory
from .terminal import RawTerminal
from .z80 import ExecutionResult, PortDevice, UnsupportedInstruction, Z80CPU


ACIA_CONTROL_PORT = 0x80
ACIA_DATA_PORT = 0x81
DEFAULT_ROM_PATH = Path(__file__).resolve().parent.parent.parent / "downloads/grants-basic/rom.bin"
_DATACLASS_SLOTS: dict[str, bool] = {"slots": True} if sys.version_info >= (3, 10) else {}


class InputRequestError(RuntimeError):
    """Raised when batch execution reaches a runtime input request."""


class GrantPorts(PortDevice):
    def __init__(self, acia: Acia6850) -> None:
        self.acia = acia

    def read_port(self, port: int) -> int:
        port &= 0xFF
        if port == ACIA_CONTROL_PORT:
            return self.acia.read_status()
        if port == ACIA_DATA_PORT:
            return self.acia.read_data()
        return 0xFF

    def write_port(self, port: int, value: int) -> None:
        port &= 0xFF
        value &= 0xFF
        if port == ACIA_CONTROL_PORT:
            self.acia.write_control(value)
            return
        if port == ACIA_DATA_PORT:
            self.acia.write_data(value)


@dataclass(frozen=True, **_DATACLASS_SLOTS)
class GrantSearleConfig:
    rom_path: Path
    entry_point: int = 0x0000
    max_steps: int = 50_000
    boot_step_budget: int = 10_000_000
    prompt_step_budget: int = 10_000_000
    interactive_max_steps: int = 2_000


class GrantSearleMachine:
    def __init__(self, config: GrantSearleConfig) -> None:
        self.config = config
        self.memory = Memory()
        self.acia = Acia6850()
        self.ports = GrantPorts(self.acia)
        self.cpu = Z80CPU(self.memory, entry_point=config.entry_point, ports=self.ports)
        self._console: list[str] = []
        self._console_offset = 0
        self.last_error: str | None = None
        self.last_result: ExecutionResult | None = None
        self._booted = False

    def load_rom(self) -> None:
        path = self.config.rom_path.expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"ROM file not found: {path}")
        self.memory.clear_ram(0x00)
        self.memory.add_rom(0x0000, path.read_bytes())

    def boot(self) -> None:
        self.load_rom()
        self.cpu.reset(entry_point=self.config.entry_point)
        self.acia.reset()
        self._console.clear()
        self._console_offset = 0
        self._run_until_output_contains("Memory top?", step_budget=self.config.boot_step_budget)
        self.send_text("\r")
        self._run_until_prompt(step_budget=self.config.prompt_step_budget)
        self._booted = True

    def send_text(self, text: str) -> None:
        self.acia.receive_text(text.replace("\n", "\r"))

    def send_file(self, path: Path) -> None:
        source_path = path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"BASIC source file not found: {source_path}")
        lines = source_path.read_text(encoding="ascii").splitlines()
        for line in lines:
            if not line.strip():
                continue
            self.send_text(f"{line.rstrip(chr(13))}\r")
            if self._booted:
                self._run_until_input_idle(step_budget=self.config.prompt_step_budget, require_activity=True)

    def run_program_file(
        self,
        path: Path,
        *,
        emit_output: Callable[[str], None] | None = None,
    ) -> str:
        self.require_boot()
        self.send_file(path)
        self._console_offset = len(self._console)
        self.send_text("RUN\r")
        streamer = _GrantBatchRunStreamer()
        while True:
            result = self.run_slice(self.config.max_steps)
            chunk = self.consume_console_text()
            if chunk:
                filtered = streamer.feed(chunk)
                if filtered and emit_output is not None:
                    emit_output(filtered)
            if result.reason == "unsupported":
                raise RuntimeError(self.last_error or "unsupported instruction")
            if self._waiting_for_serial_input() and not self._at_prompt():
                raise InputRequestError("program requested interactive input, which is not supported in --run mode")
            if self._at_prompt():
                break
        filtered = streamer.finish()
        if filtered and emit_output is not None:
            emit_output(filtered)
        return streamer.output_text

    def load_program_file(self, path: Path) -> str:
        self.require_boot()
        self.send_file(path)
        return self.console_text()

    def show_program_listing(self) -> None:
        self.require_boot()
        self.send_text("LIST\r")
        self._run_until_input_idle(step_budget=self.config.prompt_step_budget, require_activity=True)

    def console_text(self) -> str:
        return "".join(self._console)

    def consume_console_text(self) -> str:
        if self._console_offset >= len(self._console):
            return ""
        text = "".join(self._console[self._console_offset :])
        self._console_offset = len(self._console)
        return text

    def run_terminal(self) -> int:
        self.require_boot()
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            sys.stdout.write(self.consume_console_text())
            sys.stdout.flush()
            return 0
        with RawTerminal() as terminal:
            terminal.write(self._format_terminal_output(self.consume_console_text()))
            while True:
                if terminal.input_ready():
                    value = terminal.read_byte()
                    if value == 0x04:
                        terminal.write("\r\n")
                        return 0
                    if value == 0x0A:
                        normalized = 0x0D
                    elif value in (0x08, 0x7F):
                        normalized = 0x08
                    else:
                        normalized = value
                    self.acia.receive_byte(normalized)
                self.run_slice(self.config.interactive_max_steps)
                out = self.consume_console_text()
                if out:
                    terminal.write(self._format_terminal_output(out))
                if not terminal.input_ready():
                    time.sleep(0.001)

    def run_slice(self, max_steps: int) -> ExecutionResult:
        steps = 0
        self.last_error = None
        acia = self.acia
        cpu = self.cpu
        try:
            while steps < max_steps:
                if cpu.iff1 and ((acia.receive_irq_enabled and acia.rx_queue) or acia.transmit_irq_enabled):
                    cpu.service_interrupt()
                    steps += 1
                    continue
                if cpu.halted:
                    self.last_result = ExecutionResult(reason="halted", steps=steps, pc=cpu.pc)
                    self._drain_output()
                    return self.last_result
                cpu.step()
                steps += 1
        except UnsupportedInstruction as exc:
            self.last_error = str(exc)
            self.last_result = ExecutionResult(reason="unsupported", steps=steps, pc=cpu.pc)
            self._drain_output()
            return self.last_result
        self.last_result = ExecutionResult(reason="step_limit", steps=steps, pc=cpu.pc)
        self._drain_output()
        return self.last_result

    def _drain_output(self) -> None:
        chunk = self.acia.consume_tx_text()
        if not chunk:
            return
        for char in chunk:
            if char == "\r":
                continue
            self._console.append(char)

    def _format_terminal_output(self, text: str) -> str:
        formatted: list[str] = []
        for char in text:
            if char == "\n":
                formatted.append("\r\n")
            elif char == "\x08":
                formatted.append("\x08 \x08")
            else:
                formatted.append(char)
        return "".join(formatted)

    def _run_until_output_contains(self, needle: str, *, step_budget: int) -> None:
        total = 0
        while needle not in self.console_text():
            result = self.run_slice(self.config.max_steps)
            total += result.steps
            if total >= step_budget:
                raise TimeoutError(f"timed out waiting for {needle!r}")
            if result.reason == "unsupported":
                raise RuntimeError(self.last_error or "unsupported instruction")

    def _run_until_prompt(self, *, step_budget: int, require_activity: bool = False) -> None:
        total = 0
        first_pass = True
        while True:
            if not (require_activity and first_pass) and self._at_prompt():
                return
            result = self.run_slice(self.config.max_steps)
            total += result.steps
            first_pass = False
            if total >= step_budget:
                raise TimeoutError("timed out waiting for BASIC prompt")
            if result.reason == "unsupported":
                raise RuntimeError(self.last_error or "unsupported instruction")

    def _run_until_input_idle(self, *, step_budget: int, require_activity: bool = False) -> None:
        total = 0
        first_pass = True
        while True:
            if not (require_activity and first_pass):
                if not self.acia.rx_queue and (self._at_prompt() or self._waiting_for_serial_input()):
                    return
            result = self.run_slice(self.config.max_steps)
            total += result.steps
            first_pass = False
            if total >= step_budget:
                raise TimeoutError("timed out waiting for BASIC input to settle")
            if result.reason == "unsupported":
                raise RuntimeError(self.last_error or "unsupported instruction")

    def _at_prompt(self) -> bool:
        text = self.console_text().replace("\r", "")
        return text.endswith("Ok\n") or text.endswith("Ok")

    def _waiting_for_serial_input(self) -> bool:
        return self.cpu.pc in (0x0074, 0x0077, 0x0079)

    def require_boot(self) -> None:
        if not self._booted:
            raise RuntimeError("machine not booted")


def _filter_batch_run_output(text: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and lines[0].strip().upper() == "RUN":
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[-1].strip() == "Ok":
        lines.pop()
    if not lines:
        return ""
    return "\n".join(line.rstrip() for line in lines) + "\n"


class _GrantBatchRunStreamer:
    def __init__(self) -> None:
        self._buffer = ""
        self._saw_run = False
        self._pending_line: str | None = None
        self._emitted: list[str] = []

    @property
    def output_text(self) -> str:
        if not self._emitted:
            return ""
        return "".join(f"{line}\n" for line in self._emitted)

    def feed(self, text: str) -> str:
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")
        emitted: list[str] = []
        while "\n" in self._buffer:
            raw_line, self._buffer = self._buffer.split("\n", 1)
            line = raw_line.rstrip()
            if not self._saw_run:
                if line.strip().upper() == "RUN":
                    self._saw_run = True
                continue
            if not line.strip():
                continue
            flushed = self._flush_pending()
            if flushed is not None:
                emitted.append(flushed)
            self._pending_line = line
        return "".join(f"{line}\n" for line in emitted)

    def finish(self) -> str:
        if self._buffer:
            line = self._buffer.rstrip()
            self._buffer = ""
            if self._saw_run and line.strip():
                flushed = self._flush_pending()
                emitted = [flushed] if flushed is not None else []
                self._pending_line = line
                tail = self._flush_pending(final=True)
                if tail is not None:
                    emitted.append(tail)
                return "".join(f"{item}\n" for item in emitted)
            if not self._saw_run and line.strip().upper() == "RUN":
                self._saw_run = True
        flushed = self._flush_pending(final=True)
        if flushed is None:
            return ""
        return f"{flushed}\n"

    def _flush_pending(self, *, final: bool = False) -> str | None:
        if self._pending_line is None:
            return None
        line = self._pending_line
        self._pending_line = None
        if final and line.strip() == "Ok":
            return None
        self._emitted.append(line)
        return line
