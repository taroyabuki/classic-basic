from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

from z80_basic.cpu import PortDevice, Z80CPU
from z80_basic.memory import Memory

from pc8001_terminal.display import TextGeometry, TextScreen
from pc8001_terminal.cli import DEFAULT_ROM_PATH, _decode_key_sequence
from pc8001_terminal.machine import PC8001Config, PC8001Machine, RomSpec, _compile_n80_basic_program
from pc8001_terminal.memory import PC8001Memory
from pc8001_terminal.ports import (
    ALT_SYSTEM_COMMAND_PORT,
    ALT_SYSTEM_CONTROL_PORT,
    ALT_SYSTEM_DATA_PORT,
    ALT_SYSTEM_INPUT_PORT,
    DISPLAY_DATA_PORT,
    KEYBOARD_DATA_PORT,
    KEYBOARD_STATUS_PORT,
    PC8001Ports,
    SYSTEM_CONTROL_PORT,
    SYSTEM_STATUS_PORT,
)


class TextScreenTests(unittest.TestCase):
    def test_write_text_renders_ascii(self) -> None:
        screen = TextScreen(TextGeometry(rows=2, cols=8))
        screen.write_text(0, 0, "HELLO")
        self.assertEqual(screen.render_lines()[0], "HELLO")


class PC8001MemoryTests(unittest.TestCase):
    def test_vram_write_updates_screen(self) -> None:
        memory = PC8001Memory(vram_start=0x1000, geometry=TextGeometry(rows=2, cols=4))
        memory.write_byte(0x1000, ord("A"))
        self.assertEqual(memory.screen.render_lines()[0], "A")

    def test_vram_stride_can_be_wider_than_visible_columns(self) -> None:
        memory = PC8001Memory(
            vram_start=0x1000,
            vram_stride=6,
            geometry=TextGeometry(rows=2, cols=4),
        )
        memory.write_byte(0x1000, ord("A"))
        memory.write_byte(0x1004, ord("X"))
        memory.write_byte(0x1006, ord("B"))
        lines = memory.screen.render_lines()
        self.assertEqual(lines[0], "A")
        self.assertEqual(lines[1], "B")

    def test_vram_cell_width_maps_every_second_byte_to_a_visible_cell(self) -> None:
        memory = PC8001Memory(
            vram_start=0x1000,
            vram_stride=8,
            vram_cell_width=2,
            geometry=TextGeometry(rows=1, cols=4),
        )
        memory.write_byte(0x1000, ord("A"))
        memory.write_byte(0x1001, ord("x"))
        memory.write_byte(0x1002, ord("B"))
        self.assertEqual(memory.screen.render_lines()[0], "AB")

    def test_rom_region_is_read_only(self) -> None:
        memory = PC8001Memory(vram_start=0x1000, geometry=TextGeometry(rows=2, cols=4))
        memory.add_rom(name="TEST", start=0x0000, data=b"\x12\x34")
        memory.write_byte(0x0000, 0xFF)
        self.assertEqual(memory.read_byte(0x0000), 0x12)

    def test_read_write_word_works_with_ram_and_vram(self) -> None:
        memory = PC8001Memory(vram_start=0x1000, geometry=TextGeometry(rows=2, cols=4))
        memory.write_word(0x0100, 0x1234)
        memory.write_word(0x1000, 0x4241)
        self.assertEqual(memory.read_word(0x0100), 0x1234)
        self.assertEqual(memory.read_word(0x1000), 0x4241)
        self.assertEqual(memory.screen.render_lines()[0][:2], "AB")

    def test_vram_write_logging_records_pc_and_address(self) -> None:
        memory = PC8001Memory(vram_start=0x1000, geometry=TextGeometry(rows=2, cols=4))
        memory.set_vram_log_limit(4)
        memory.set_current_pc(0x1234)
        memory.write_byte(0x1001, 0x41)
        self.assertIn("PC=0x1234 VRAM 0x1001 0x41", memory.format_vram_log())
        self.assertIn("VRAM 0x1001 from 0x1234", memory.format_vram_summary())
        self.assertEqual(memory.format_vram_summary(limit=0), "")


