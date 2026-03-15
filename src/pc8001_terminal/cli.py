from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .machine import PC8001Config, PC8001Machine, RomSpec, _preprocess_n80_basic_source

DEFAULT_ROM_PATH = Path(__file__).resolve().parent.parent.parent / "downloads/pc8001/N80_11.rom"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pc8001_terminal",
        description=(
            "Run a terminal-oriented PC-8001 emulator scaffold.\n\n"
            "Current scope: ROM/RAM mapping, text VRAM, keyboard input, a\n"
            "minimal Z80 execution path, and a terminal loop. The ROM-backed\n"
            "path is still a scaffold with hard-coded terminal hooks, not a\n"
            "full PC-8001 emulation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--rom",
        action="append",
        metavar="PATH@ADDR",
        default=[],
        help="Attach a ROM image at a hex address, for example: --rom n80.rom@0x0000",
    )
    parser.add_argument(
        "program",
        nargs="?",
        help="Optional BASIC source file to inject after boot.",
    )
    parser.add_argument(
        "--entry-point",
        type=_parse_hex,
        default=None,
        help="Start Z80 execution at this address. Default: demo firmware or no execution",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=100000,
        help="Instruction budget for each run slice. Default: 100000",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=32,
        help=(
            "Number of run-slice rounds before giving up in batch mode. "
            "Increase for programs with long computation between outputs. "
            "Default: 32. Env: CLASSIC_BASIC_PC8001_BATCH_ROUNDS"
        ),
    )
    parser.add_argument(
        "--trace",
        action="store_true",
        help="Print Z80 register state before each executed instruction",
    )
    parser.add_argument(
        "--breakpoint",
        action="append",
        type=_parse_hex,
        default=[],
        metavar="ADDR",
        help="Stop before executing this address. Repeat to add multiple breakpoints.",
    )
    parser.add_argument(
        "--system-input-value",
        type=_parse_hex,
        default=0x06,
        help="Default value returned by port 0xFE when no input pulse is queued. Default: 0x06",
    )
    parser.add_argument(
        "--keys",
        default="",
        help="ASCII keys to inject after boot. Use \\r for Enter, \\n for LF, \\xNN for bytes.",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help=(
            "Interactive mode: load PROGRAM.bas into memory (no RUN) then drop "
            "into the interactive terminal. Without PROGRAM.bas, just start the "
            "terminal as normal."
        ),
    )
    parser.add_argument(
        "--show-state",
        action="store_true",
        help="Print the stop reason, PC, and register state after each run",
    )
    parser.add_argument(
        "--show-ports",
        action="store_true",
        help="Print the recent port I/O log after each run",
    )
    parser.add_argument(
        "--show-port-summary",
        action="store_true",
        help="Print aggregated port I/O counts grouped by port and caller PC",
    )
    parser.add_argument(
        "--show-vram",
        action="store_true",
        help="Print the recent VRAM write log after each run",
    )
    parser.add_argument(
        "--show-vram-summary",
        action="store_true",
        help="Print aggregated VRAM write counts grouped by address and caller PC",
    )
    parser.add_argument(
        "--port-log-limit",
        type=int,
        default=64,
        help="How many recent port or VRAM events to keep when detailed logs are enabled. Default: 64",
    )
    parser.add_argument(
        "--summary-limit",
        type=int,
        default=32,
        help="How many rows to show in aggregated port or VRAM summaries. Default: 32",
    )
    parser.add_argument(
        "--vram-start",
        type=_parse_hex,
        default=0xF300,
        help="Text VRAM base address for the scaffold. Default: 0xF300",
    )
    parser.add_argument(
        "--vram-stride",
        type=_parse_hex,
        default=0x78,
        help="Bytes per VRAM row. Default: 0x78",
    )
    parser.add_argument(
        "--vram-cell-width",
        type=_parse_hex,
        default=2,
        help="Bytes per visible character cell in VRAM. Default: 2",
    )
    parser.add_argument("--rows", type=int, default=20, help="Terminal rows. Default: 20")
    parser.add_argument("--cols", type=int, default=40, help="Terminal columns. Default: 40")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        roms = tuple(_parse_rom_spec(item) for item in args.rom)
        if not roms and DEFAULT_ROM_PATH.is_file():
            roms = (RomSpec(path=DEFAULT_ROM_PATH, start=0x0000, name=DEFAULT_ROM_PATH.name),)
        entry_point = args.entry_point
        if entry_point is None and len(roms) == 1 and roms[0].start == 0x0000:
            entry_point = 0x0000
        machine = PC8001Machine(
            PC8001Config(
                roms=roms,
                entry_point=entry_point,
                startup_program=Path(args.program) if args.program else None,
                run_startup=not args.interactive,
                max_steps=args.max_steps,
                batch_rounds=int(os.environ.get("CLASSIC_BASIC_PC8001_BATCH_ROUNDS", args.max_rounds)),
                vram_start=args.vram_start,
                vram_stride=args.vram_stride,
                vram_cell_width=args.vram_cell_width,
                rows=args.rows,
                cols=args.cols,
                port_log_limit=args.port_log_limit if args.show_ports else 0,
                vram_log_limit=args.port_log_limit if args.show_vram else 0,
                summary_limit=args.summary_limit,
                breakpoints=tuple(args.breakpoint),
                system_input_value=args.system_input_value,
            )
        )
        machine.cpu.trace = args.trace
        if args.keys:
            machine.load_roms()
            machine.boot_demo()
            keys = _preprocess_keys_for_n80_basic(args.keys)
            machine.inject_keys(_decode_key_sequence(keys))
            machine._run_host_basic_until_settled()
            machine._print_non_tty_output()
            rc = 0
        else:
            try:
                rc = machine.run_terminal()
            except KeyboardInterrupt:
                rc = 0
        if args.show_state:
            print(machine.format_state_summary(), file=sys.stderr)
        if args.show_ports:
            print(machine.format_port_log(), file=sys.stderr)
        if args.show_port_summary:
            print(machine.format_port_summary(), file=sys.stderr)
        if args.show_vram:
            print(machine.format_vram_log(), file=sys.stderr)
        if args.show_vram_summary:
            print(machine.format_vram_summary(), file=sys.stderr)
        return rc
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _parse_hex(value: str) -> int:
    return int(value, 0)


