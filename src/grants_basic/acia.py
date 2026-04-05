from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque


STATUS_RDRF = 0x01
STATUS_TDRE = 0x02
STATUS_DCD = 0x04
STATUS_CTS = 0x08
STATUS_FE = 0x10
STATUS_OVRN = 0x20
STATUS_PE = 0x40
STATUS_IRQ = 0x80


@dataclass(slots=True)
class Acia6850:
    rx_queue: Deque[int] = field(default_factory=deque)
    tx_bytes: bytearray = field(default_factory=bytearray)
    control: int = 0x00
    receive_irq_enabled: bool = False
    transmit_irq_enabled: bool = False
    overrun: bool = False

    def reset(self) -> None:
        self.rx_queue.clear()
        self.tx_bytes.clear()
        self.control = 0x00
        self.receive_irq_enabled = False
        self.transmit_irq_enabled = False
        self.overrun = False

    @property
    def irq_asserted(self) -> bool:
        if self.receive_irq_enabled and self.rx_queue:
            return True
        if self.transmit_irq_enabled:
            return True
        return False

    def write_control(self, value: int) -> None:
        value &= 0xFF
        if (value & 0x03) == 0x03:
            self.reset()
            return
        self.control = value
        self.receive_irq_enabled = bool(value & 0x80)
        self.transmit_irq_enabled = ((value >> 5) & 0x03) == 0x01

    def write_data(self, value: int) -> None:
        self.tx_bytes.append(value & 0xFF)

    def read_status(self) -> int:
        status = STATUS_TDRE
        if self.rx_queue:
            status |= STATUS_RDRF
        if self.overrun:
            status |= STATUS_OVRN
        if self.irq_asserted:
            status |= STATUS_IRQ
        return status

    def read_data(self) -> int:
        if not self.rx_queue:
            return 0x00
        value = self.rx_queue.popleft()
        if not self.rx_queue:
            self.overrun = False
        return value

    def receive_byte(self, value: int) -> None:
        if self.rx_queue:
            self.overrun = True
        self.rx_queue.append(value & 0xFF)

    def receive_text(self, text: str) -> None:
        for value in text.encode("latin-1", errors="replace"):
            self.receive_byte(value)

    def consume_tx_text(self) -> str:
        if not self.tx_bytes:
            return ""
        data = bytes(self.tx_bytes)
        self.tx_bytes.clear()
        return data.decode("latin-1", errors="replace")