class PC8001MachineTests(unittest.TestCase):
    def test_demo_boot_writes_banner(self) -> None:
        machine = PC8001Machine(PC8001Config(rows=5, cols=40))
        machine.load_roms()
        machine.boot_demo()
        lines = machine.memory.screen.render_lines()
        self.assertIn("PC-8001 scaffold", lines[0])

    def test_rom_loader_reads_file(self) -> None:
        path = Path("/tmp/pc8001-rom.bin")
        path.write_bytes(b"\xAA\x55")
        machine = PC8001Machine(
            PC8001Config(roms=(RomSpec(path=path, start=0x2000, name="rom.bin"),))
        )
        machine.load_roms()
        self.assertEqual(machine.memory.read_byte(0x2000), 0xAA)

    def test_default_rom_path_matches_expected_location(self) -> None:
        self.assertTrue(DEFAULT_ROM_PATH.is_absolute())
        self.assertEqual(DEFAULT_ROM_PATH.name, "N80_11.rom")
        self.assertIn("downloads/pc8001", str(DEFAULT_ROM_PATH))

    def test_default_config_uses_pc8001_vram_stride(self) -> None:
        self.assertEqual(PC8001Config().vram_stride, 0x78)
        self.assertEqual(PC8001Config().vram_cell_width, 2)
        self.assertEqual(PC8001Config().cols, 40)
        self.assertEqual(PC8001Config().rows, 20)

    def test_decode_key_sequence_supports_escape_sequences(self) -> None:
        self.assertEqual(_decode_key_sequence("A\\r\\x31"), [0x41, 0x0D, 0x31])

    def test_load_basic_source_injects_non_empty_lines(self) -> None:
        path = Path("/tmp/pc8001-source.bas")
        path.write_text("10 PRINT 1\n\n20 END\r\n", encoding="utf-8")
        machine = PC8001Machine(PC8001Config())
        captured: list[list[int]] = []

        def capture(values: list[int]) -> bool:
            captured.append(values)
            return True

        machine.inject_keys = capture  # type: ignore[method-assign]
        machine.load_basic_source(path)

        self.assertEqual(
            captured,
            [
                [ord(char) for char in "10 PRINT 1"] + [0x0D],
                [ord(char) for char in "20 END"] + [0x0D],
            ],
        )

    def test_compile_n80_basic_program_tokenizes_rom_batch_subset(self) -> None:
        compiled = _compile_n80_basic_program(
            "10 DEFDBL A-Z\n20 FOR I=2 TO 1 STEP -1\n30 IF 5 MOD 2=1 THEN PRINT HEX$(PEEK(VARPTR(A)))\n40 NEXT I\n"
        )
        self.assertEqual(compiled[0], (10, bytes([0xAE, 0x20, 0x41, 0xF4, 0x5A])))
        self.assertEqual(compiled[1], (20, bytes([0x49, 0xF1, 0x32])))
        self.assertEqual(compiled[2][0], 21)
        self.assertTrue(compiled[2][1].startswith(bytes([0x8B, 0x20, 0x49, 0xF2, 0x31, 0x20, 0xD8, 0x20, 0x89, 0x20])))
        self.assertEqual(
            compiled[3],
            (
                30,
                bytes(
                    [
                        0x8B,
                        0x20,
                        0x35,
                        0x20,
                        0xFD,
                        0x20,
                        0x32,
                        0xF1,
                        0x31,
                        0x20,
                        0xD8,
                        0x20,
                        0x91,
                        0x20,
                        0xFF,
                        0x9A,
                        0x28,
                        0xFF,
                        0x97,
                        0x28,
                        0xE5,
                        0x28,
                        0x41,
                        0x29,
                        0x29,
                        0x29,
                    ]
                ),
            ),
        )
        self.assertEqual(compiled[4], (40, bytes([0x49, 0xF1, 0x49, 0xF4, 0x31, 0x3A, 0x89, 0x20, 0x32, 0x31])))

    def test_default_rom_startup_program_runs_tokenized_arithmetic(self) -> None:
        if not DEFAULT_ROM_PATH.is_file():
            self.skipTest(f"ROM not available: {DEFAULT_ROM_PATH}")

        path = Path("/tmp/pc8001-rom-batch.bas")
        path.write_text("10 K=12\n20 PRINT 2+2\n30 FOR I=1 TO 2:PRINT I:NEXT I\n", encoding="utf-8")
        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=DEFAULT_ROM_PATH, start=0x0000, name=DEFAULT_ROM_PATH.name),),
                entry_point=0x0000,
                startup_program=path,
                max_steps=300000,
            )
        )
        machine.load_roms()
        machine.boot_demo()
        output = machine.consume_console_output()

        self.assertIn("NEC PC-8001 BASIC Ver 1.1", output)
        self.assertIn(" 4 ", output)
        self.assertIn(" 1 ", output)
        self.assertIn(" 2 ", output)
        self.assertNotIn("Syntax error", output)

    def test_default_rom_startup_program_runs_frac_probe(self) -> None:
        if not DEFAULT_ROM_PATH.is_file():
            self.skipTest(f"ROM not available: {DEFAULT_ROM_PATH}")

        path = Path(__file__).resolve().parents[1] / "tests" / "data" / "nbasic_frac2_varptr_probe.bas"
        if not path.is_file():
            self.skipTest(f"probe not available: {path}")

        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=DEFAULT_ROM_PATH, start=0x0000, name=DEFAULT_ROM_PATH.name),),
                entry_point=0x0000,
                startup_program=path,
                max_steps=800000,
            )
        )
        machine.load_roms()
        machine.boot_demo()
        output = machine.consume_console_output()

        self.assertIn("3.141592653589793", output)
        self.assertIn("C6", output)

    def test_rom_entry_point_executes_program(self) -> None:
        path = Path("/tmp/pc8001-prog.bin")
        path.write_bytes(bytes([0x3E, ord("X"), 0xD3, DISPLAY_DATA_PORT, 0x76]))
        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=path, start=0x2000, name="prog.bin"),),
                entry_point=0x2000,
            )
        )
        machine.load_roms()
        machine.boot_demo()
        self.assertEqual(machine.memory.screen.render_lines()[0], "X")

    def test_handle_key_pulses_system_input(self) -> None:
        machine = PC8001Machine(PC8001Config())
        machine.load_roms()
        machine.handle_key(ord("A"))
        self.assertEqual(machine.ports.read_port(0xFE), 0x04)

    def test_run_firmware_records_stop_reason(self) -> None:
        path = Path("/tmp/pc8001-unsupported.bin")
        path.write_bytes(bytes([0xED]))
        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=path, start=0x2000, name="unsupported.bin"),),
                entry_point=0x2000,
            )
        )
        machine.load_roms()
        result = machine.run_firmware(max_steps=4)
        self.assertEqual(result.reason, "unsupported")
        self.assertIn("unsupported ED opcode", machine.format_state_summary())
        self.assertIn("next@0x2000=[ED", machine.format_state_summary())
        self.assertIn('decoded="DB 0xED,0x00"', machine.format_state_summary())
        self.assertEqual(machine.format_port_log(), "No port activity recorded")
        self.assertEqual(machine.format_port_summary(), "No port activity recorded")
        self.assertEqual(machine.format_vram_log(), "No VRAM activity recorded")
        self.assertEqual(machine.format_vram_summary(), "No VRAM activity recorded")

    def test_run_firmware_stops_at_breakpoint(self) -> None:
        path = Path("/tmp/pc8001-break.bin")
        path.write_bytes(bytes([0x00, 0x76]))
        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=path, start=0x2000, name="break.bin"),),
                entry_point=0x2000,
                breakpoints=(0x2001,),
            )
        )
        machine.load_roms()
        result = machine.run_firmware(max_steps=4)
        self.assertEqual(result.reason, "breakpoint")
        self.assertEqual(result.steps, 1)
        self.assertEqual(result.pc, 0x2001)

    def test_host_terminal_input_hook_returns_queued_key(self) -> None:
        path = Path("/tmp/pc8001-host-input.bin")
        path.write_bytes(bytes([0xCD, 0x75, 0x0F, 0x76]))
        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=path, start=0x0000, name="host-input.bin"),),
                entry_point=0x0000,
            )
        )
        machine.load_roms()
        machine.ports.queue_key(ord("A"))
        result = machine.run_firmware(max_steps=8)
        self.assertEqual(result.reason, "halted")
        self.assertEqual(machine.cpu.a, ord("A"))

    def test_host_terminal_input_hook_waits_without_key(self) -> None:
        path = Path("/tmp/pc8001-host-wait.bin")
        path.write_bytes(bytes([0xCD, 0x75, 0x0F, 0x76]))
        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=path, start=0x0000, name="host-wait.bin"),),
                entry_point=0x0000,
            )
        )
        machine.load_roms()
        result = machine.run_firmware(max_steps=8)
        self.assertEqual(result.reason, "input_wait")
        self.assertEqual(result.pc, 0x0F75)

    def test_host_terminal_output_hook_captures_console_text(self) -> None:
        path = Path("/tmp/pc8001-host-output.bin")
        path.write_bytes(bytes([0x3E, ord("X"), 0xCD, 0xA6, 0x40, 0x76]))
        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=path, start=0x0000, name="host-output.bin"),),
                entry_point=0x0000,
            )
        )
        machine.load_roms()
        result = machine.run_firmware(max_steps=8)
        self.assertEqual(result.reason, "halted")
        self.assertEqual(machine.consume_console_output(), "X")

    def test_host_terminal_line_hook_loads_basic_buffer(self) -> None:
        path = Path("/tmp/pc8001-host-line.bin")
        path.write_bytes(bytes([0xCD, 0x7E, 0x1B, 0x76]))
        machine = PC8001Machine(
            PC8001Config(
                roms=(RomSpec(path=path, start=0x0000, name="host-line.bin"),),
                entry_point=0x0000,
            )
        )
        machine.load_roms()
        for value in b"PRINT 1\r":
            machine.ports.queue_key(value)
        result = machine.run_firmware(max_steps=16)
        self.assertEqual(result.reason, "halted")
        self.assertEqual(machine.cpu.hl, 0xEC95)
        self.assertEqual(machine.memory.read_byte(0xEC96), ord("P"))
        self.assertEqual(machine.memory.read_byte(0xEC97), ord("R"))
        self.assertEqual(machine.memory.read_byte(0xEC9D), 0x00)

    def test_defdbl_and_hash_suffix_accepted(self) -> None:
        """N-BASIC: DEFDBL and # type suffix are silently accepted (real hardware behaviour)."""
        if not DEFAULT_ROM_PATH.is_file():
            self.skipTest(f"ROM not available: {DEFAULT_ROM_PATH}")

        env = os.environ.copy()
        pythonpath = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = pythonpath if "PYTHONPATH" not in env else f"{pythonpath}:{env['PYTHONPATH']}"
        result = subprocess.run(
            [
                "python3",
                "-m",
                "pc8001_terminal",
                "--keys",
                r"PRINT 12\rDEFDBL A-Z\rA#=1\r",
            ],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("NEC PC-8001 BASIC Ver 1.1", result.stdout)
        self.assertIn(" 12 ", result.stdout)
        self.assertEqual(result.stdout.count("Syntax error"), 0)


class Z80CpuIoTests(unittest.TestCase):
    def test_decode_instruction_formats_known_and_unknown(self) -> None:
        memory = Memory(0x100)
        memory.load(
            0x0000,
            bytes(
                [
                    0x21, 0x34, 0x12,
                    0x20, 0xFE,
                    0xED, 0x44,
                    0xED, 0x4B, 0x78, 0x56,
                    0xED, 0xA1,
                    0xED, 0xB0,
                    0xCB, 0x7E,
                    0xED, 0xB8,
                ]
            ),
        )
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0000), "LD HL,0x1234")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0003), "JR NZ,0x0003")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0005), "NEG")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0007), "LD BC,(0x5678)")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x000B), "CPI")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x000D), "LDIR")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x000F), "BIT 7,(HL)")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0011), "LDDR")

    def test_decode_instruction_formats_djnz_and_alu_groups(self) -> None:
        memory = Memory(0x100)
        memory.load(0x0000, bytes([0x07, 0x0F, 0x17, 0x1F, 0x2F, 0x37, 0x3F, 0x08, 0x10, 0xFE, 0x30, 0xFE, 0x38, 0xFE, 0x80, 0x89, 0xA1, 0xA8, 0xB7, 0xBE, 0xC8, 0xC4, 0x34, 0x12, 0xCE, 0x01, 0xDE, 0x02, 0xD2, 0x78, 0x56, 0xDA, 0x9A, 0xBC, 0xE2, 0x11, 0x22, 0xEA, 0x33, 0x44, 0xD9, 0xCF, 0xDF, 0xEF, 0xFF]))
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0000), "RLCA")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0001), "RRCA")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0002), "RLA")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0003), "RRA")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0004), "CPL")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0005), "SCF")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0006), "CCF")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0007), "EX AF,AF'")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0008), "DJNZ 0x0008")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x000A), "JR NC,0x000A")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x000C), "JR C,0x000C")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x000E), "ADD A,B")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x000F), "ADC A,C")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0010), "AND C")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0011), "XOR B")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0012), "OR A")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0013), "CP (HL)")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0014), "RET Z")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0015), "CALL NZ,0x1234")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0018), "ADC A,0x01")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x001A), "SBC A,0x02")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x001C), "JP NC,0x5678")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x001F), "JP C,0xBC9A")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0022), "JP PO,0x2211")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0025), "JP PE,0x4433")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0028), "EXX")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x0029), "RST 08H")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x002A), "RST 18H")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x002B), "RST 28H")
        self.assertEqual(Z80CPU.decode_instruction(memory, 0x002C), "RST 38H")

    def test_in_out_and_branching(self) -> None:
        memory = Memory(0x100)
        ports = _DummyPorts()
        memory.load(
            0x0000,
            bytes(
                [
                    0xDB, 0x20,      # IN A,(20h)
                    0xFE, 0x00,      # CP 0
                    0x20, 0x01,      # JR NZ,+1
                    0x76,            # HALT
                    0xD3, 0x10,      # OUT (10h),A
                    0x76,            # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000, ports=ports)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(ports.writes, [(0x10, 0x41)])

    def test_ld_matrix_and_inc_dec(self) -> None:
        memory = Memory(0x100)
        memory.load(
            0x0000,
            bytes(
                [
                    0x06, 0x10,        # LD B,10h
                    0x04,              # INC B
                    0x0D,              # DEC C
                    0x21, 0x40, 0x00,  # LD HL,0040h
                    0x70,              # LD (HL),B
                    0x7E,              # LD A,(HL)
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.b, 0x11)
        self.assertEqual(cpu.c, 0xFF)
        self.assertEqual(cpu.a, 0x11)
        self.assertEqual(memory.read_byte(0x0040), 0x11)

    def test_push_pop_and_indirect_hl(self) -> None:
        memory = Memory(0x10000)
        memory.load(
            0x0000,
            bytes(
                [
                    0x01, 0x34, 0x12,  # LD BC,1234h
                    0xC5,              # PUSH BC
                    0xE1,              # POP HL
                    0x22, 0x80, 0x00,  # LD (0080h),HL
                    0x21, 0x00, 0x00,  # LD HL,0000h
                    0x2A, 0x80, 0x00,  # pseudo-not-yet, replaced below
                ]
            ),
        )
        memory.load(
            0x000C,
            bytes(
                [
                    0xE9,              # JP (HL)
                    0x76,              # HALT at 1234h
                ]
            ),
        )
        memory.write_byte(0x1234, 0x76)
        cpu = Z80CPU(memory, entry_point=0x0000)
        # Patch in HL from memory using direct words until opcode 0x2A exists.
        while cpu.pc != 0x000C and not cpu.halted:
            cpu.step()
        self.assertEqual(memory.read_word(0x0080), 0x1234)
        cpu.hl = memory.read_word(0x0080)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.pc, 0x1235)

    def test_16bit_memory_indirection_and_add(self) -> None:
        memory = Memory(0x10000)
        memory.write_word(0x0080, 0x1234)
        memory.load(
            0x0000,
            bytes(
                [
                    0x01, 0x34, 0x12,  # LD BC,1234h
                    0x3E, 0x56,        # LD A,56h
                    0x02,              # LD (BC),A
                    0x0A,              # LD A,(BC)
                    0x11, 0x02, 0x00,  # LD DE,0002h
                    0x21, 0x00, 0x10,  # LD HL,1000h
                    0x19,              # ADD HL,DE
                    0x22, 0x90, 0x00,  # LD (0090h),HL
                    0x2A, 0x80, 0x00,  # LD HL,(0080h)
                    0x29,              # ADD HL,HL
                    0x31, 0x01, 0x00,  # LD SP,0001h
                    0x39,              # ADD HL,SP
                    0x21, 0x40, 0x00,  # LD HL,0040h
                    0x36, 0x00,        # LD (HL),00h
                    0x34,              # INC (HL)
                    0x35,              # DEC (HL)
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(memory.read_byte(0x1234), 0x56)
        self.assertEqual(memory.read_word(0x0090), 0x1002)
        self.assertEqual(cpu.hl, 0x0040)
        self.assertEqual(memory.read_byte(0x0040), 0x00)

    def test_ld_a_indirect_bc_preserves_flags(self) -> None:
        memory = Memory(0x100)
        memory.write_byte(0x0042, 0x7F)
        memory.load(0x0000, bytes([0x0A, 0x76]))  # LD A,(BC); HALT
        cpu = Z80CPU(memory, entry_point=0x0000)
        cpu.bc = 0x0042
        cpu.zero = True
        cpu.carry = False

        cpu.step()

        self.assertEqual(cpu.a, 0x7F)
        self.assertTrue(cpu.zero)
        self.assertFalse(cpu.carry)

    def test_ld_a_indirect_de_preserves_flags(self) -> None:
        memory = Memory(0x100)
        memory.write_byte(0x0042, 0x00)
        memory.load(0x0000, bytes([0x1A, 0x76]))  # LD A,(DE); HALT
        cpu = Z80CPU(memory, entry_point=0x0000)
        cpu.de = 0x0042
        cpu.zero = False
        cpu.carry = True

        cpu.step()

        self.assertEqual(cpu.a, 0x00)
        self.assertFalse(cpu.zero)
        self.assertTrue(cpu.carry)

    def test_add_hl_sets_carry_on_16bit_overflow(self) -> None:
        memory = Memory(0x100)
        memory.load(
            0x0000,
            bytes(
                [
                    0x21, 0xFF, 0xFF,  # LD HL,FFFFh
                    0x11, 0x01, 0x00,  # LD DE,0001h
                    0x19,              # ADD HL,DE
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.hl, 0x0000)
        self.assertTrue(cpu.carry)

    def test_16bit_decrement_and_immediate_alu(self) -> None:
        memory = Memory(0x10000)
        memory.load(
            0x0000,
            bytes(
                [
                    0x01, 0x00, 0x10,  # LD BC,1000h
                    0x0B,              # DEC BC
                    0x11, 0x00, 0x20,  # LD DE,2000h
                    0x1B,              # DEC DE
                    0x21, 0x00, 0x30,  # LD HL,3000h
                    0x2B,              # DEC HL
                    0x31, 0x00, 0x40,  # LD SP,4000h
                    0x3B,              # DEC SP
                    0x3E, 0x55,        # LD A,55h
                    0xE6, 0x0F,        # AND 0Fh
                    0xF6, 0x80,        # OR 80h
                    0xEE, 0x8A,        # XOR 8Ah
                    0xD6, 0x07,        # SUB 07h
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.bc, 0x0FFF)
        self.assertEqual(cpu.de, 0x1FFF)
        self.assertEqual(cpu.hl, 0x2FFF)
        self.assertEqual(cpu.sp, 0x3FFF)
        self.assertEqual(cpu.a, 0x08)
        self.assertFalse(cpu.zero)

    def test_ed_neg_and_16bit_loads(self) -> None:
        memory = Memory(0x10000)
        memory.write_word(0x4000, 0x2222)
        memory.write_word(0x5000, 0x3333)
        memory.write_word(0x6000, 0x4444)
        memory.load(
            0x0000,
            bytes(
                [
                    0x01, 0x34, 0x12,        # LD BC,1234h
                    0xED, 0x43, 0x00, 0x20,  # LD (2000h),BC
                    0x11, 0x78, 0x56,        # LD DE,5678h
                    0xED, 0x53, 0x02, 0x20,  # LD (2002h),DE
                    0x31, 0x9A, 0xBC,        # LD SP,BC9Ah
                    0xED, 0x73, 0x04, 0x20,  # LD (2004h),SP
                    0xED, 0x4B, 0x00, 0x40,  # LD BC,(4000h)
                    0xED, 0x5B, 0x00, 0x50,  # LD DE,(5000h)
                    0xED, 0x7B, 0x00, 0x60,  # LD SP,(6000h)
                    0x3E, 0x05,              # LD A,05h
                    0xED, 0x44,              # NEG
                    0x76,                    # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(memory.read_word(0x2000), 0x1234)
        self.assertEqual(memory.read_word(0x2002), 0x5678)
        self.assertEqual(memory.read_word(0x2004), 0xBC9A)
        self.assertEqual(cpu.bc, 0x2222)
        self.assertEqual(cpu.de, 0x3333)
        self.assertEqual(cpu.sp, 0x4444)
        self.assertEqual(cpu.a, 0xFB)
        self.assertFalse(cpu.zero)

    def test_ed_im_and_i_r_transfers_are_supported(self) -> None:
        memory = Memory(0x100)
        memory.load(
            0x0000,
            bytes(
                [
                    0x3E, 0x80,  # LD A,80h
                    0xED, 0x47,  # LD I,A
                    0xED, 0x4F,  # LD R,A
                    0x3E, 0x00,  # LD A,00h
                    0xED, 0x57,  # LD A,I
                    0xED, 0x5F,  # LD A,R
                    0xED, 0x5E,  # IM 2
                    0x76,        # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.i, 0x80)
        self.assertEqual(cpu.r, 0x80)
        self.assertEqual(cpu.a, 0x80)
        self.assertEqual(cpu.interrupt_mode, 2)

    def test_ed_port_io_and_block_copy(self) -> None:
        memory = Memory(0x10000)
        ports = _DummyPorts()
        memory.load(0x3000, b"ABC")
        memory.load(
            0x0000,
            bytes(
                [
                    0x0E, 0x20,              # LD C,20h
                    0xED, 0x78,              # IN A,(C)
                    0xED, 0x41,              # OUT (C),B
                    0x06, 0x55,              # LD B,55h
                    0xED, 0x41,              # OUT (C),B
                    0x01, 0x03, 0x00,        # LD BC,0003h
                    0x11, 0x00, 0x31,        # LD DE,3100h
                    0x21, 0x00, 0x30,        # LD HL,3000h
                    0xED, 0xB0,              # LDIR
                    0x76,                    # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000, ports=ports)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.a, 0x41)
        self.assertEqual(ports.writes, [(0x20, 0x00), (0x20, 0x55)])
        self.assertEqual(memory.slice(0x3100, 3), b"ABC")
        self.assertEqual(cpu.bc, 0x0000)
        self.assertEqual(cpu.hl, 0x3003)
        self.assertEqual(cpu.de, 0x3103)
        self.assertTrue(cpu.zero)

    def test_rotation_carry_and_backward_copy(self) -> None:
        memory = Memory(0x10000)
        memory.load(0x3000, b"XYZ")
        memory.load(
            0x0000,
            bytes(
                [
                    0x3E, 0x81,        # LD A,81h
                    0x07,              # RLCA -> 03h
                    0x0F,              # RRCA -> 81h
                    0x37,              # SCF -> zero false
                    0x17,              # RLA -> 03h
                    0x3F,              # CCF -> zero true
                    0x1F,              # RRA -> 01h
                    0x2F,              # CPL -> FEh
                    0x01, 0x03, 0x00,  # LD BC,0003h
                    0x11, 0x02, 0x31,  # LD DE,3102h
                    0x21, 0x02, 0x30,  # LD HL,3002h
                    0xED, 0xB8,        # LDDR
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.a, 0xFE)
        self.assertEqual(memory.slice(0x3100, 3), b"XYZ")
        self.assertEqual(cpu.bc, 0x0000)
        self.assertEqual(cpu.hl, 0x2FFF)
        self.assertEqual(cpu.de, 0x30FF)
        self.assertTrue(cpu.zero)

    def test_carry_branches_and_adc_sbc(self) -> None:
        memory = Memory(0x10000)
        memory.load(
            0x0000,
            bytes(
                [
                    0x37,              # SCF
                    0x38, 0x01,        # JR C,+1
                    0x76,              # HALT (skip)
                    0x3F,              # CCF
                    0x30, 0x01,        # JR NC,+1
                    0x76,              # HALT (skip)
                    0x3E, 0x10,        # LD A,10h
                    0x0E, 0x02,        # LD C,02h
                    0x37,              # SCF
                    0x89,              # ADC A,C -> 13h
                    0xCE, 0x01,        # ADC A,01h -> 15h
                    0x9A,              # SBC A,D -> 15h (D=0)
                    0xDE, 0x05,        # SBC A,05h -> 10h
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.a, 0x0F)
        self.assertFalse(cpu.zero)
        self.assertFalse(cpu.carry)

    def test_carry_condition_flow_and_rst_variants(self) -> None:
        memory = Memory(0x10000)
        memory.load(
            0x0000,
            bytes(
                [
                    0xCD, 0x10, 0x00,  # CALL 0010h
                    0x76,              # HALT
                ]
            ),
        )
        memory.load(
            0x0010,
            bytes(
                [
                    0x37,              # SCF
                    0xDA, 0x20, 0x00,  # JP C,0020h
                ]
            ),
        )
        memory.load(
            0x0020,
            bytes(
                [
                    0x3F,              # CCF
                    0xD4, 0x30, 0x00,  # CALL NC,0030h
                    0xD2, 0x40, 0x00,  # JP NC,0040h
                ]
            ),
        )
        memory.load(
            0x0030,
            bytes(
                [
                    0xCF,              # RST 08H
                    0xD0,              # RET NC
                ]
            ),
        )
        memory.load(0x0008, bytes([0xC9]))  # RET
        memory.load(
            0x0040,
            bytes(
                [
                    0xDF,              # RST 18H
                    0xEF,              # RST 28H
                    0xFF,              # RST 38H
                    0xC9,              # RET
                ]
            ),
        )
        memory.load(0x0018, bytes([0xC9]))
        memory.load(0x0028, bytes([0xC9]))
        memory.load(0x0038, bytes([0xC9]))
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertFalse(cpu.carry)
        self.assertEqual(cpu.pc, 0x0004)

    def test_parity_flow_after_ldir(self) -> None:
        memory = Memory(0x10000)
        memory.load(0x3000, b"AB")
        memory.load(
            0x0000,
            bytes(
                [
                    0x01, 0x02, 0x00,  # LD BC,0002h
                    0x11, 0x00, 0x31,  # LD DE,3100h
                    0x21, 0x00, 0x30,  # LD HL,3000h
                    0xED, 0xA0,        # LDI -> BC=1, parity_even true
                    0xEA, 0x20, 0x00,  # JP PE,0020h
                    0x76,              # HALT (skip)
                ]
            ),
        )
        memory.load(
            0x0020,
            bytes(
                [
                    0xED, 0xB0,        # LDIR -> BC=0, parity_even false
                    0xE2, 0x30, 0x00,  # JP PO,0030h
                    0x76,              # HALT (skip)
                ]
            ),
        )
        memory.load(0x0030, bytes([0x76]))
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(memory.slice(0x3100, 2), b"AB")
        self.assertEqual(cpu.bc, 0x0000)
        self.assertFalse(cpu.parity_even)

    def test_cb_bit_and_shift_operations(self) -> None:
        memory = Memory(0x10000)
        memory.load(
            0x0000,
            bytes(
                [
                    0x06, 0x81,        # LD B,81h
                    0xCB, 0x00,        # RLC B -> 03h
                    0x0E, 0x01,        # LD C,01h
                    0xCB, 0x39,        # SRL C -> 00h
                    0x21, 0x00, 0x20,  # LD HL,2000h
                    0x36, 0x80,        # LD (HL),80h
                    0xCB, 0x7E,        # BIT 7,(HL) -> zero false
                    0xCB, 0x26,        # SLA (HL) -> 00h
                    0xCB, 0x46,        # BIT 0,(HL) -> zero true
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.b, 0x03)
        self.assertEqual(cpu.c, 0x00)
        self.assertEqual(memory.read_byte(0x2000), 0x00)
        self.assertTrue(cpu.zero)

    def test_djnz_and_register_alu_groups(self) -> None:
        memory = Memory(0x10000)
        memory.load(
            0x0000,
            bytes(
                [
                    0x06, 0x03,        # LD B,03h
                    0x0E, 0x01,        # LD C,01h
                    0x10, 0xFE,        # DJNZ to self-adjacent until B=0
                    0x3E, 0x10,        # LD A,10h
                    0x06, 0x03,        # LD B,03h
                    0x80,              # ADD A,B -> 13h
                    0xA1,              # AND C -> 01h
                    0xA8,              # XOR B -> 02h
                    0xB1,              # OR C -> 03h
                    0x21, 0x00, 0x20,  # LD HL,2000h
                    0x36, 0x03,        # LD (HL),03h
                    0xBE,              # CP (HL)
                    0x90,              # SUB B -> 00h
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.b, 0x03)
        self.assertEqual(cpu.c, 0x01)
        self.assertEqual(cpu.a, 0x00)
        self.assertTrue(cpu.zero)

    def test_conditional_return_call_and_rst(self) -> None:
        memory = Memory(0x10000)
        memory.load(
            0x0000,
            bytes(
                [
                    0xCD, 0x10, 0x00,  # CALL 0010h
                    0x76,              # HALT
                ]
            ),
        )
        memory.load(
            0x0010,
            bytes(
                [
                    0xAF,              # XOR A
                    0xCC, 0x20, 0x00,  # CALL Z,0020h
                    0x3C,              # INC A
                    0xC4, 0x30, 0x00,  # CALL NZ,0030h
                    0xC9,              # RET
                ]
            ),
        )
        memory.load(
            0x0020,
            bytes(
                [
                    0xC8,              # RET Z
                ]
            ),
        )
        memory.load(
            0x0030,
            bytes(
                [
                    0xFF,              # RST 38H
                    0xC0,              # RET NZ
                ]
            ),
        )
        memory.load(
            0x0038,
            bytes(
                [
                    0xC9,              # RET
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.a, 0x01)
        self.assertFalse(cpu.zero)
        self.assertEqual(cpu.pc, 0x0004)

    def test_ex_af_af_prime_and_exx(self) -> None:
        memory = Memory(0x10000)
        memory.load(
            0x0000,
            bytes(
                [
                    0x3E, 0x12,        # LD A,12h
                    0x06, 0x34,        # LD B,34h
                    0x0E, 0x56,        # LD C,56h
                    0x11, 0x78, 0x9A,  # LD DE,9A78h
                    0x21, 0xBC, 0xDE,  # LD HL,DEBCh
                    0x08,              # EX AF,AF'
                    0xD9,              # EXX
                    0x3E, 0x99,        # LD A,99h
                    0x06, 0xAA,        # LD B,AAh
                    0x0E, 0xBB,        # LD C,BBh
                    0x11, 0xCC, 0xDD,  # LD DE,DDCCh
                    0x21, 0xEE, 0xFF,  # LD HL,FFEEh
                    0x08,              # EX AF,AF'
                    0xD9,              # EXX
                    0x76,              # HALT
                ]
            ),
        )
        cpu = Z80CPU(memory, entry_point=0x0000)
        while not cpu.halted:
            cpu.step()
        self.assertEqual(cpu.a, 0x12)
        self.assertEqual(cpu.bc, 0x3456)
        self.assertEqual(cpu.de, 0x9A78)
        self.assertEqual(cpu.hl, 0xDEBC)
        self.assertEqual(cpu.alt_a, 0x99)
        self.assertEqual((cpu.alt_b << 8) | cpu.alt_c, 0xAABB)
        self.assertEqual((cpu.alt_d << 8) | cpu.alt_e, 0xDDCC)
        self.assertEqual((cpu.alt_h << 8) | cpu.alt_l, 0xFFEE)


class PC8001PortTests(unittest.TestCase):
    def test_keyboard_and_display_ports(self) -> None:
        memory = PC8001Memory(vram_start=0x1000, geometry=TextGeometry(rows=2, cols=8))
        ports = PC8001Ports(memory=memory, port_log_limit=32)
        ports.queue_key(ord("A"))
        self.assertEqual(ports.read_port(KEYBOARD_STATUS_PORT), 1)
        self.assertEqual(ports.read_port(KEYBOARD_DATA_PORT), ord("A"))
        self.assertEqual(ports.read_port(SYSTEM_STATUS_PORT), 0)
        ports.write_port(SYSTEM_CONTROL_PORT, 0x80)
        self.assertEqual(ports.read_port(SYSTEM_STATUS_PORT), 0)
        self.assertEqual(ports.read_port(SYSTEM_STATUS_PORT), 0x20)
        self.assertEqual(ports.read_port(0x09), 0x7F)
        ports.queue_key(ord("Z"))
        self.assertEqual(ports.read_port(ALT_SYSTEM_INPUT_PORT), 0x02)
        ports.write_port(ALT_SYSTEM_CONTROL_PORT, 0x0E)
        ports.write_port(ALT_SYSTEM_COMMAND_PORT, 0x34)
        ports.write_port(ALT_SYSTEM_CONTROL_PORT, 0x09)
        self.assertEqual(ports.read_port(ALT_SYSTEM_INPUT_PORT), 0x04)
        ports.write_port(ALT_SYSTEM_CONTROL_PORT, 0x08)
        self.assertEqual(ports.read_port(ALT_SYSTEM_INPUT_PORT), 0x02)
        ports.write_port(ALT_SYSTEM_CONTROL_PORT, 0x0B)
        self.assertEqual(ports.read_port(ALT_SYSTEM_INPUT_PORT), 0x01)
        ports.write_port(ALT_SYSTEM_CONTROL_PORT, 0x0A)
        self.assertEqual(ports.read_port(ALT_SYSTEM_DATA_PORT), ord("Z"))
        ports.write_port(ALT_SYSTEM_COMMAND_PORT, 0x0D)
        ports.write_port(ALT_SYSTEM_CONTROL_PORT, 0x80)
        self.assertEqual(ports.read_port(SYSTEM_STATUS_PORT), 0x00)
        ports.write_port(DISPLAY_DATA_PORT, ord("B"))
        self.assertEqual(memory.screen.render_lines()[0], "B")
        self.assertIn("IN 0x20 KBD_STATUS 0x01", ports.format_port_log())
        self.assertIn("OUT 0x10 DISPLAY_DATA 0x42", ports.format_port_log())
        self.assertIn("OUT 0x10 DISPLAY_DATA from 0x0000", ports.format_port_summary())
        self.assertEqual(ports.format_port_summary(limit=0), "")


class _DummyPorts(PortDevice):
    def __init__(self) -> None:
        self.writes: list[tuple[int, int]] = []

    def read_port(self, port: int) -> int:
        return 0x41 if port == 0x20 else 0x00

    def write_port(self, port: int, value: int) -> None:
        self.writes.append((port & 0xFF, value & 0xFF))


if __name__ == "__main__":
    unittest.main()
