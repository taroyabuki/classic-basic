from __future__ import annotations

from collections import deque
from dataclasses import field

from dataclass_compat import dataclass
from typing import Deque

from z80_basic.cpu import PortDevice

from .memory import PC8001Memory


KEYBOARD_STATUS_PORT = 0x20
KEYBOARD_DATA_PORT = 0x21
DISPLAY_DATA_PORT = 0x10
CRT_STATUS_PORT = 0x09
SYSTEM_STATUS_PORT = 0x40
SYSTEM_CONTROL_PORT = 0x51
SYSTEM_INPUT_PORT = 0xFE
ALT_SYSTEM_INPUT_PORT = 0xFA
ALT_SYSTEM_CONTROL_PORT = 0xFB
ALT_SYSTEM_DATA_PORT = 0xF8
ALT_SYSTEM_COMMAND_PORT = 0xF9

PORT_NAMES = {
    KEYBOARD_STATUS_PORT: "KBD_STATUS",
    KEYBOARD_DATA_PORT: "KBD_DATA",
    DISPLAY_DATA_PORT: "DISPLAY_DATA",
    CRT_STATUS_PORT: "CRT_STATUS",
    SYSTEM_STATUS_PORT: "SYSTEM_STATUS",
    SYSTEM_CONTROL_PORT: "SYSTEM_CONTROL",
    SYSTEM_INPUT_PORT: "SYSTEM_INPUT",
    ALT_SYSTEM_DATA_PORT: "ALT_SYSTEM_DATA",
    ALT_SYSTEM_COMMAND_PORT: "ALT_SYSTEM_COMMAND",
    ALT_SYSTEM_INPUT_PORT: "ALT_SYSTEM_INPUT",
    ALT_SYSTEM_CONTROL_PORT: "ALT_SYSTEM_CONTROL",
    0x30: "TEXT_ATTR?",
    0x50: "CRTC_DATA?",
    0x64: "SUBSYS_CMD?",
    0x65: "SUBSYS_DATA?",
    0x68: "SUBSYS_MODE?",
}


