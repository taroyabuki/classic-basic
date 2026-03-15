from __future__ import annotations

import argparse
from pathlib import Path

from .emulator import (
    EmulatorConfig,
    UnsupportedBdosCall,
    UnsupportedInstruction,
    Z80BasicMachine,
)
from .terminal import RawTerminal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m z80_basic",
        description="CP/M + BASIC-80 terminal runner skeleton",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    console_parser = subparsers.add_parser(
        "console",
        help="Exercise raw terminal I/O without the emulator core",
    )
    console_parser.add_argument(
        "--show-bytes",
        action="store_true",
        help="Display byte values for each received character",
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Prepare a CP/M memory image and optionally execute it",
    )
    run_parser.add_argument("--cpm-image", type=Path, required=True)
    run_parser.add_argument("--mbasic", type=Path, required=True)
    run_parser.add_argument(
        "--execute",
        action="store_true",
        help="Attempt to execute the loaded COM program with the minimal CPU core",
    )
    run_parser.add_argument(
        "--max-steps",
        type=int,
        default=100000,
        help="Instruction budget for --execute mode",
    )

    return parser


def run_console(show_bytes: bool) -> int:
    print("Raw console mode. Press Ctrl-D to exit.")

    with RawTerminal() as terminal:
        while True:
            char = terminal.read_char()
            if char == "\x04":
                terminal.write("\r\n")
                return 0

            if show_bytes:
                terminal.write(f"[{ord(char):02X}]")
            terminal.write(char)


def run_emulator(cpm_image: Path, mbasic: Path, execute: bool, max_steps: int) -> int:
    config = EmulatorConfig(
        cpm_image=cpm_image.expanduser().resolve(),
        mbasic_path=mbasic.expanduser().resolve(),
    )
    machine = Z80BasicMachine(config)
    print(machine.describe_environment())

    if not execute:
        return 0

    print("Starting execution. Press Ctrl-D if the program requests input.")
    with RawTerminal() as terminal:
        result = machine.run(console=terminal, max_steps=max_steps)

    print()
    print(
        f"Execution stopped: {result.reason} after {result.steps} steps "
        f"at 0x{result.pc:04X}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "console":
            return run_console(show_bytes=args.show_bytes)
        if args.command == "run":
            return run_emulator(
                cpm_image=args.cpm_image,
                mbasic=args.mbasic,
                execute=args.execute,
                max_steps=args.max_steps,
            )
    except (
        FileNotFoundError,
        UnsupportedBdosCall,
        UnsupportedInstruction,
        ValueError,
    ) as exc:
        print(f"error: {exc}")
        return 2

    parser.error(f"unknown command: {args.command}")
    return 2
