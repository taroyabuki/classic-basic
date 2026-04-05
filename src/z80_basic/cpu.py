from __future__ import annotations

from dataclasses import dataclass

from .memory import Memory


class UnsupportedInstruction(RuntimeError):
    pass


@dataclass(slots=True)
class ExecutionResult:
    reason: str
    steps: int
    pc: int


class PortDevice:
    def read_port(self, port: int) -> int:
        return 0xFF

    def write_port(self, port: int, value: int) -> None:
        return None


class Z80CPU:
    def __init__(self, memory: Memory, entry_point: int, ports: PortDevice | None = None) -> None:
        self.memory = memory
        self.ports = ports or PortDevice()
        self.a = 0
        self.b = 0
        self.c = 0
        self.d = 0
        self.e = 0
        self.h = 0
        self.l = 0
        self.alt_a = 0
        self.alt_b = 0
        self.alt_c = 0
        self.alt_d = 0
        self.alt_e = 0
        self.alt_h = 0
        self.alt_l = 0
        self.pc = entry_point & 0xFFFF
        self.sp = 0xFFFE
        self.halted = False
        self.zero = False
        self.carry = False
        self.parity_even = False
        self.sign = False
        self.alt_zero = False
        self.alt_carry = False
        self.alt_parity_even = False
        self.alt_sign = False
        self.interrupt_mode = 0
        self.i = 0
        self.r = 0
        self.ix = 0
        self.iy = 0
        self.trace = False

    @property
    def bc(self) -> int:
        return (self.b << 8) | self.c

    @bc.setter
    def bc(self, value: int) -> None:
        self.b = (value >> 8) & 0xFF
        self.c = value & 0xFF

    @property
    def de(self) -> int:
        return (self.d << 8) | self.e

    @de.setter
    def de(self, value: int) -> None:
        self.d = (value >> 8) & 0xFF
        self.e = value & 0xFF

    @property
    def hl(self) -> int:
        return (self.h << 8) | self.l

    @hl.setter
    def hl(self, value: int) -> None:
        self.h = (value >> 8) & 0xFF
        self.l = value & 0xFF

    def read_next_byte(self) -> int:
        value = self.memory.read_byte(self.pc)
        self.pc = (self.pc + 1) & 0xFFFF
        return value

    def _notify_port_write(self, origin_pc: int) -> None:
        pass  # Subclasses (e.g. PC8801CPU) may override for port logging

    def read_next_word(self) -> int:
        low = self.read_next_byte()
        high = self.read_next_byte()
        return low | (high << 8)

    def push_word(self, value: int) -> None:
        self.sp = (self.sp - 1) & 0xFFFF
        self.memory.write_byte(self.sp, (value >> 8) & 0xFF)
        self.sp = (self.sp - 1) & 0xFFFF
        self.memory.write_byte(self.sp, value & 0xFF)

    def pop_word(self) -> int:
        low = self.memory.read_byte(self.sp)
        self.sp = (self.sp + 1) & 0xFFFF
        high = self.memory.read_byte(self.sp)
        self.sp = (self.sp + 1) & 0xFFFF
        return low | (high << 8)

    def step(self) -> None:
        if self.trace:
            self._trace_state()
        opcode = self.read_next_byte()

        if opcode == 0x00:
            return
        if opcode == 0x01:
            self.bc = self.read_next_word()
            return
        if opcode == 0x02:
            self.memory.write_byte(self.bc, self.a)
            return
        if opcode == 0x03:
            self.bc = (self.bc + 1) & 0xFFFF
            return
        if opcode == 0x07:
            self.carry = bool(self.a & 0x80)
            self.a = ((self.a << 1) | (self.a >> 7)) & 0xFF
            return
        if opcode == 0x0B:
            self.bc = (self.bc - 1) & 0xFFFF
            return
        if opcode == 0x04:
            self.b = self._inc8(self.b)
            return
        if opcode == 0x05:
            self.b = self._dec8(self.b)
            return
        if opcode == 0x18:
            displacement = self._read_signed_byte()
            self.pc = (self.pc + displacement) & 0xFFFF
            return
        if opcode == 0x0F:
            self.carry = bool(self.a & 0x01)
            self.a = ((self.a >> 1) | ((self.a & 0x01) << 7)) & 0xFF
            return
        if opcode == 0x20:
            displacement = self._read_signed_byte()
            if not self.zero:
                self.pc = (self.pc + displacement) & 0xFFFF
            return
        if opcode == 0x30:
            displacement = self._read_signed_byte()
            if not self.carry:
                self.pc = (self.pc + displacement) & 0xFFFF
            return
        if opcode == 0x06:
            self.b = self.read_next_byte()
            return
        if opcode == 0x08:
            self.a, self.alt_a = self.alt_a, self.a
            self.zero, self.alt_zero = self.alt_zero, self.zero
            self.carry, self.alt_carry = self.alt_carry, self.carry
            self.parity_even, self.alt_parity_even = self.alt_parity_even, self.parity_even
            self.sign, self.alt_sign = self.alt_sign, self.sign
            return
        if opcode == 0x0C:
            self.c = self._inc8(self.c)
            return
        if opcode == 0x0D:
            self.c = self._dec8(self.c)
            return
        if opcode == 0x0E:
            self.c = self.read_next_byte()
            return
        if opcode == 0x10:
            displacement = self._read_signed_byte()
            self.b = (self.b - 1) & 0xFF
            if self.b != 0:
                self.pc = (self.pc + displacement) & 0xFFFF
            return
        if opcode == 0x09:
            self.hl = self._add16(self.hl, self.bc)
            return
        if opcode == 0x0A:
            self.a = self.memory.read_byte(self.bc)
            return
        if opcode == 0x11:
            self.de = self.read_next_word()
            return
        if opcode == 0x12:
            self.memory.write_byte(self.de, self.a)
            return
        if opcode == 0x13:
            self.de = (self.de + 1) & 0xFFFF
            return
        if opcode == 0x1B:
            self.de = (self.de - 1) & 0xFFFF
            return
        if opcode == 0x19:
            self.hl = self._add16(self.hl, self.de)
            return
        if opcode == 0x1A:
            self.a = self.memory.read_byte(self.de)
            return
        if opcode == 0x14:
            self.d = self._inc8(self.d)
            return
        if opcode == 0x15:
            self.d = self._dec8(self.d)
            return
        if opcode == 0x16:
            self.d = self.read_next_byte()
            return
        if opcode == 0x1C:
            self.e = self._inc8(self.e)
            return
        if opcode == 0x1D:
            self.e = self._dec8(self.e)
            return
        if opcode == 0x1E:
            self.e = self.read_next_byte()
            return
        if opcode == 0x17:
            carry_in = 1 if self.carry else 0
            self.carry = bool(self.a & 0x80)
            self.a = ((self.a << 1) | carry_in) & 0xFF
            return
        if opcode == 0x21:
            self.hl = self.read_next_word()
            return
        if opcode == 0x22:
            address = self.read_next_word()
            self.memory.write_word(address, self.hl)
            return
        if opcode == 0x23:
            self.hl = (self.hl + 1) & 0xFFFF
            return
        if opcode == 0x2B:
            self.hl = (self.hl - 1) & 0xFFFF
            return
        if opcode == 0x24:
            self.h = self._inc8(self.h)
            return
        if opcode == 0x25:
            self.h = self._dec8(self.h)
            return
        if opcode == 0x26:
            self.h = self.read_next_byte()
            return
        if opcode == 0x28:
            displacement = self._read_signed_byte()
            if self.zero:
                self.pc = (self.pc + displacement) & 0xFFFF
            return
        if opcode == 0x38:
            displacement = self._read_signed_byte()
            if self.carry:
                self.pc = (self.pc + displacement) & 0xFFFF
            return
        if opcode == 0x29:
            self.hl = self._add16(self.hl, self.hl)
            return
        if opcode == 0x1F:
            carry_in = 0x80 if self.carry else 0
            self.carry = bool(self.a & 0x01)
            self.a = ((self.a >> 1) | carry_in) & 0xFF
            return
        if opcode == 0x2A:
            address = self.read_next_word()
            self.hl = self.memory.read_word(address)
            return
        if opcode == 0x2C:
            self.l = self._inc8(self.l)
            return
        if opcode == 0x2D:
            self.l = self._dec8(self.l)
            return
        if opcode == 0x2E:
            self.l = self.read_next_byte()
            return
        if opcode == 0x2F:
            self.a ^= 0xFF
            self.zero = self.a == 0
            return
        if opcode == 0x31:
            self.sp = self.read_next_word()
            return
        if opcode == 0x33:
            self.sp = (self.sp + 1) & 0xFFFF
            return
        if opcode == 0x3B:
            self.sp = (self.sp - 1) & 0xFFFF
            return
        if opcode == 0x37:
            self.carry = True
            return
        if opcode == 0x34:
            self.memory.write_byte(self.hl, self._inc8(self.memory.read_byte(self.hl)))
            return
        if opcode == 0x35:
            self.memory.write_byte(self.hl, self._dec8(self.memory.read_byte(self.hl)))
            return
        if opcode == 0x32:
            address = self.read_next_word()
            self.memory.write_byte(address, self.a)
            return
        if opcode == 0x36:
            self.memory.write_byte(self.hl, self.read_next_byte())
            return
        if opcode == 0x39:
            self.hl = self._add16(self.hl, self.sp)
            return
        if opcode == 0x3F:
            self.carry = not self.carry
            return
        if opcode == 0x3A:
            address = self.read_next_word()
            self.a = self.memory.read_byte(address)
            return
        if opcode == 0x3C:
            self.a = self._inc8(self.a)
            return
        if opcode == 0x3D:
            self.a = self._dec8(self.a)
            return
        if opcode == 0x3E:
            self.a = self.read_next_byte()
            return
        if 0x80 <= opcode <= 0x87:
            self.a = self._add8(self.a, self._read_reg(opcode & 0x07))
            return
        if 0x88 <= opcode <= 0x8F:
            self.a = self._add8(self.a, self._read_reg(opcode & 0x07), carry_in=1 if self.carry else 0)
            return
        if 0x90 <= opcode <= 0x97:
            self.a = self._sub8(self.a, self._read_reg(opcode & 0x07))
            return
        if 0x98 <= opcode <= 0x9F:
            self.a = self._sub8(self.a, self._read_reg(opcode & 0x07), carry_in=1 if self.carry else 0)
            return
        if 0xA0 <= opcode <= 0xA7:
            self.a &= self._read_reg(opcode & 0x07)
            self._set_logic_flags(self.a)
            return
        if 0xA8 <= opcode <= 0xAF:
            self.a ^= self._read_reg(opcode & 0x07)
            self._set_logic_flags(self.a)
            return
        if 0xB0 <= opcode <= 0xB7:
            self.a |= self._read_reg(opcode & 0x07)
            self._set_logic_flags(self.a)
            return
        if 0xB8 <= opcode <= 0xBF:
            value = self._read_reg(opcode & 0x07)
            self.zero = self.a == value
            self.carry = self.a < value
            self.parity_even = self.zero
            return
        if 0x40 <= opcode <= 0x7F and opcode != 0x76:
            self._write_reg((opcode >> 3) & 0x07, self._read_reg(opcode & 0x07))
            return
        if opcode == 0x76:
            self.halted = True
            return
        if opcode == 0xC0:
            if not self.zero:
                self.pc = self.pop_word()
            return
        if opcode == 0xC8:
            if self.zero:
                self.pc = self.pop_word()
            return
        if opcode == 0xC6:
            self.a = self._add8(self.a, self.read_next_byte())
            return
        if opcode == 0xD6:
            self.a = self._sub8(self.a, self.read_next_byte())
            return
        if opcode == 0xCE:
            self.a = self._add8(self.a, self.read_next_byte(), carry_in=1 if self.carry else 0)
            return
        if opcode == 0xE6:
            self.a &= self.read_next_byte()
            self._set_logic_flags(self.a)
            return
        if opcode == 0xEE:
            self.a ^= self.read_next_byte()
            self._set_logic_flags(self.a)
            return
        if opcode == 0xC1:
            self.bc = self.pop_word()
            return
        if opcode == 0xB7:
            self.zero = self.a == 0
            self.parity_even = self._even_parity(self.a)
            return
        if opcode == 0xC2:
            address = self.read_next_word()
            if not self.zero:
                self.pc = address
            return
        if opcode == 0xC3:
            self.pc = self.read_next_word()
            return
        if opcode == 0xC4:
            address = self.read_next_word()
            if not self.zero:
                self.push_word(self.pc)
                self.pc = address
            return
        if opcode == 0xC5:
            self.push_word(self.bc)
            return
        if opcode == 0xC7:
            self.push_word(self.pc)
            self.pc = 0x0000
            return
        if opcode == 0xCA:
            address = self.read_next_word()
            if self.zero:
                self.pc = address
            return
        if opcode == 0xC9:
            self.pc = self.pop_word()
            return
        if opcode == 0xCB:
            self._step_cb()
            return
        if opcode == 0xCD:
            destination = self.read_next_word()
            self.push_word(self.pc)
            self.pc = destination
            return
        if opcode == 0xCC:
            destination = self.read_next_word()
            if self.zero:
                self.push_word(self.pc)
                self.pc = destination
            return
        if opcode == 0xCF:
            self.push_word(self.pc)
            self.pc = 0x0008
            return
        if opcode == 0xD0:
            if not self.carry:
                self.pc = self.pop_word()
            return
        if opcode == 0xD1:
            self.de = self.pop_word()
            return
        if opcode == 0xD2:
            address = self.read_next_word()
            if not self.carry:
                self.pc = address
            return
        if opcode == 0xD3:
            port = self.read_next_byte()
            self._notify_port_write((self.pc - 2) & 0xFFFF)
            self.ports.write_port(port, self.a)
            return
        if opcode == 0xD4:
            destination = self.read_next_word()
            if not self.carry:
                self.push_word(self.pc)
                self.pc = destination
            return
        if opcode == 0xD5:
            self.push_word(self.de)
            return
        if opcode == 0xD7:
            self.push_word(self.pc)
            self.pc = 0x0010
            return
        if opcode == 0xD8:
            if self.carry:
                self.pc = self.pop_word()
            return
        if opcode == 0xD9:
            self.b, self.alt_b = self.alt_b, self.b
            self.c, self.alt_c = self.alt_c, self.c
            self.d, self.alt_d = self.alt_d, self.d
            self.e, self.alt_e = self.alt_e, self.e
            self.h, self.alt_h = self.alt_h, self.h
            self.l, self.alt_l = self.alt_l, self.l
            return
        if opcode == 0xDA:
            address = self.read_next_word()
            if self.carry:
                self.pc = address
            return
        if opcode == 0xDB:
            port = self.read_next_byte()
            self._notify_port_write((self.pc - 2) & 0xFFFF)
            self.a = self.ports.read_port(port)
            self.zero = self.a == 0
            return
        if opcode == 0xDC:
            destination = self.read_next_word()
            if self.carry:
                self.push_word(self.pc)
                self.pc = destination
            return
        if opcode == 0xDF:
            self.push_word(self.pc)
            self.pc = 0x0018
            return
        if opcode == 0xED:
            self._step_ed()
            return
        if opcode == 0xE0:
            if not self.parity_even:
                self.pc = self.pop_word()
            return
        if opcode == 0xE1:
            self.hl = self.pop_word()
            return
        if opcode == 0xE3:
            value = self.memory.read_word(self.sp)
            self.memory.write_word(self.sp, self.hl)
            self.hl = value
            return
        if opcode == 0xE4:
            destination = self.read_next_word()
            if not self.parity_even:
                self.push_word(self.pc)
                self.pc = destination
            return
        if opcode == 0xE7:
            self.push_word(self.pc)
            self.pc = 0x0020
            return
        if opcode == 0xE8:
            if self.parity_even:
                self.pc = self.pop_word()
            return
        if opcode == 0xE5:
            self.push_word(self.hl)
            return
        if opcode == 0xE2:
            address = self.read_next_word()
            if not self.parity_even:
                self.pc = address
            return
        if opcode == 0xE9:
            self.pc = self.hl
            return
        if opcode == 0xEA:
            address = self.read_next_word()
            if self.parity_even:
                self.pc = address
            return
        if opcode == 0xEB:
            self.de, self.hl = self.hl, self.de
            return
        if opcode == 0xEC:
            destination = self.read_next_word()
            if self.parity_even:
                self.push_word(self.pc)
                self.pc = destination
            return
        if opcode == 0xEF:
            self.push_word(self.pc)
            self.pc = 0x0028
            return
        if opcode == 0xF0:
            if not self.sign:
                self.pc = self.pop_word()
            return
        if opcode == 0xF1:
            value = self.pop_word()
            self.a = (value >> 8) & 0xFF
            self.zero = bool(value & 0x40)
            self.carry = bool(value & 0x01)
            self.parity_even = bool(value & 0x04)
            self.sign = bool(value & 0x80)
            return
        if opcode == 0xF3:
            return
        if opcode == 0xF4:
            destination = self.read_next_word()
            if not self.sign:
                self.push_word(self.pc)
                self.pc = destination
            return
        if opcode == 0xF5:
            self.push_word(
                (self.a << 8)
                | (0x80 if self.sign else 0x00)
                | (0x40 if self.zero else 0x00)
                | (0x04 if self.parity_even else 0x00)
                | (0x01 if self.carry else 0x00)
            )
            return
        if opcode == 0xF2:
            address = self.read_next_word()
            if not self.sign:
                self.pc = address
            return
        if opcode == 0xFB:
            return
        if opcode == 0xF6:
            self.a |= self.read_next_byte()
            self._set_logic_flags(self.a)
            return
        if opcode == 0xF7:
            self.push_word(self.pc)
            self.pc = 0x0030
            return
        if opcode == 0xF8:
            if self.sign:
                self.pc = self.pop_word()
            return
        if opcode == 0xF9:
            self.sp = self.hl
            return
        if opcode == 0xDE:
            self.a = self._sub8(self.a, self.read_next_byte(), carry_in=1 if self.carry else 0)
            return
        if opcode == 0xFE:
            value = self.read_next_byte()
            self.zero = self.a == value
            self.carry = self.a < value
            self.sign = ((self.a - value) & 0x80) != 0
            return
        if opcode == 0xFA:
            address = self.read_next_word()
            if self.sign:
                self.pc = address
            return
        if opcode == 0xFF:
            self.push_word(self.pc)
            self.pc = 0x0038
            return
        if opcode == 0xFC:
            destination = self.read_next_word()
            if self.sign:
                self.push_word(self.pc)
                self.pc = destination
            return
        if opcode == 0xDD:
            self._step_xp(is_iy=False)
            return
        if opcode == 0xFD:
            self._step_xp(is_iy=True)
            return

        raise UnsupportedInstruction(
            f"unsupported opcode 0x{opcode:02X} at 0x{(self.pc - 1) & 0xFFFF:04X}"
        )

    @staticmethod
    def decode_instruction(memory: Memory, pc: int) -> str:
        reader = memory.read_byte
        opcode = reader(pc)
        byte1 = reader((pc + 1) & 0xFFFF)
        byte2 = reader((pc + 2) & 0xFFFF)
        word = byte1 | (byte2 << 8)

        if opcode == 0x00:
            return "NOP"
        if opcode == 0x01:
            return f"LD BC,0x{word:04X}"
        if opcode == 0x02:
            return "LD (BC),A"
        if opcode == 0x03:
            return "INC BC"
        if opcode == 0x07:
            return "RLCA"
        if opcode == 0x04:
            return "INC B"
        if opcode == 0x05:
            return "DEC B"
        if opcode == 0x06:
            return f"LD B,0x{byte1:02X}"
        if opcode == 0x08:
            return "EX AF,AF'"
        if opcode == 0x09:
            return "ADD HL,BC"
        if opcode == 0x0A:
            return "LD A,(BC)"
        if opcode == 0x0B:
            return "DEC BC"
        if opcode == 0x0C:
            return "INC C"
        if opcode == 0x0D:
            return "DEC C"
        if opcode == 0x0E:
            return f"LD C,0x{byte1:02X}"
        if opcode == 0x10:
            return f"DJNZ {Z80CPU._decode_relative(pc, byte1)}"
        if opcode == 0x11:
            return f"LD DE,0x{word:04X}"
        if opcode == 0x12:
            return "LD (DE),A"
        if opcode == 0x13:
            return "INC DE"
        if opcode == 0x14:
            return "INC D"
        if opcode == 0x15:
            return "DEC D"
        if opcode == 0x16:
            return f"LD D,0x{byte1:02X}"
        if opcode == 0x18:
            return f"JR {Z80CPU._decode_relative(pc, byte1)}"
        if opcode == 0x0F:
            return "RRCA"
        if opcode == 0x19:
            return "ADD HL,DE"
        if opcode == 0x1A:
            return "LD A,(DE)"
        if opcode == 0x1B:
            return "DEC DE"
        if opcode == 0x1C:
            return "INC E"
        if opcode == 0x1D:
            return "DEC E"
        if opcode == 0x1E:
            return f"LD E,0x{byte1:02X}"
        if opcode == 0x17:
            return "RLA"
        if opcode == 0x20:
            return f"JR NZ,{Z80CPU._decode_relative(pc, byte1)}"
        if opcode == 0x30:
            return f"JR NC,{Z80CPU._decode_relative(pc, byte1)}"
        if opcode == 0x21:
            return f"LD HL,0x{word:04X}"
        if opcode == 0x22:
            return f"LD (0x{word:04X}),HL"
        if opcode == 0x23:
            return "INC HL"
        if opcode == 0x24:
            return "INC H"
        if opcode == 0x25:
            return "DEC H"
        if opcode == 0x26:
            return f"LD H,0x{byte1:02X}"
        if opcode == 0x28:
            return f"JR Z,{Z80CPU._decode_relative(pc, byte1)}"
        if opcode == 0x38:
            return f"JR C,{Z80CPU._decode_relative(pc, byte1)}"
        if opcode == 0x29:
            return "ADD HL,HL"
        if opcode == 0x1F:
            return "RRA"
        if opcode == 0x2A:
            return f"LD HL,(0x{word:04X})"
        if opcode == 0x2B:
            return "DEC HL"
        if opcode == 0x2C:
            return "INC L"
        if opcode == 0x2D:
            return "DEC L"
        if opcode == 0x2E:
            return f"LD L,0x{byte1:02X}"
        if opcode == 0x2F:
            return "CPL"
        if opcode == 0x31:
            return f"LD SP,0x{word:04X}"
        if opcode == 0x32:
            return f"LD (0x{word:04X}),A"
        if opcode == 0x33:
            return "INC SP"
        if opcode == 0x37:
            return "SCF"
        if opcode == 0x34:
            return "INC (HL)"
        if opcode == 0x35:
            return "DEC (HL)"
        if opcode == 0x36:
            return f"LD (HL),0x{byte1:02X}"
        if opcode == 0x39:
            return "ADD HL,SP"
        if opcode == 0x3F:
            return "CCF"
        if opcode == 0x3A:
            return f"LD A,(0x{word:04X})"
        if opcode == 0x3B:
            return "DEC SP"
        if opcode == 0x3C:
            return "INC A"
        if opcode == 0x3D:
            return "DEC A"
        if opcode == 0x3E:
            return f"LD A,0x{byte1:02X}"
        if 0x80 <= opcode <= 0x87:
            return f"ADD A,{Z80CPU._reg_name(opcode & 0x07)}"
        if 0x88 <= opcode <= 0x8F:
            return f"ADC A,{Z80CPU._reg_name(opcode & 0x07)}"
        if 0x90 <= opcode <= 0x97:
            return f"SUB {Z80CPU._reg_name(opcode & 0x07)}"
        if 0x98 <= opcode <= 0x9F:
            return f"SBC A,{Z80CPU._reg_name(opcode & 0x07)}"
        if 0xA0 <= opcode <= 0xA7:
            return f"AND {Z80CPU._reg_name(opcode & 0x07)}"
        if 0xA8 <= opcode <= 0xAF:
            return f"XOR {Z80CPU._reg_name(opcode & 0x07)}"
        if 0xB0 <= opcode <= 0xB7:
            return f"OR {Z80CPU._reg_name(opcode & 0x07)}"
        if 0xB8 <= opcode <= 0xBF:
            return f"CP {Z80CPU._reg_name(opcode & 0x07)}"
        if 0x40 <= opcode <= 0x7F and opcode != 0x76:
            return f"LD {Z80CPU._reg_name((opcode >> 3) & 0x07)},{Z80CPU._reg_name(opcode & 0x07)}"
        if opcode == 0x76:
            return "HALT"
        if opcode == 0xC0:
            return "RET NZ"
        if opcode == 0xC8:
            return "RET Z"
        if opcode == 0xB7:
            return "OR A"
        if opcode == 0xC1:
            return "POP BC"
        if opcode == 0xC2:
            return f"JP NZ,0x{word:04X}"
        if opcode == 0xC3:
            return f"JP 0x{word:04X}"
        if opcode == 0xC4:
            return f"CALL NZ,0x{word:04X}"
        if opcode == 0xC5:
            return "PUSH BC"
        if opcode == 0xC6:
            return f"ADD A,0x{byte1:02X}"
        if opcode == 0xC7:
            return "RST 00H"
        if opcode == 0xC9:
            return "RET"
        if opcode == 0xCB:
            return Z80CPU._decode_cb(memory, pc)
        if opcode == 0xCA:
            return f"JP Z,0x{word:04X}"
        if opcode == 0xCC:
            return f"CALL Z,0x{word:04X}"
        if opcode == 0xCE:
            return f"ADC A,0x{byte1:02X}"
        if opcode == 0xCD:
            return f"CALL 0x{word:04X}"
        if opcode == 0xCF:
            return "RST 08H"
        if opcode == 0xD0:
            return "RET NC"
        if opcode == 0xD1:
            return "POP DE"
        if opcode == 0xD8:
            return "RET C"
        if opcode == 0xD2:
            return f"JP NC,0x{word:04X}"
        if opcode == 0xD3:
            return f"OUT (0x{byte1:02X}),A"
        if opcode == 0xD4:
            return f"CALL NC,0x{word:04X}"
        if opcode == 0xD5:
            return "PUSH DE"
        if opcode == 0xD7:
            return "RST 10H"
        if opcode == 0xD9:
            return "EXX"
        if opcode == 0xD6:
            return f"SUB 0x{byte1:02X}"
        if opcode == 0xDA:
            return f"JP C,0x{word:04X}"
        if opcode == 0xDE:
            return f"SBC A,0x{byte1:02X}"
        if opcode == 0xDB:
            return f"IN A,(0x{byte1:02X})"
        if opcode == 0xDC:
            return f"CALL C,0x{word:04X}"
        if opcode == 0xDF:
            return "RST 18H"
        if opcode == 0xED:
            return Z80CPU._decode_ed(memory, pc)
        if opcode == 0xE1:
            return "POP HL"
        if opcode == 0xE3:
            return "EX (SP),HL"
        if opcode == 0xE7:
            return "RST 20H"
        if opcode == 0xE5:
            return "PUSH HL"
        if opcode == 0xE6:
            return f"AND 0x{byte1:02X}"
        if opcode == 0xE2:
            return f"JP PO,0x{word:04X}"
        if opcode == 0xE0:
            return "RET PO"
        if opcode == 0xE9:
            return "JP (HL)"
        if opcode == 0xE4:
            return f"CALL PO,0x{word:04X}"
        if opcode == 0xE8:
            return "RET PE"
        if opcode == 0xEA:
            return f"JP PE,0x{word:04X}"
        if opcode == 0xEB:
            return "EX DE,HL"
        if opcode == 0xEC:
            return f"CALL PE,0x{word:04X}"
        if opcode == 0xEF:
            return "RST 28H"
        if opcode == 0xF0:
            return "RET P"
        if opcode == 0xEE:
            return f"XOR 0x{byte1:02X}"
        if opcode == 0xF1:
            return "POP AF"
        if opcode == 0xF2:
            return f"JP P,0x{word:04X}"
        if opcode == 0xF3:
            return "DI"
        if opcode == 0xF4:
            return f"CALL P,0x{word:04X}"
        if opcode == 0xF5:
            return "PUSH AF"
        if opcode == 0xF6:
            return f"OR 0x{byte1:02X}"
        if opcode == 0xF7:
            return "RST 30H"
        if opcode == 0xF8:
            return "RET M"
        if opcode == 0xF9:
            return "LD SP,HL"
        if opcode == 0xFA:
            return f"JP M,0x{word:04X}"
        if opcode == 0xFB:
            return "EI"
        if opcode == 0xFE:
            return f"CP 0x{byte1:02X}"
        if opcode == 0xFF:
            return "RST 38H"
        if opcode == 0xFC:
            return f"CALL M,0x{word:04X}"
        return f"DB 0x{opcode:02X}"

    def _read_signed_byte(self) -> int:
        value = self.read_next_byte()
        return value - 0x100 if value & 0x80 else value

    def _inc8(self, value: int) -> int:
        result = (value + 1) & 0xFF
        self.zero = result == 0
        self.sign = bool(result & 0x80)
        return result

    def _dec8(self, value: int) -> int:
        result = (value - 1) & 0xFF
        self.zero = result == 0
        self.sign = bool(result & 0x80)
        return result

    def _add8(self, left: int, right: int, *, carry_in: int = 0) -> int:
        total = left + right + carry_in
        result = total & 0xFF
        self.zero = result == 0
        self.carry = total > 0xFF
        self.parity_even = self._even_parity(result)
        self.sign = bool(result & 0x80)
        return result

    def _add16(self, left: int, right: int) -> int:
        total = left + right
        self.carry = total > 0xFFFF
        return total & 0xFFFF

    def _sub8(self, left: int, right: int, *, carry_in: int = 0) -> int:
        total = left - right - carry_in
        result = total & 0xFF
        self.zero = result == 0
        self.carry = total < 0
        self.parity_even = self._even_parity(result)
        self.sign = bool(result & 0x80)
        return result

    def _set_logic_flags(self, value: int) -> None:
        self.zero = value == 0
        self.carry = False
        self.parity_even = self._even_parity(value)
        self.sign = bool(value & 0x80)

    @staticmethod
    def _even_parity(value: int) -> bool:
        return (value & 0xFF).bit_count() % 2 == 0

    def _read_reg(self, code: int) -> int:
        if code == 0:
            return self.b
        if code == 1:
            return self.c
        if code == 2:
            return self.d
        if code == 3:
            return self.e
        if code == 4:
            return self.h
        if code == 5:
            return self.l
        if code == 6:
            return self.memory.read_byte(self.hl)
        return self.a

    def _write_reg(self, code: int, value: int) -> None:
        value &= 0xFF
        if code == 0:
            self.b = value
            return
        if code == 1:
            self.c = value
            return
        if code == 2:
            self.d = value
            return
        if code == 3:
            self.e = value
            return
        if code == 4:
            self.h = value
            return
        if code == 5:
            self.l = value
            return
        if code == 6:
            self.memory.write_byte(self.hl, value)
            return
        self.a = value

    def _trace_state(self) -> None:
        print(
            f"PC={self.pc:04X} SP={self.sp:04X} "
            f"A={self.a:02X} B={self.b:02X} C={self.c:02X} "
            f"D={self.d:02X} E={self.e:02X} H={self.h:02X} L={self.l:02X} "
            f"Z={int(self.zero)} C={int(self.carry)} P={int(self.parity_even)}"
        )

    def _step_cb(self) -> None:
        opcode = self.read_next_byte()
        reg = opcode & 0x07
        value = self._read_reg(reg)
        group = opcode >> 6
        if group == 0:
            op = (opcode >> 3) & 0x07
            if op == 0x00:
                self.carry = bool(value & 0x80)
                result = ((value << 1) | (value >> 7)) & 0xFF
            elif op == 0x01:
                self.carry = bool(value & 0x01)
                result = ((value >> 1) | ((value & 0x01) << 7)) & 0xFF
            elif op == 0x02:
                carry_in = 1 if self.carry else 0
                self.carry = bool(value & 0x80)
                result = ((value << 1) | carry_in) & 0xFF
            elif op == 0x03:
                carry_in = 0x80 if self.carry else 0
                self.carry = bool(value & 0x01)
                result = ((value >> 1) | carry_in) & 0xFF
            elif op == 0x04:
                self.carry = bool(value & 0x80)
                result = (value << 1) & 0xFF
            elif op == 0x05:
                self.carry = bool(value & 0x01)
                result = ((value >> 1) | (value & 0x80)) & 0xFF
            elif op == 0x07:
                self.carry = bool(value & 0x01)
                result = (value >> 1) & 0xFF
            else:
                raise UnsupportedInstruction(
                    f"unsupported CB opcode 0x{opcode:02X} at 0x{(self.pc - 2) & 0xFFFF:04X}"
                )
            self._write_reg(reg, result)
            self.zero = result == 0
            self.parity_even = self._even_parity(result)
            return
        if group == 1:
            bit = (opcode >> 3) & 0x07
            self.zero = (value & (1 << bit)) == 0
            self.parity_even = self.zero
            return
        raise UnsupportedInstruction(
            f"unsupported CB opcode 0x{opcode:02X} at 0x{(self.pc - 2) & 0xFFFF:04X}"
        )

    def _step_xp(self, is_iy: bool) -> None:
        """Handle DD (IX) and FD (IY) prefix instructions."""
        xp = self.iy if is_iy else self.ix
        opcode = self.read_next_byte()

        def xp_disp() -> int:
            d = self.read_next_byte()
            return d - 256 if d >= 128 else d

        def set_xp(val: int) -> None:
            val &= 0xFFFF
            if is_iy:
                self.iy = val
            else:
                self.ix = val

        def read_xp_d() -> int:
            return self.memory.read_byte((xp + xp_disp()) & 0xFFFF)

        def write_xp_d(d: int, val: int) -> None:
            self.memory.write_byte((xp + d) & 0xFFFF, val & 0xFF)

        if opcode == 0x21:
            set_xp(self.read_next_word())
        elif opcode == 0x22:
            self.memory.write_word(self.read_next_word(), xp)
        elif opcode == 0x2A:
            set_xp(self.memory.read_word(self.read_next_word()))
        elif opcode == 0x23:
            set_xp(xp + 1)
        elif opcode == 0x2B:
            set_xp(xp - 1)
        elif opcode == 0x09:
            total = xp + self.bc; self.carry = total > 0xFFFF; set_xp(total)
        elif opcode == 0x19:
            total = xp + self.de; self.carry = total > 0xFFFF; set_xp(total)
        elif opcode == 0x29:
            total = xp + xp; self.carry = total > 0xFFFF; set_xp(total)
        elif opcode == 0x39:
            total = xp + self.sp; self.carry = total > 0xFFFF; set_xp(total)
        elif opcode == 0xE1:
            set_xp(self.pop_word())
        elif opcode == 0xE5:
            self.push_word(xp)
        elif opcode == 0xE3:
            tmp = self.memory.read_word(self.sp)
            self.memory.write_word(self.sp, xp)
            set_xp(tmp)
        elif opcode == 0xE9:
            self.pc = xp
        elif opcode == 0xF9:
            self.sp = xp
        elif opcode == 0x34:
            d = xp_disp(); addr = (xp + d) & 0xFFFF
            self.memory.write_byte(addr, self._inc8(self.memory.read_byte(addr)))
        elif opcode == 0x35:
            d = xp_disp(); addr = (xp + d) & 0xFFFF
            self.memory.write_byte(addr, self._dec8(self.memory.read_byte(addr)))
        elif opcode == 0x36:
            d = xp_disp(); n = self.read_next_byte()
            self.memory.write_byte((xp + d) & 0xFFFF, n)
        elif opcode == 0x46:
            self.b = read_xp_d()
        elif opcode == 0x4E:
            self.c = read_xp_d()
        elif opcode == 0x56:
            self.d = read_xp_d()
        elif opcode == 0x5E:
            self.e = read_xp_d()
        elif opcode == 0x66:
            self.h = read_xp_d()
        elif opcode == 0x6E:
            self.l = read_xp_d()
        elif opcode == 0x7E:
            self.a = read_xp_d()
        elif opcode == 0x70:
            d = xp_disp(); write_xp_d(d, self.b)
        elif opcode == 0x71:
            d = xp_disp(); write_xp_d(d, self.c)
        elif opcode == 0x72:
            d = xp_disp(); write_xp_d(d, self.d)
        elif opcode == 0x73:
            d = xp_disp(); write_xp_d(d, self.e)
        elif opcode == 0x74:
            d = xp_disp(); write_xp_d(d, self.h)
        elif opcode == 0x75:
            d = xp_disp(); write_xp_d(d, self.l)
        elif opcode == 0x77:
            d = xp_disp(); write_xp_d(d, self.a)
        elif opcode == 0x86:
            self.a = self._add8(self.a, read_xp_d())
        elif opcode == 0x8E:
            self.a = self._add8(self.a, read_xp_d(), carry_in=1 if self.carry else 0)
        elif opcode == 0x96:
            self.a = self._sub8(self.a, read_xp_d())
        elif opcode == 0x9E:
            self.a = self._sub8(self.a, read_xp_d(), carry_in=1 if self.carry else 0)
        elif opcode == 0xA6:
            self.a &= read_xp_d(); self._set_logic_flags(self.a)
        elif opcode == 0xAE:
            self.a ^= read_xp_d(); self._set_logic_flags(self.a)
        elif opcode == 0xB6:
            self.a |= read_xp_d(); self._set_logic_flags(self.a)
        elif opcode == 0xBE:
            v = read_xp_d()
            self.zero = self.a == v; self.carry = self.a < v
            self.sign = ((self.a - v) & 0x80) != 0
        elif opcode == 0xCB:
            # DDCB / FDCB: bit operations on (IX+d)/(IY+d)
            d = xp_disp()
            addr = (xp + d) & 0xFFFF
            cb_op = self.read_next_byte()
            bit = (cb_op >> 3) & 0x07
            if 0x46 <= cb_op <= 0x7E and cb_op & 0x07 == 0x06:
                # BIT b,(IX+d)
                self.zero = not bool(self.memory.read_byte(addr) & (1 << bit))
                self.parity_even = self.zero
            elif 0x86 <= cb_op <= 0xBE and cb_op & 0x07 == 0x06:
                # RES b,(IX+d)
                self.memory.write_byte(addr, self.memory.read_byte(addr) & ~(1 << bit) & 0xFF)
            elif 0xC6 <= cb_op <= 0xFE and cb_op & 0x07 == 0x06:
                # SET b,(IX+d)
                self.memory.write_byte(addr, self.memory.read_byte(addr) | (1 << bit))
            elif cb_op == 0x06:
                # RLC (IX+d)
                v = self.memory.read_byte(addr)
                self.carry = bool(v & 0x80); v = ((v << 1) | (1 if self.carry else 0)) & 0xFF
                self.memory.write_byte(addr, v)
            elif cb_op == 0x0E:
                # RRC (IX+d)
                v = self.memory.read_byte(addr)
                self.carry = bool(v & 0x01); v = ((v >> 1) | (0x80 if self.carry else 0)) & 0xFF
                self.memory.write_byte(addr, v)
            elif cb_op == 0x16:
                # RL (IX+d)
                v = self.memory.read_byte(addr); cin = 1 if self.carry else 0
                self.carry = bool(v & 0x80); v = ((v << 1) | cin) & 0xFF
                self.memory.write_byte(addr, v)
            elif cb_op == 0x1E:
                # RR (IX+d)
                v = self.memory.read_byte(addr); cin = 0x80 if self.carry else 0
                self.carry = bool(v & 0x01); v = ((v >> 1) | cin) & 0xFF
                self.memory.write_byte(addr, v)
            elif cb_op == 0x26:
                # SLA (IX+d)
                v = self.memory.read_byte(addr)
                self.carry = bool(v & 0x80); v = (v << 1) & 0xFF
                self.memory.write_byte(addr, v)
            elif cb_op == 0x2E:
                # SRA (IX+d)
                v = self.memory.read_byte(addr)
                self.carry = bool(v & 0x01); v = ((v >> 1) | (v & 0x80)) & 0xFF
                self.memory.write_byte(addr, v)
            elif cb_op == 0x3E:
                # SRL (IX+d)
                v = self.memory.read_byte(addr)
                self.carry = bool(v & 0x01); v = (v >> 1) & 0xFF
                self.memory.write_byte(addr, v)
            # Other CB variants treated as NOP
        # Unknown DD/FD opcode: treat as NOP (skip the second byte already consumed)

    def _step_ed(self) -> None:
        opcode = self.read_next_byte()
        if opcode in (0x46, 0x4E, 0x66, 0x6E):
            self.interrupt_mode = 0
            return
        if opcode in (0x56, 0x76):
            self.interrupt_mode = 1
            return
        if opcode in (0x5E, 0x7E):
            self.interrupt_mode = 2
            return
        if opcode == 0x47:
            self.i = self.a
            return
        if opcode == 0x4F:
            self.r = self.a
            return
        if opcode == 0x57:
            self.a = self.i
            self.zero = self.a == 0
            self.parity_even = self._even_parity(self.a)
            self.sign = bool(self.a & 0x80)
            return
        if opcode == 0x5F:
            self.a = self.r
            self.zero = self.a == 0
            self.parity_even = self._even_parity(self.a)
            self.sign = bool(self.a & 0x80)
            return
        if opcode in (0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x78):
            self._notify_port_write((self.pc - 2) & 0xFFFF)
            value = self.ports.read_port(self.c)
            self._write_reg((opcode >> 3) & 0x07, value)
            self.zero = value == 0
            self.parity_even = self._even_parity(value)
            return
        if opcode in (0x41, 0x49, 0x51, 0x59, 0x61, 0x69, 0x79):
            self._notify_port_write((self.pc - 2) & 0xFFFF)
            self.ports.write_port(self.c, self._read_reg((opcode >> 3) & 0x07))
            return
        if opcode in (0x44, 0x4C, 0x54, 0x5C, 0x64, 0x6C, 0x74, 0x7C):
            # NEG (all mirror opcodes)
            self.carry = self.a != 0
            self.a = (-self.a) & 0xFF
            self.zero = self.a == 0
            self.sign = bool(self.a & 0x80)
            self.parity_even = self.a == 0x80
            return
        if opcode in (0x42, 0x52, 0x62, 0x72):
            # SBC HL,rr
            reg_pairs = [self.bc, self.de, self.hl, self.sp]
            rr = reg_pairs[(opcode - 0x42) >> 4]
            total = self.hl - rr - (1 if self.carry else 0)
            self.carry = total < 0
            self.hl = total & 0xFFFF
            self.zero = self.hl == 0
            self.sign = bool(self.hl & 0x8000)
            return
        if opcode in (0x4A, 0x5A, 0x6A, 0x7A):
            # ADC HL,rr
            reg_pairs = [self.bc, self.de, self.hl, self.sp]
            rr = reg_pairs[(opcode - 0x4A) >> 4]
            total = self.hl + rr + (1 if self.carry else 0)
            self.carry = total > 0xFFFF
            self.hl = total & 0xFFFF
            self.zero = self.hl == 0
            self.sign = bool(self.hl & 0x8000)
            return
        if opcode == 0x43:
            self.memory.write_word(self.read_next_word(), self.bc)
            return
        if opcode == 0x4B:
            self.bc = self.memory.read_word(self.read_next_word())
            return
        if opcode == 0x53:
            self.memory.write_word(self.read_next_word(), self.de)
            return
        if opcode == 0x5B:
            self.de = self.memory.read_word(self.read_next_word())
            return
        if opcode == 0x73:
            self.memory.write_word(self.read_next_word(), self.sp)
            return
        if opcode == 0x7B:
            self.sp = self.memory.read_word(self.read_next_word())
            return
        if opcode == 0xA0:
            self._ldi()
            return
        if opcode == 0xA1:
            self._cpi()
            return
        if opcode == 0xA8:
            self._ldd()
            return
        if opcode == 0xB0:
            while self.bc != 0:
                self._ldi()
            return
        if opcode == 0xB1:
            while self.bc != 0:
                self._cpi()
                if self.zero:
                    break
            return
        if opcode == 0xB8:
            while self.bc != 0:
                self._ldd()
            return
        # Undefined ED opcode — on real Z80, these are effectively NOP
        pass

    @staticmethod
    def _decode_relative(pc: int, value: int) -> str:
        signed = value - 0x100 if value & 0x80 else value
        target = (pc + 2 + signed) & 0xFFFF
        return f"0x{target:04X}"

    @staticmethod
    def _reg_name(code: int) -> str:
        return ("B", "C", "D", "E", "H", "L", "(HL)", "A")[code & 0x07]

    @staticmethod
    def _decode_ed(memory: Memory, pc: int) -> str:
        opcode = memory.read_byte((pc + 1) & 0xFFFF)
        low = memory.read_byte((pc + 2) & 0xFFFF)
        high = memory.read_byte((pc + 3) & 0xFFFF)
        word = low | (high << 8)
        if opcode in (0x46, 0x4E, 0x66, 0x6E):
            return "IM 0"
        if opcode == 0x47:
            return "LD I,A"
        if opcode == 0x4F:
            return "LD R,A"
        if opcode in (0x56, 0x76):
            return "IM 1"
        if opcode == 0x57:
            return "LD A,I"
        if opcode == 0x5F:
            return "LD A,R"
        if opcode in (0x5E, 0x7E):
            return "IM 2"
        if opcode == 0x44:
            return "NEG"
        if opcode in (0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x78):
            return f"IN {Z80CPU._reg_name((opcode >> 3) & 0x07)},(C)"
        if opcode in (0x41, 0x49, 0x51, 0x59, 0x61, 0x69, 0x79):
            return f"OUT (C),{Z80CPU._reg_name((opcode >> 3) & 0x07)}"
        if opcode == 0x43:
            return f"LD (0x{word:04X}),BC"
        if opcode == 0x4B:
            return f"LD BC,(0x{word:04X})"
        if opcode == 0x53:
            return f"LD (0x{word:04X}),DE"
        if opcode == 0x5B:
            return f"LD DE,(0x{word:04X})"
        if opcode == 0x73:
            return f"LD (0x{word:04X}),SP"
        if opcode == 0x7B:
            return f"LD SP,(0x{word:04X})"
        if opcode == 0xA0:
            return "LDI"
        if opcode == 0xA1:
            return "CPI"
        if opcode == 0xA8:
            return "LDD"
        if opcode == 0xB0:
            return "LDIR"
        if opcode == 0xB1:
            return "CPIR"
        if opcode == 0xB8:
            return "LDDR"
        return f"DB 0xED,0x{opcode:02X}"

    @staticmethod
    def _decode_cb(memory: Memory, pc: int) -> str:
        opcode = memory.read_byte((pc + 1) & 0xFFFF)
        reg = Z80CPU._reg_name(opcode & 0x07)
        group = opcode >> 6
        if group == 0:
            op = (opcode >> 3) & 0x07
            names = {
                0x00: "RLC",
                0x01: "RRC",
                0x02: "RL",
                0x03: "RR",
                0x04: "SLA",
                0x05: "SRA",
                0x07: "SRL",
            }
            if op in names:
                return f"{names[op]} {reg}"
        if group == 1:
            return f"BIT {(opcode >> 3) & 0x07},{reg}"
        return f"DB 0xCB,0x{opcode:02X}"

    def _ldi(self) -> None:
        self.memory.write_byte(self.de, self.memory.read_byte(self.hl))
        self.hl = (self.hl + 1) & 0xFFFF
        self.de = (self.de + 1) & 0xFFFF
        self.bc = (self.bc - 1) & 0xFFFF
        self.zero = self.bc == 0
        self.parity_even = self.bc != 0

    def _ldd(self) -> None:
        self.memory.write_byte(self.de, self.memory.read_byte(self.hl))
        self.hl = (self.hl - 1) & 0xFFFF
        self.de = (self.de - 1) & 0xFFFF
        self.bc = (self.bc - 1) & 0xFFFF
        self.zero = self.bc == 0
        self.parity_even = self.bc != 0

    def _cpi(self) -> None:
        result = (self.a - self.memory.read_byte(self.hl)) & 0xFF
        self.hl = (self.hl + 1) & 0xFFFF
        self.bc = (self.bc - 1) & 0xFFFF
        self.zero = result == 0
        self.parity_even = self.bc != 0
