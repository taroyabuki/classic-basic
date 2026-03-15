from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .bdos import CPMBdos, UnsupportedBdosCall
from .cpm import CPMEnvironment, build_cpm_environment
from .cpu import ExecutionResult, Z80CPU, UnsupportedInstruction


@dataclass(slots=True)
class EmulatorConfig:
    cpm_image: Path
    mbasic_path: Path
    memory_size: int = 0x10000


class Z80BasicMachine:
    def __init__(self, config: EmulatorConfig) -> None:
        self.config = config
        self.environment: CPMEnvironment | None = None

    def validate_inputs(self) -> None:
        if not self.config.cpm_image.is_file():
            raise FileNotFoundError(f"CP/M image not found: {self.config.cpm_image}")
        if not self.config.mbasic_path.is_file():
            raise FileNotFoundError(f"MBASIC binary not found: {self.config.mbasic_path}")
        if self.config.memory_size != 0x10000:
            raise ValueError("Current skeleton assumes a 64KB address space")

    def prepare(self) -> CPMEnvironment:
        self.validate_inputs()
        self.environment = build_cpm_environment(
            cpm_image_path=self.config.cpm_image,
            program_path=self.config.mbasic_path,
            memory_size=self.config.memory_size,
        )
        return self.environment

    def describe_environment(self) -> str:
        environment = self.environment or self.prepare()
        return environment.describe()

    def run(self, console: object, max_steps: int = 100000) -> ExecutionResult:
        environment = self.environment or self.prepare()
        cpu = Z80CPU(environment.memory, environment.program_entry)
        bdos = CPMBdos(environment=environment, console=console)

        steps = 0
        while steps < max_steps:
            if cpu.pc == environment.wboot_entry:
                return ExecutionResult(reason="warm_boot", steps=steps, pc=cpu.pc)
            if cpu.pc == environment.bdos_entry:
                reason = bdos.handle_call(cpu)
                steps += 1
                if reason is not None:
                    return ExecutionResult(reason=reason, steps=steps, pc=cpu.pc)
                continue
            if cpu.halted:
                return ExecutionResult(reason="halt", steps=steps, pc=cpu.pc)

            cpu.step()
            steps += 1

        return ExecutionResult(reason="step_limit", steps=steps, pc=cpu.pc)


__all__ = [
    "EmulatorConfig",
    "ExecutionResult",
    "UnsupportedBdosCall",
    "UnsupportedInstruction",
    "Z80BasicMachine",
]
