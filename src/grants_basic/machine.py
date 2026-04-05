from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .acia import Acia6850
from .memory import Memory
from .terminal import RawTerminal
from .z80 import ExecutionResult, PortDevice, UnsupportedInstruction, Z80CPU


ACIA_CONTROL_PORT = 0x80
ACIA_DATA_PORT = 0x81
DEFAULT_ROM_PATH = Path(__file__).resolve().parent.parent.parent / "downloads/grants-basic/rom.bin"


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


@dataclass(frozen=True, slots=True)
class GrantSearleConfig:
    rom_path: Path
    entry_point: int = 0x0000
    max_steps: int = 200_000
    boot_step_budget: int = 10_000_000
    prompt_step_budget: int = 10_000_000


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

    def run_program_file(self, path: Path) -> str:
        self.require_boot()
        self.send_file(path)
        self.send_text("RUN\r")
        self._run_until_input_idle(step_budget=self.config.prompt_step_budget, require_activity=True)
        if self._waiting_for_serial_input() and not self._at_prompt():
            raise InputRequestError("program requested interactive input, which is not supported in --run mode")
        return self.console_text()

    def load_program_file(self, path: Path) -> str:
        self.require_boot()
        self.send_file(path)
        return self.console_text()

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
                self.run_slice(self.config.max_steps)
                out = self.consume_console_text()
                if out:
                    terminal.write(self._format_terminal_output(out))
                if terminal.input_ready():
                    value = terminal.read_byte()
                    if value == 0x04:
                        terminal.write("\r\n")
                        return 0
                    self.acia.receive_byte(0x0D if value == 0x0A else value)
                else:
                    time.sleep(0.005)

    def run_slice(self, max_steps: int) -> ExecutionResult:
        steps = 0
        self.last_error = None
        try:
            while steps < max_steps:
                if self.acia.irq_asserted and self.cpu.iff1:
                    self.cpu.service_interrupt()
                    steps += 1
                    continue
                if self.cpu.halted:
                    self.last_result = ExecutionResult(reason="halted", steps=steps, pc=self.cpu.pc)
                    self._drain_output()
                    return self.last_result
                self.cpu.step()
                steps += 1
                self._drain_output()
        except UnsupportedInstruction as exc:
            self.last_error = str(exc)
            self.last_result = ExecutionResult(reason="unsupported", steps=steps, pc=self.cpu.pc)
            self._drain_output()
            return self.last_result
        self.last_result = ExecutionResult(reason="step_limit", steps=steps, pc=self.cpu.pc)
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
        return text.replace("\n", "\r\n")

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
