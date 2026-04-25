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
    parser.add_argument("-f", "--file", type=Path, help="Load a BASIC source file")
    parser.add_argument("-r", "--run", action="store_true", help="Run the loaded BASIC source file")
    parser.add_argument("--timeout", type=float, default=None, help="Wall-clock timeout in seconds")
    parser.add_argument(
        "--max-steps",
        type=int,
        default=int(os.environ.get("GRANTS_BASIC_MAX_STEPS", "50000")),
        help="Instruction budget per execution slice",
    )
    parser.add_argument(
        "--boot-step-budget",
        type=int,
        default=int(os.environ.get("GRANTS_BASIC_BOOT_STEP_BUDGET", "10000000")),
        help="Instruction budget while waiting for the initial Memory top? prompt",
    )
    parser.add_argument(
        "--prompt-step-budget",
        type=int,
        default=int(os.environ.get("GRANTS_BASIC_PROMPT_STEP_BUDGET", "10000000")),
        help="Instruction budget while loading lines and waiting for the BASIC prompt to settle",
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
    config = GrantSearleConfig(
        rom_path=args.rom,
        max_steps=args.max_steps,
        boot_step_budget=args.boot_step_budget,
        prompt_step_budget=args.prompt_step_budget,
    )
    machine = GrantSearleMachine(config)
    try:
        _install_timeout(args.timeout)
        machine.boot()
        if args.file is not None and args.run:
            emitted_chunks: list[str] = []

            def _emit(text: str) -> None:
                emitted_chunks.append(text)
                sys.stdout.write(text)
                sys.stdout.flush()

            result = machine.run_program_file(
                args.file,
                emit_output=_emit,
            )
            if result and not emitted_chunks:
                sys.stdout.write(result)
                sys.stdout.flush()
            return 0
        if args.file is not None:
            machine.load_program_file(args.file)
        return machine.run_terminal()
    except KeyboardInterrupt:
        return 130
    except (FileNotFoundError, InputRequestError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        _clear_timeout()