def _parse_rom_spec(spec: str) -> RomSpec:
    if "@" not in spec:
        raise ValueError(f"ROM spec must be PATH@ADDR: {spec}")
    path_text, start_text = spec.rsplit("@", 1)
    path = Path(path_text)
    return RomSpec(path=path, start=_parse_hex(start_text), name=path.name)


def _decode_key_sequence(text: str) -> list[int]:
    decoded = text.encode("utf-8").decode("unicode_escape")
    return [ord(char) for char in decoded]


_BASIC_LINE_PREFIX = __import__("re").compile(r"^\d+\s")


def _preprocess_keys_for_n80_basic(keys: str) -> str:
    """If *keys* looks like N-BASIC source (lines prefixed with line numbers),
    apply source-level rewrites (FOR loop rewriting, condition normalisation)
    so that the interactive ROM path behaves like the binary-injection path.
    Non-BASIC keys (single commands, etc.) pass through unchanged.
    """
    # Split on literal \r escape sequences (the raw string the user passed in)
    parts = keys.split(r"\r")
    if not any(_BASIC_LINE_PREFIX.match(p.lstrip()) for p in parts):
        return keys
    # Reassemble as plain source lines (without the \r markers) for preprocessing
    source = "\n".join(p for p in parts if p)
    preprocessed = _preprocess_n80_basic_source(source)
    # Rebuild as \r-separated key sequence
    return r"\r".join(preprocessed.splitlines()) + r"\r"