@dataclass(slots=True)
class PC8001Ports(PortDevice):
    memory: PC8001Memory
    cursor_row: int = 0
    cursor_col: int = 0
    keyboard: Deque[int] = field(default_factory=deque)
    system_status_script: Deque[int] = field(default_factory=deque)
    system_status_value: int = 0
    system_status_toggle: bool = True
    crt_status_value: int = 0x7F
    system_input_value: int = 0x06
    system_input_script: Deque[int] = field(default_factory=deque)
    alt_input_ready: bool = False
    alt_command_phase: str = "idle"
    alt_command_value: int = 0
    port_log_limit: int = 0
    port_log: Deque[str] = field(default_factory=deque)
    current_pc: int = 0
    port_summary: dict[tuple[str, int, int], int] = field(default_factory=dict)

    def queue_key(self, value: int) -> None:
        self.keyboard.append(value & 0xFF)

    def has_queued_key(self) -> bool:
        return bool(self.keyboard)

    def pop_queued_key(self) -> int:
        if not self.keyboard:
            return 0
        return self.keyboard.popleft()

    def consume_line(self) -> list[int] | None:
        values = list(self.keyboard)
        for index, value in enumerate(values):
            if value not in (0x0A, 0x0D):
                continue
            line = values[:index]
            count = index + 1
            if count < len(values) and values[count] in (0x0A, 0x0D) and values[count] != value:
                count += 1
            for _ in range(count):
                self.keyboard.popleft()
            return line
        return None

    def set_current_pc(self, pc: int) -> None:
        self.current_pc = pc & 0xFFFF

    def pulse_system_input(self, value: int, *, repeat: int = 1, fallback: int | None = None) -> None:
        for _ in range(max(0, repeat)):
            self.system_input_script.append(value & 0xFF)
        if fallback is not None:
            self.system_input_value = fallback & 0xFF

    def read_port(self, port: int) -> int:
        port &= 0xFF
        if port == KEYBOARD_STATUS_PORT:
            value = 1 if self.keyboard else 0
            self._record_port_event("IN", port, value)
            return value
        if port == KEYBOARD_DATA_PORT:
            if self.keyboard:
                value = self.pop_queued_key()
                self._record_port_event("IN", port, value)
                return value
            self._record_port_event("IN", port, 0)
            return 0
        if port == SYSTEM_STATUS_PORT:
            if self.system_status_script:
                self.system_status_value = self.system_status_script.popleft()
            else:
                self.system_status_toggle = not self.system_status_toggle
                self.system_status_value = 0x20 if self.system_status_toggle else 0x00
            self._record_port_event("IN", port, self.system_status_value)
            return self.system_status_value
        if port == CRT_STATUS_PORT:
            value = self.crt_status_value
            self._record_port_event("IN", port, value)
            return value
        if port == ALT_SYSTEM_DATA_PORT:
            if self.keyboard:
                value = self.pop_queued_key()
                self.alt_command_phase = "idle"
                self.alt_input_ready = False
            else:
                value = 0
            self._record_port_event("IN", port, value)
            return value
        if port in (SYSTEM_INPUT_PORT, ALT_SYSTEM_INPUT_PORT):
            if port == ALT_SYSTEM_INPUT_PORT and self.keyboard:
                if self.alt_command_phase == "wait_data_ready":
                    value = 0x04
                elif self.alt_command_phase == "wait_data_clear":
                    value = 0x02
                elif self.alt_command_phase == "wait_read_ready":
                    value = 0x01
                else:
                    value = 0x02
            elif self.system_input_script:
                value = self.system_input_script.popleft()
            else:
                value = self.system_input_value
            self._record_port_event("IN", port, value)
            return value
        value = 0xFF
        self._record_port_event("IN", port, value)
        return value

    def write_port(self, port: int, value: int) -> None:
        port &= 0xFF
        value &= 0xFF
        self._record_port_event("OUT", port, value)
        if port in (SYSTEM_CONTROL_PORT, ALT_SYSTEM_CONTROL_PORT):
            if port == ALT_SYSTEM_CONTROL_PORT:
                if value == 0x0E:
                    self.alt_command_phase = "set_data_prefix"
                elif value == 0x09 and self.keyboard and self.alt_command_phase == "set_data_value":
                    self.alt_command_phase = "wait_data_ready"
                elif value == 0x08 and self.alt_command_phase == "wait_data_ready":
                    self.alt_command_phase = "wait_data_clear"
                elif value == 0x0B and self.keyboard:
                    self.alt_command_phase = "wait_read_ready"
                elif value == 0x0A and self.alt_command_phase == "wait_read_ready":
                    self.alt_command_phase = "read_data"
            if value & 0x80:
                # Minimal handshake for the ROM's polling loops.
                self.system_status_script.extend((0x00, 0x20))
                self.system_status_value = 0
                self.system_status_toggle = True
            return
        if port == ALT_SYSTEM_COMMAND_PORT:
            self.alt_command_value = value
            if self.alt_command_phase == "set_data_prefix":
                self.alt_command_phase = "set_data_value"
            return
        if port != DISPLAY_DATA_PORT:
            return
        if value == 0x0D:
            self.cursor_col = 0
            return
        if value == 0x0A:
            self.cursor_row += 1
            if self.cursor_row >= self.memory.geometry.rows:
                self._scroll()
                self.cursor_row = self.memory.geometry.rows - 1
            return
        if value == 0x08:
            if self.cursor_col > 0:
                self.cursor_col -= 1
                self._write_at_cursor(ord(" "))
            return
        if 0x20 <= value <= 0x7E:
            self._write_at_cursor(value)
            self.cursor_col += 1
            if self.cursor_col >= self.memory.geometry.cols:
                self.cursor_col = 0
                self.cursor_row += 1
                if self.cursor_row >= self.memory.geometry.rows:
                    self._scroll()
                    self.cursor_row = self.memory.geometry.rows - 1

    def format_port_log(self) -> str:
        if not self.port_log:
            return "No port activity recorded"
        return "\n".join(self.port_log)

    def format_port_summary(self, limit: int | None = None) -> str:
        if not self.port_summary:
            return "No port activity recorded"
        lines = []
        items = sorted(
            self.port_summary.items(),
            key=lambda item: (-item[1], item[0][0], item[0][1], item[0][2]),
        )
        if limit is not None:
            items = items[: max(0, limit)]
        for (direction, port, pc), count in items:
            lines.append(
                f"{count:5d} {direction} 0x{port:02X} {self._port_name(port)} from 0x{pc:04X}"
            )
        return "\n".join(lines)

    def _record_port_event(self, direction: str, port: int, value: int) -> None:
        key = (direction, port & 0xFF, self.current_pc)
        self.port_summary[key] = self.port_summary.get(key, 0) + 1
        if self.port_log_limit <= 0:
            return
        if len(self.port_log) >= self.port_log_limit:
            self.port_log.popleft()
        self.port_log.append(
            f"PC=0x{self.current_pc:04X} {direction} 0x{port & 0xFF:02X}"
            f" {self._port_name(port)} 0x{value & 0xFF:02X}"
        )

    def _port_name(self, port: int) -> str:
        return PORT_NAMES.get(port & 0xFF, "UNKNOWN")

    def _write_at_cursor(self, value: int) -> None:
        self.memory.write_byte(
            self.memory.visible_address(self.cursor_row, self.cursor_col),
            value,
        )

    def _scroll(self) -> None:
        lines = self.memory.screen.render_lines()
        self.memory.screen.clear()
        for row, line in enumerate(lines[1:]):
            self.memory.screen.write_text(row, 0, line)
