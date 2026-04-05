from __future__ import annotations

import argparse
import hashlib
import html.parser
import shutil
import sys
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

PAGES = [
    "http://searle.x10host.com/z80/SimpleZ80.html",
    "https://luca.msxall.com/Z80_simples/Grant%27s%20Z80%20computer.html",
]


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        mapping = dict(attrs)
        href = mapping.get("href")
        if href:
            self.links.append(href)


def discover_zip() -> tuple[bytes, str]:
    for page in PAGES:
        with urllib.request.urlopen(page) as response:
            html = response.read().decode("latin-1", errors="ignore")
        parser = LinkParser()
        parser.feed(html)
        for href in parser.links:
            if ".zip" not in href.lower():
                continue
            url = urllib.parse.urljoin(page, href)
            with urllib.request.urlopen(url) as response:
                return response.read(), url
    raise SystemExit("could not discover Grant Searle ROM zip URL")


def parse_ihex(text: str) -> bytes:
    image = bytearray()
    upper = 0
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        if not line.startswith(":"):
            raise SystemExit(f"bad Intel HEX line {line_no}")
        record = bytes.fromhex(line[1:])
        count = record[0]
        address = (record[1] << 8) | record[2]
        record_type = record[3]
        data = record[4:-1]
        checksum = record[-1]
        if (((sum(record[:-1]) & 0xFF) ^ 0xFF) + 1) & 0xFF != checksum:
            raise SystemExit(f"bad Intel HEX checksum on line {line_no}")
        if record_type == 0x00:
            absolute = upper + address
            end = absolute + count
            if end > len(image):
                image.extend(b"\xFF" * (end - len(image)))
            image[absolute:end] = data
        elif record_type == 0x01:
            break
        elif record_type == 0x04:
            upper = (((data[0] << 8) | data[1]) << 16)
    return bytes(image)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m grants_basic.setup_bundle")
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument("--rom-zip", type=Path)
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args(argv)

    download_dir = args.download_dir.resolve()
    zip_path = download_dir / "grant-searle-rom.zip"
    rom_hex_path = download_dir / "ROM.HEX"
    rom_bin_path = download_dir / "rom.bin"

    download_dir.mkdir(parents=True, exist_ok=True)

    if args.rom_zip is not None:
        shutil.copyfile(args.rom_zip, zip_path)
    elif args.update and zip_path.exists():
        zip_path.unlink()

    if not zip_path.exists():
        payload, url = discover_zip()
        zip_path.write_bytes(payload)
        print(f"downloaded {url}", file=sys.stderr)

    with zipfile.ZipFile(zip_path) as archive:
        members = {name.upper(): name for name in archive.namelist()}
        if "ROM.HEX" not in members:
            raise SystemExit("ROM.HEX not found in Grant Searle bundle")
        rom_hex_path.write_bytes(archive.read(members["ROM.HEX"]))
        for wanted in ("INTMINI.HEX", "BASIC.HEX", "INTMINI.ASM", "BASIC.ASM"):
            if wanted in members:
                (download_dir / wanted).write_bytes(archive.read(members[wanted]))

    rom_bin = parse_ihex(rom_hex_path.read_text(encoding="ascii"))
    rom_bin_path.write_bytes(rom_bin)

    actual_sha1 = hashlib.sha1(rom_bin).hexdigest()
    print(f"rom.bin sha1 {actual_sha1}", file=sys.stderr)
    print(str(rom_bin_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
