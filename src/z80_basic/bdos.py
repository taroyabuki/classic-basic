from __future__ import annotations

from dataclasses import dataclass

from .cpm import CPMEnvironment


class UnsupportedBdosCall(RuntimeError):
    pass


@dataclass(slots=True)
class CPMBdos:
    environment: CPMEnvironment
    console: object

    def handle_call(self, cpu: "Z80CPU") -> str | None:
        function = cpu.c

        if function == 0:
            return "warm_boot"
        if function == 1:
            value = self.console.read_byte()
            self.console.write_byte(value)
            cpu.a = value
            cpu.pc = cpu.pop_word()
            return None
        if function == 2:
            self.console.write_byte(cpu.e)
            cpu.pc = cpu.pop_word()
            return None
        if function == 6:
            if cpu.e == 0xFF:
                cpu.a = self.console.read_byte() if self.console.input_ready() else 0x00
            else:
                self.console.write_byte(cpu.e)
                cpu.a = cpu.e
            cpu.pc = cpu.pop_word()
            return None
        if function == 9:
            address = cpu.de
            while True:
                value = self.environment.memory.read_byte(address)
                if value == ord("$"):
                    break
                self.console.write_byte(value)
                address = (address + 1) & 0xFFFF
            cpu.pc = cpu.pop_word()
            return None
        if function == 11:
            cpu.a = 0xFF if self.console.input_ready() else 0x00
            cpu.pc = cpu.pop_word()
            return None

        raise UnsupportedBdosCall(f"unsupported BDOS function: {function}")


from .cpu import Z80CPU
