from __future__ import annotations

import argparse
import os
import signal
import sys
from pathlib import Path

from .machine import DEFAULT_ROM_PATH, GrantSearleConfig, GrantSearleMachine, InputRequestError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m grants_basic",
        description="Run Grant Searle's Z80 SBC ROM BASIC in a terminal.",
    )
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM_PATH, help="Path to rom.bin")
    parser.add_argument("--file", type=Path, help="Load a BASIC source file")
    parser.add_argument("--run", action="store_true", help="Run the loaded BASIC source file")
    parser.add_argument("--timeout", type=float, default=None, help="Wall-clock timeout in seconds")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=int(os.environ.get("GRANTS_BASIC_MAX_STEPS", "200000")),
        help="Instruction budget per execution slice",
    )
    return parser


def _install_timeout(seconds: float | None) -> None:
    if seconds is None:
        return

    def _handle_timeout(signum: int, frame: object) -> None:
        del signum, frame
        raise TimeoutError("timed out")

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, seconds)


def _clear_timeout() -> None:
    signal.setitimer(signal.ITIMER_REAL, 0.0)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.run and args.file is None:
        parser.error("--run requires --file")
    config = GrantSearleConfig(rom_path=args.rom, max_steps=args.max_steps)
    machine = GrantSearleMachine(config)
    try:
        _install_timeout(args.timeout)
        machine.boot()
        if args.file is not None and args.run:
            sys.stdout.write(machine.run_program_file(args.file))
            return 0
        if args.file is not None:
            machine.load_program_file(args.file)
        return machine.run_terminal()
    except (FileNotFoundError, InputRequestError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        _clear_timeout()
