from __future__ import annotations

import sys
from dataclasses import dataclass

from .memory import Memory


_EVEN_PARITY = tuple(((value & 0xFF).bit_count() % 2) == 0 for value in range(256))
_DATACLASS_SLOTS: dict[str, bool] = {"slots": True} if sys.version_info >= (3, 10) else {}


class UnsupportedInstruction(RuntimeError):
    pass


@dataclass(**_DATACLASS_SLOTS)
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
    __slots__ = (
        "memory",
        "_mem_data",
        "_mem_rom_mask",
        "ports",
        "a",
        "b",
        "c",
        "d",
        "e",
        "h",
        "l",
        "alt_a",
        "alt_b",
        "alt_c",
        "alt_d",
        "alt_e",
        "alt_h",
        "alt_l",
        "pc",
        "sp",
        "ix",
        "iy",
        "i",
        "r",
        "interrupt_mode",
        "iff1",
        "iff2",
        "halted",
        "zero",
        "carry",
        "parity_even",
        "sign",
        "alt_zero",
        "alt_carry",
        "alt_parity_even",
        "alt_sign",
        "trace",
        "_ei_pending",
    )

    def __init__(self, memory: Memory, entry_point: int = 0x0000, ports: PortDevice | None = None) -> None:
        self.memory = memory
        self._mem_data = memory._data
        self._mem_rom_mask = memory._rom_mask
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
        self.ix = 0
        self.iy = 0
        self.i = 0
        self.r = 0
        self.interrupt_mode = 0
        self.iff1 = False
        self.iff2 = False
        self.halted = False
        self.zero = False
        self.carry = False
        self.parity_even = False
        self.sign = False
        self.alt_zero = False
        self.alt_carry = False
        self.alt_parity_even = False
        self.alt_sign = False
        self.trace = False
        self._ei_pending = False

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

    def reset(self, entry_point: int = 0x0000) -> None:
        self.__init__(self.memory, entry_point=entry_point, ports=self.ports)

    def _read_byte(self, address: int) -> int:
        return self._mem_data[address & 0xFFFF]

    def _write_byte(self, address: int, value: int) -> None:
        index = address & 0xFFFF
        if self._mem_rom_mask[index]:
            return
        self._mem_data[index] = value & 0xFF

    def _read_word(self, address: int) -> int:
        index = address & 0xFFFF
        next_index = (index + 1) & 0xFFFF
        return self._mem_data[index] | (self._mem_data[next_index] << 8)

    def _write_word(self, address: int, value: int) -> None:
        self._write_byte(address, value)
        self._write_byte(address + 1, value >> 8)

    def read_next_byte(self) -> int:
        value = self._mem_data[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        self.r = (self.r + 1) & 0x7F
        return value

    def read_next_word(self) -> int:
        low = self.read_next_byte()
        high = self.read_next_byte()
        return low | (high << 8)

    def push_word(self, value: int) -> None:
        self.sp = (self.sp - 1) & 0xFFFF
        self._write_byte(self.sp, (value >> 8) & 0xFF)
        self.sp = (self.sp - 1) & 0xFFFF
        self._write_byte(self.sp, value & 0xFF)

    def pop_word(self) -> int:
        low = self._read_byte(self.sp)
        self.sp = (self.sp + 1) & 0xFFFF
        high = self._read_byte(self.sp)
        self.sp = (self.sp + 1) & 0xFFFF
        return low | (high << 8)

    def service_interrupt(self) -> None:
        self.halted = False
        self.iff1 = False
        self.iff2 = False
        if self.interrupt_mode == 2:
            vector = ((self.i << 8) | 0xFF) & 0xFFFF
            self.push_word(self.pc)
            self.pc = self._read_word(vector)
            return
        self.push_word(self.pc)
        self.pc = 0x0038

    def step(self) -> None:
        if self.trace:
            self._trace_state()
        activate_ei = self._ei_pending
        opcode = self._mem_data[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        self.r = (self.r + 1) & 0x7F

        if opcode == 0x00:
            pass
        elif opcode == 0x01:
            self.bc = self.read_next_word()
        elif opcode == 0x02:
            self._write_byte(self.bc, self.a)
        elif opcode == 0x03:
            self.bc = (self.bc + 1) & 0xFFFF
        elif opcode == 0x04:
            self.b = self._inc8(self.b)
        elif opcode == 0x05:
            self.b = self._dec8(self.b)
        elif opcode == 0x06:
            self.b = self.read_next_byte()
        elif opcode == 0x07:
            self.carry = bool(self.a & 0x80)
            self.a = ((self.a << 1) | (self.a >> 7)) & 0xFF
        elif opcode == 0x08:
            self.a, self.alt_a = self.alt_a, self.a
            self.zero, self.alt_zero = self.alt_zero, self.zero
            self.carry, self.alt_carry = self.alt_carry, self.carry
            self.parity_even, self.alt_parity_even = self.alt_parity_even, self.parity_even
            self.sign, self.alt_sign = self.alt_sign, self.sign
        elif opcode == 0x09:
            self.hl = self._add16(self.hl, self.bc)
        elif opcode == 0x0A:
            self.a = self._read_byte(self.bc)
        elif opcode == 0x0B:
            self.bc = (self.bc - 1) & 0xFFFF
        elif opcode == 0x0C:
            self.c = self._inc8(self.c)
        elif opcode == 0x0D:
            self.c = self._dec8(self.c)
        elif opcode == 0x0E:
            self.c = self.read_next_byte()
        elif opcode == 0x0F:
            self.carry = bool(self.a & 0x01)
            self.a = ((self.a >> 1) | ((self.a & 0x01) << 7)) & 0xFF
        elif opcode == 0x10:
            displacement = self._read_signed_byte()
            self.b = (self.b - 1) & 0xFF
            if self.b != 0:
                self.pc = (self.pc + displacement) & 0xFFFF
        elif opcode == 0x11:
            self.de = self.read_next_word()
        elif opcode == 0x12:
            self._write_byte(self.de, self.a)
        elif opcode == 0x13:
            self.de = (self.de + 1) & 0xFFFF
        elif opcode == 0x14:
            self.d = self._inc8(self.d)
        elif opcode == 0x15:
            self.d = self._dec8(self.d)
        elif opcode == 0x16:
            self.d = self.read_next_byte()
        elif opcode == 0x17:
            carry_in = 1 if self.carry else 0
            self.carry = bool(self.a & 0x80)
            self.a = ((self.a << 1) | carry_in) & 0xFF
        elif opcode == 0x18:
            self.pc = (self.pc + self._read_signed_byte()) & 0xFFFF
        elif opcode == 0x19:
            self.hl = self._add16(self.hl, self.de)
        elif opcode == 0x1A:
            self.a = self._read_byte(self.de)
        elif opcode == 0x1B:
            self.de = (self.de - 1) & 0xFFFF
        elif opcode == 0x1C:
            self.e = self._inc8(self.e)
        elif opcode == 0x1D:
            self.e = self._dec8(self.e)
        elif opcode == 0x1E:
            self.e = self.read_next_byte()
        elif opcode == 0x1F:
            carry_in = 0x80 if self.carry else 0
            self.carry = bool(self.a & 0x01)
            self.a = ((self.a >> 1) | carry_in) & 0xFF
        elif opcode == 0x20:
            displacement = self._read_signed_byte()
            if not self.zero:
                self.pc = (self.pc + displacement) & 0xFFFF
        elif opcode == 0x21:
            self.hl = self.read_next_word()
        elif opcode == 0x22:
            self._write_word(self.read_next_word(), self.hl)
        elif opcode == 0x23:
            self.hl = (self.hl + 1) & 0xFFFF
        elif opcode == 0x24:
            self.h = self._inc8(self.h)
        elif opcode == 0x25:
            self.h = self._dec8(self.h)
        elif opcode == 0x26:
            self.h = self.read_next_byte()
        elif opcode == 0x27:
            self._daa()
        elif opcode == 0x28:
            displacement = self._read_signed_byte()
            if self.zero:
                self.pc = (self.pc + displacement) & 0xFFFF
        elif opcode == 0x29:
            self.hl = self._add16(self.hl, self.hl)
        elif opcode == 0x2A:
            self.hl = self._read_word(self.read_next_word())
        elif opcode == 0x2B:
            self.hl = (self.hl - 1) & 0xFFFF
        elif opcode == 0x2C:
            self.l = self._inc8(self.l)
        elif opcode == 0x2D:
            self.l = self._dec8(self.l)
        elif opcode == 0x2E:
            self.l = self.read_next_byte()
        elif opcode == 0x2F:
            self.a ^= 0xFF
            self.zero = self.a == 0
        elif opcode == 0x30:
            displacement = self._read_signed_byte()
            if not self.carry:
                self.pc = (self.pc + displacement) & 0xFFFF
        elif opcode == 0x31:
            self.sp = self.read_next_word()
        elif opcode == 0x32:
            self._write_byte(self.read_next_word(), self.a)
        elif opcode == 0x33:
            self.sp = (self.sp + 1) & 0xFFFF
        elif opcode == 0x34:
            self._write_byte(self.hl, self._inc8(self._read_byte(self.hl)))
        elif opcode == 0x35:
            self._write_byte(self.hl, self._dec8(self._read_byte(self.hl)))
        elif opcode == 0x36:
            self._write_byte(self.hl, self.read_next_byte())
        elif opcode == 0x37:
            self.carry = True
        elif opcode == 0x38:
            displacement = self._read_signed_byte()
            if self.carry:
                self.pc = (self.pc + displacement) & 0xFFFF
        elif opcode == 0x39:
            self.hl = self._add16(self.hl, self.sp)
        elif opcode == 0x3A:
            self.a = self._read_byte(self.read_next_word())
        elif opcode == 0x3B:
            self.sp = (self.sp - 1) & 0xFFFF
        elif opcode == 0x3C:
            self.a = self._inc8(self.a)
        elif opcode == 0x3D:
            self.a = self._dec8(self.a)
        elif opcode == 0x3E:
            self.a = self.read_next_byte()
        elif opcode == 0x3F:
            self.carry = not self.carry
        elif 0x40 <= opcode <= 0x7F and opcode != 0x76:
            self._write_reg((opcode >> 3) & 0x07, self._read_reg(opcode & 0x07))
        elif opcode == 0x76:
            self.halted = True
        elif 0x80 <= opcode <= 0x87:
            self.a = self._add8(self.a, self._read_reg(opcode & 0x07))
        elif 0x88 <= opcode <= 0x8F:
            self.a = self._add8(self.a, self._read_reg(opcode & 0x07), carry_in=1 if self.carry else 0)
        elif 0x90 <= opcode <= 0x97:
            self.a = self._sub8(self.a, self._read_reg(opcode & 0x07))
        elif 0x98 <= opcode <= 0x9F:
            self.a = self._sub8(self.a, self._read_reg(opcode & 0x07), carry_in=1 if self.carry else 0)
        elif 0xA0 <= opcode <= 0xA7:
            self.a &= self._read_reg(opcode & 0x07)
            self._set_logic_flags(self.a)
        elif 0xA8 <= opcode <= 0xAF:
            self.a ^= self._read_reg(opcode & 0x07)
            self._set_logic_flags(self.a)
        elif 0xB0 <= opcode <= 0xB7:
            self.a |= self._read_reg(opcode & 0x07)
            self._set_logic_flags(self.a)
        elif 0xB8 <= opcode <= 0xBF:
            value = self._read_reg(opcode & 0x07)
            self.zero = self.a == value
            self.carry = self.a < value
            self.sign = ((self.a - value) & 0x80) != 0
            self.parity_even = self.zero
        elif opcode == 0xC0:
            if not self.zero:
                self.pc = self.pop_word()
        elif opcode == 0xC1:
            self.bc = self.pop_word()
        elif opcode == 0xC2:
            address = self.read_next_word()
            if not self.zero:
                self.pc = address
        elif opcode == 0xC3:
            self.pc = self.read_next_word()
        elif opcode == 0xC4:
            address = self.read_next_word()
            if not self.zero:
                self.push_word(self.pc)
                self.pc = address
        elif opcode == 0xC5:
            self.push_word(self.bc)
        elif opcode == 0xC6:
            self.a = self._add8(self.a, self.read_next_byte())
        elif opcode == 0xC7:
            self.push_word(self.pc)
            self.pc = 0x0000
        elif opcode == 0xC8:
            if self.zero:
                self.pc = self.pop_word()
        elif opcode == 0xC9:
            self.pc = self.pop_word()
        elif opcode == 0xCA:
            address = self.read_next_word()
            if self.zero:
                self.pc = address
        elif opcode == 0xCB:
            self._step_cb()
        elif opcode == 0xCC:
            address = self.read_next_word()
            if self.zero:
                self.push_word(self.pc)
                self.pc = address
        elif opcode == 0xCD:
            destination = self.read_next_word()
            self.push_word(self.pc)
            self.pc = destination
        elif opcode == 0xCE:
            self.a = self._add8(self.a, self.read_next_byte(), carry_in=1 if self.carry else 0)
        elif opcode == 0xCF:
            self.push_word(self.pc)
            self.pc = 0x0008
        elif opcode == 0xD0:
            if not self.carry:
                self.pc = self.pop_word()
        elif opcode == 0xD1:
            self.de = self.pop_word()
        elif opcode == 0xD2:
            address = self.read_next_word()
            if not self.carry:
                self.pc = address
        elif opcode == 0xD3:
            self.ports.write_port(self.read_next_byte(), self.a)
        elif opcode == 0xD4:
            address = self.read_next_word()
            if not self.carry:
                self.push_word(self.pc)
                self.pc = address
        elif opcode == 0xD5:
            self.push_word(self.de)
        elif opcode == 0xD6:
            self.a = self._sub8(self.a, self.read_next_byte())
        elif opcode == 0xD7:
            self.push_word(self.pc)
            self.pc = 0x0010
        elif opcode == 0xD8:
            if self.carry:
                self.pc = self.pop_word()
        elif opcode == 0xD9:
            self.b, self.alt_b = self.alt_b, self.b
            self.c, self.alt_c = self.alt_c, self.c
            self.d, self.alt_d = self.alt_d, self.d
            self.e, self.alt_e = self.alt_e, self.e
            self.h, self.alt_h = self.alt_h, self.h
            self.l, self.alt_l = self.alt_l, self.l
        elif opcode == 0xDA:
            address = self.read_next_word()
            if self.carry:
                self.pc = address
        elif opcode == 0xDB:
            self.a = self.ports.read_port(self.read_next_byte())
            self.zero = self.a == 0
        elif opcode == 0xDC:
            address = self.read_next_word()
            if self.carry:
                self.push_word(self.pc)
                self.pc = address
        elif opcode == 0xDD:
            self._step_xp(is_iy=False)
        elif opcode == 0xDE:
            self.a = self._sub8(self.a, self.read_next_byte(), carry_in=1 if self.carry else 0)
        elif opcode == 0xDF:
            self.push_word(self.pc)
            self.pc = 0x0018
        elif opcode == 0xE0:
            if not self.parity_even:
                self.pc = self.pop_word()
        elif opcode == 0xE1:
            self.hl = self.pop_word()
        elif opcode == 0xE2:
            address = self.read_next_word()
            if not self.parity_even:
                self.pc = address
        elif opcode == 0xE3:
            value = self._read_word(self.sp)
            self._write_word(self.sp, self.hl)
            self.hl = value
        elif opcode == 0xE4:
            address = self.read_next_word()
            if not self.parity_even:
                self.push_word(self.pc)
                self.pc = address
        elif opcode == 0xE5:
            self.push_word(self.hl)
        elif opcode == 0xE6:
            self.a &= self.read_next_byte()
            self._set_logic_flags(self.a)
        elif opcode == 0xE7:
            self.push_word(self.pc)
            self.pc = 0x0020
        elif opcode == 0xE8:
            if self.parity_even:
                self.pc = self.pop_word()
        elif opcode == 0xE9:
            self.pc = self.hl
        elif opcode == 0xEA:
            address = self.read_next_word()
            if self.parity_even:
                self.pc = address
        elif opcode == 0xEB:
            self.de, self.hl = self.hl, self.de
        elif opcode == 0xEC:
            address = self.read_next_word()
            if self.parity_even:
                self.push_word(self.pc)
                self.pc = address
        elif opcode == 0xED:
            self._step_ed()
        elif opcode == 0xEE:
            self.a ^= self.read_next_byte()
            self._set_logic_flags(self.a)
        elif opcode == 0xEF:
            self.push_word(self.pc)
            self.pc = 0x0028
        elif opcode == 0xF0:
            if not self.sign:
                self.pc = self.pop_word()
        elif opcode == 0xF1:
            value = self.pop_word()
            self.a = (value >> 8) & 0xFF
            self.sign = bool(value & 0x80)
            self.zero = bool(value & 0x40)
            self.parity_even = bool(value & 0x04)
            self.carry = bool(value & 0x01)
        elif opcode == 0xF2:
            address = self.read_next_word()
            if not self.sign:
                self.pc = address
        elif opcode == 0xF3:
            self.iff1 = False
            self.iff2 = False
            self._ei_pending = False
        elif opcode == 0xF4:
            address = self.read_next_word()
            if not self.sign:
                self.push_word(self.pc)
                self.pc = address
        elif opcode == 0xF5:
            self.push_word(
                (self.a << 8)
                | (0x80 if self.sign else 0x00)
                | (0x40 if self.zero else 0x00)
                | (0x04 if self.parity_even else 0x00)
                | (0x01 if self.carry else 0x00)
            )
        elif opcode == 0xF6:
            self.a |= self.read_next_byte()
            self._set_logic_flags(self.a)
        elif opcode == 0xF7:
            self.push_word(self.pc)
            self.pc = 0x0030
        elif opcode == 0xF8:
            if self.sign:
                self.pc = self.pop_word()
        elif opcode == 0xF9:
            self.sp = self.hl
        elif opcode == 0xFA:
            address = self.read_next_word()
            if self.sign:
                self.pc = address
        elif opcode == 0xFB:
            self._ei_pending = True
        elif opcode == 0xFC:
            address = self.read_next_word()
            if self.sign:
                self.push_word(self.pc)
                self.pc = address
        elif opcode == 0xFD:
            self._step_xp(is_iy=True)
        elif opcode == 0xFE:
            value = self.read_next_byte()
            self.zero = self.a == value
            self.carry = self.a < value
            self.sign = ((self.a - value) & 0x80) != 0
        elif opcode == 0xFF:
            self.push_word(self.pc)
            self.pc = 0x0038
        else:
            raise UnsupportedInstruction(f"unsupported opcode 0x{opcode:02X} at 0x{(self.pc - 1) & 0xFFFF:04X}")

        if activate_ei:
            self.iff1 = True
            self.iff2 = True
            self._ei_pending = False

    def _read_signed_byte(self) -> int:
        value = self._mem_data[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        self.r = (self.r + 1) & 0x7F
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
        return _EVEN_PARITY[value & 0xFF]

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
            return self._read_byte(self.hl)
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
            self._write_byte(self.hl, value)
            return
        self.a = value

    def _trace_state(self) -> None:
        print(
            f"PC={self.pc:04X} SP={self.sp:04X} A={self.a:02X} "
            f"BC={self.bc:04X} DE={self.de:04X} HL={self.hl:04X} "
            f"IX={self.ix:04X} IY={self.iy:04X} IFF1={int(self.iff1)}"
        )

    def _daa(self) -> None:
        adjust = 0
        if self.carry or self.a > 0x99:
            adjust |= 0x60
            self.carry = True
        if (self.a & 0x0F) > 0x09:
            adjust |= 0x06
        self.a = (self.a + adjust) & 0xFF
        self.zero = self.a == 0
        self.sign = bool(self.a & 0x80)
        self.parity_even = self._even_parity(self.a)

    def _step_cb(self) -> None:
        opcode = self._mem_data[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        self.r = (self.r + 1) & 0x7F
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
                raise UnsupportedInstruction(f"unsupported CB opcode 0x{opcode:02X}")
            self._write_reg(reg, result)
            self.zero = result == 0
            self.parity_even = self._even_parity(result)
            return
        if group == 1:
            bit = (opcode >> 3) & 0x07
            self.zero = (value & (1 << bit)) == 0
            self.parity_even = self.zero
            return
        if group == 2:
            bit = (opcode >> 3) & 0x07
            self._write_reg(reg, value & ~(1 << bit))
            return
        if group == 3:
            bit = (opcode >> 3) & 0x07
            self._write_reg(reg, value | (1 << bit))
            return
        raise UnsupportedInstruction(f"unsupported CB opcode 0x{opcode:02X}")

    def _step_xp(self, is_iy: bool) -> None:
        xp = self.iy if is_iy else self.ix
        opcode = self._mem_data[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        self.r = (self.r + 1) & 0x7F

        def xp_disp() -> int:
            displacement = self.read_next_byte()
            return displacement - 256 if displacement >= 128 else displacement

        def set_xp(value: int) -> None:
            value &= 0xFFFF
            if is_iy:
                self.iy = value
            else:
                self.ix = value

        def read_xp_d() -> int:
            return self._read_byte((xp + xp_disp()) & 0xFFFF)

        def write_xp_d(displacement: int, value: int) -> None:
            self._write_byte((xp + displacement) & 0xFFFF, value & 0xFF)

        if opcode == 0x21:
            set_xp(self.read_next_word())
        elif opcode == 0x22:
            self._write_word(self.read_next_word(), xp)
        elif opcode == 0x2A:
            set_xp(self._read_word(self.read_next_word()))
        elif opcode == 0x23:
            set_xp(xp + 1)
        elif opcode == 0x2B:
            set_xp(xp - 1)
        elif opcode == 0x09:
            total = xp + self.bc
            self.carry = total > 0xFFFF
            set_xp(total)
        elif opcode == 0x19:
            total = xp + self.de
            self.carry = total > 0xFFFF
            set_xp(total)
        elif opcode == 0x29:
            total = xp + xp
            self.carry = total > 0xFFFF
            set_xp(total)
        elif opcode == 0x39:
            total = xp + self.sp
            self.carry = total > 0xFFFF
            set_xp(total)
        elif opcode == 0xE1:
            set_xp(self.pop_word())
        elif opcode == 0xE5:
            self.push_word(xp)
        elif opcode == 0xE3:
            tmp = self._read_word(self.sp)
            self._write_word(self.sp, xp)
            set_xp(tmp)
        elif opcode == 0xE9:
            self.pc = xp
        elif opcode == 0xF9:
            self.sp = xp
        elif opcode == 0x34:
            displacement = xp_disp()
            addr = (xp + displacement) & 0xFFFF
            self._write_byte(addr, self._inc8(self._read_byte(addr)))
        elif opcode == 0x35:
            displacement = xp_disp()
            addr = (xp + displacement) & 0xFFFF
            self._write_byte(addr, self._dec8(self._read_byte(addr)))
        elif opcode == 0x36:
            displacement = xp_disp()
            self._write_byte((xp + displacement) & 0xFFFF, self.read_next_byte())
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
        elif opcode == 0x70:
            write_xp_d(xp_disp(), self.b)
        elif opcode == 0x71:
            write_xp_d(xp_disp(), self.c)
        elif opcode == 0x72:
            write_xp_d(xp_disp(), self.d)
        elif opcode == 0x73:
            write_xp_d(xp_disp(), self.e)
        elif opcode == 0x74:
            write_xp_d(xp_disp(), self.h)
        elif opcode == 0x75:
            write_xp_d(xp_disp(), self.l)
        elif opcode == 0x77:
            write_xp_d(xp_disp(), self.a)
        elif opcode == 0x7E:
            self.a = read_xp_d()
        elif opcode == 0x86:
            self.a = self._add8(self.a, read_xp_d())
        elif opcode == 0x8E:
            self.a = self._add8(self.a, read_xp_d(), carry_in=1 if self.carry else 0)
        elif opcode == 0x96:
            self.a = self._sub8(self.a, read_xp_d())
        elif opcode == 0x9E:
            self.a = self._sub8(self.a, read_xp_d(), carry_in=1 if self.carry else 0)
        elif opcode == 0xA6:
            self.a &= read_xp_d()
            self._set_logic_flags(self.a)
        elif opcode == 0xAE:
            self.a ^= read_xp_d()
            self._set_logic_flags(self.a)
        elif opcode == 0xB6:
            self.a |= read_xp_d()
            self._set_logic_flags(self.a)
        elif opcode == 0xBE:
            value = read_xp_d()
            self.zero = self.a == value
            self.carry = self.a < value
            self.sign = ((self.a - value) & 0x80) != 0

    def _step_ed(self) -> None:
        opcode = self._mem_data[self.pc]
        self.pc = (self.pc + 1) & 0xFFFF
        self.r = (self.r + 1) & 0x7F
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
        if opcode in (0x45, 0x55, 0x65, 0x75, 0x4D, 0x5D, 0x6D, 0x7D):
            self.iff1 = self.iff2
            self.pc = self.pop_word()
            return
        if opcode in (0x40, 0x48, 0x50, 0x58, 0x60, 0x68, 0x78):
            value = self.ports.read_port(self.c)
            self._write_reg((opcode >> 3) & 0x07, value)
            self.zero = value == 0
            self.parity_even = self._even_parity(value)
            return
        if opcode in (0x41, 0x49, 0x51, 0x59, 0x61, 0x69, 0x79):
            self.ports.write_port(self.c, self._read_reg((opcode >> 3) & 0x07))
            return
        if opcode in (0x44, 0x4C, 0x54, 0x5C, 0x64, 0x6C, 0x74, 0x7C):
            self.carry = self.a != 0
            self.a = (-self.a) & 0xFF
            self.zero = self.a == 0
            self.sign = bool(self.a & 0x80)
            self.parity_even = self.a == 0x80
            return
        if opcode in (0x42, 0x52, 0x62, 0x72):
            reg_pairs = [self.bc, self.de, self.hl, self.sp]
            rr = reg_pairs[(opcode - 0x42) >> 4]
            total = self.hl - rr - (1 if self.carry else 0)
            self.carry = total < 0
            self.hl = total & 0xFFFF
            self.zero = self.hl == 0
            self.sign = bool(self.hl & 0x8000)
            return
        if opcode in (0x4A, 0x5A, 0x6A, 0x7A):
            reg_pairs = [self.bc, self.de, self.hl, self.sp]
            rr = reg_pairs[(opcode - 0x4A) >> 4]
            total = self.hl + rr + (1 if self.carry else 0)
            self.carry = total > 0xFFFF
            self.hl = total & 0xFFFF
            self.zero = self.hl == 0
            self.sign = bool(self.hl & 0x8000)
            return
        if opcode == 0x43:
            self._write_word(self.read_next_word(), self.bc)
            return
        if opcode == 0x4B:
            self.bc = self._read_word(self.read_next_word())
            return
        if opcode == 0x53:
            self._write_word(self.read_next_word(), self.de)
            return
        if opcode == 0x5B:
            self.de = self._read_word(self.read_next_word())
            return
        if opcode == 0x73:
            self._write_word(self.read_next_word(), self.sp)
            return
        if opcode == 0x7B:
            self.sp = self._read_word(self.read_next_word())
            return
        if opcode == 0xA0:
            self._ldi()
            return
        if opcode == 0xA1:
            self._cpi()
            return
        if opcode == 0xA2:
            self._ini()
            return
        if opcode == 0xA3:
            self._outi()
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
        if opcode == 0xB2:
            while self.b != 0:
                self._ini()
            return
        if opcode == 0xB3:
            while self.b != 0:
                self._outi()
            return
        if opcode == 0xB8:
            while self.bc != 0:
                self._ldd()
            return

    def _ldi(self) -> None:
        self._write_byte(self.de, self._read_byte(self.hl))
        self.hl = (self.hl + 1) & 0xFFFF
        self.de = (self.de + 1) & 0xFFFF
        self.bc = (self.bc - 1) & 0xFFFF

    def _ldd(self) -> None:
        self._write_byte(self.de, self._read_byte(self.hl))
        self.hl = (self.hl - 1) & 0xFFFF
        self.de = (self.de - 1) & 0xFFFF
        self.bc = (self.bc - 1) & 0xFFFF

    def _cpi(self) -> None:
        value = self._read_byte(self.hl)
        self.hl = (self.hl + 1) & 0xFFFF
        self.bc = (self.bc - 1) & 0xFFFF
        self.zero = self.a == value

    def _ini(self) -> None:
        value = self.ports.read_port(self.c)
        self._write_byte(self.hl, value)
        self.hl = (self.hl + 1) & 0xFFFF
        self.b = (self.b - 1) & 0xFF
        self.zero = self.b == 0

    def _outi(self) -> None:
        value = self._read_byte(self.hl)
        self.ports.write_port(self.c, value)
        self.hl = (self.hl + 1) & 0xFFFF
        self.b = (self.b - 1) & 0xFF
        self.zero = self.b == 0
