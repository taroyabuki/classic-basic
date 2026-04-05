from __future__ import annotations

from pathlib import Path


def parse_ihex(text: str) -> bytes:
    image = bytearray()
    upper = 0
    seen_data = False
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise ValueError(f"line {line_number}: missing ':'")
        payload = bytes.fromhex(line[1:])
        if len(payload) < 5:
            raise ValueError(f"line {line_number}: truncated record")
        length = payload[0]
        address = (payload[1] << 8) | payload[2]
        record_type = payload[3]
        data = payload[4:-1]
        checksum = payload[-1]
        total = sum(payload[:-1]) & 0xFF
        expected = ((~total + 1) & 0xFF)
        if checksum != expected:
            raise ValueError(f"line {line_number}: bad checksum")
        if len(data) != length:
            raise ValueError(f"line {line_number}: length mismatch")
        if record_type == 0x00:
            absolute = upper + address
            end = absolute + len(data)
            if end > len(image):
                image.extend(b"\xFF" * (end - len(image)))
            image[absolute:end] = data
            seen_data = True
        elif record_type == 0x01:
            break
        elif record_type == 0x04:
            if len(data) != 2:
                raise ValueError(f"line {line_number}: bad extended linear address")
            upper = ((data[0] << 8) | data[1]) << 16
        else:
            continue
    if not seen_data:
        raise ValueError("no data records found")
    return bytes(image)


def parse_ihex_file(path: Path) -> bytes:
    return parse_ihex(path.read_text(encoding="ascii"))
