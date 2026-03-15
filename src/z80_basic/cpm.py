from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .disk import DiskImage
from .memory import Memory

CPM_TPA_BASE = 0x0100
CPM_COMMAND_TAIL = 0x0080
CPM_DEFAULT_DMA = 0x0080
CPM_FCB1 = 0x005C
CPM_FCB2 = 0x006C
CPM_WBOOT_VECTOR = 0x0000
CPM_CURRENT_DRIVE = 0x0004
CPM_BDOS_VECTOR = 0x0005
CPM_IOBYTE = 0x0003
CPM_WBOOT_ENTRY = 0xEA00
CPM_BDOS_ENTRY = 0xF200


@dataclass(slots=True)
class CPMEnvironment:
    memory: Memory
    disk_image: DiskImage
    program_name: str
    program_size: int
    program_entry: int = CPM_TPA_BASE
    command_tail_address: int = CPM_COMMAND_TAIL
    dma_address: int = CPM_DEFAULT_DMA
    wboot_entry: int = CPM_WBOOT_ENTRY
    bdos_entry: int = CPM_BDOS_ENTRY

    def describe(self) -> str:
        top_of_tpa = self.program_entry + self.program_size
        return "\n".join(
            [
                "Prepared CP/M memory image.",
                f"Disk image: {self.disk_image.describe()}",
                (
                    f"Program: {self.program_name} "
                    f"({self.program_size} bytes) at 0x{self.program_entry:04X}"
                ),
                f"TPA used: 0x{self.program_entry:04X}-0x{top_of_tpa - 1:04X}",
                f"WBOOT vector: JP 0x{self.wboot_entry:04X}",
                f"BDOS vector:  JP 0x{self.bdos_entry:04X}",
                f"DMA address:  0x{self.dma_address:04X}",
                "Minimal CPU execution and BDOS console traps are available.",
            ]
        )


def build_cpm_environment(
    cpm_image_path: Path,
    program_path: Path,
    memory_size: int,
) -> CPMEnvironment:
    memory = Memory(memory_size)
    disk_image = DiskImage.from_path(cpm_image_path)
    program_bytes = program_path.read_bytes()

    if not program_bytes:
        raise ValueError(f"program is empty: {program_path}")

    available = memory_size - CPM_TPA_BASE
    if len(program_bytes) > available:
        raise ValueError(
            f"program is too large for the TPA: {len(program_bytes)} > {available}"
        )

    _initialize_page_zero(memory)
    _initialize_command_tail(memory)
    memory.load(CPM_TPA_BASE, program_bytes)

    return CPMEnvironment(
        memory=memory,
        disk_image=disk_image,
        program_name=program_path.name,
        program_size=len(program_bytes),
    )


def _initialize_page_zero(memory: Memory) -> None:
    # CP/M places jump vectors at 0000h and 0005h.
    _write_jump(memory, CPM_WBOOT_VECTOR, CPM_WBOOT_ENTRY)
    _write_jump(memory, CPM_BDOS_VECTOR, CPM_BDOS_ENTRY)
    memory.write_byte(CPM_IOBYTE, 0x00)
    memory.write_byte(CPM_CURRENT_DRIVE, 0x00)


def _initialize_command_tail(memory: Memory) -> None:
    memory.write_byte(CPM_COMMAND_TAIL, 0x00)
    memory.load(CPM_COMMAND_TAIL + 1, b"\r")
    memory.load(CPM_FCB1, bytes(16))
    memory.load(CPM_FCB2, bytes(16))


def _write_jump(memory: Memory, address: int, destination: int) -> None:
    memory.write_byte(address, 0xC3)
    memory.write_word(address + 1, destination)
