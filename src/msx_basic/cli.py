"""Command-line interface for the MSX-BASIC terminal bridge."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bridge import OpenMSXBridge
from .loop import run_batch, run_interactive, run_load, run_loaded_batch


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ROM_DIR = _PROJECT_ROOT / "downloads" / "msx"
_DEFAULT_ROM_FILE = _DEFAULT_ROM_DIR / "msx1-basic-bios.rom"
# openMSX looks for machines at $OPENMSX_USER_DATA/machines/<name>/hardwareconfig.xml
_USER_DATA_DIR = _PROJECT_ROOT / "runtime" / "msx-basic"
_MACHINE_NAME = "msx-basic"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m msx_basic",
        description=(
            "Run MSX-BASIC in the terminal via openMSX.\n\n"
            "Requires an MSX-BASIC ROM file (see --rom / --machine).\n"
            "Run ./setup/msxbasic.sh to prepare:\n"
            f"  {_DEFAULT_ROM_FILE}\n"
            "or pass --rom PATH to specify a different location."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--rom",
        metavar="PATH",
        type=Path,
        default=_DEFAULT_ROM_FILE if _DEFAULT_ROM_FILE.is_file() else None,
        help=(
            "Path to a 32 KB MSX1 BIOS+BASIC ROM image. "
            f"Default: {_DEFAULT_ROM_FILE}"
        ),
    )
    source.add_argument(
        "--machine",
        metavar="NAME",
        default=None,
        help=(
            "openMSX machine name to use (e.g. National_CF-2700). "
            "ROMs must already be installed in openMSX's search path."
        ),
    )

    parser.add_argument(
        "--rom-dir",
        metavar="DIR",
        type=Path,
        default=_DEFAULT_ROM_DIR,
        help=(
            "Extra directory added to openMSX's ROM search path. "
            f"Default: {_DEFAULT_ROM_DIR}"
        ),
    )
    parser.add_argument(
        "program",
        nargs="?",
        type=Path,
        help="Optional line-numbered ASCII BASIC source file.",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help=(
            "Start interactive mode. With PROGRAM.bas, load it first and leave "
            "it ready to RUN from the Ok prompt."
        ),
    )
    parser.add_argument(
        "--run-loaded",
        action="store_true",
        help=(
            "Load PROGRAM.bas via the interactive path, RUN it, print output, "
            "and exit without returning to interactive mode."
        ),
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Resolve machine config: if --rom is given we generate a temporary config;
    # otherwise the caller must supply a --machine that already has its ROMs.
    if args.machine:
        machine = args.machine
        extra_rom_path = str(args.rom_dir) if args.rom_dir.is_dir() else None
        config_path = None
    elif args.rom:
        rom_path = args.rom.expanduser().resolve()
        if not rom_path.is_file():
            print(f"error: ROM file not found: {rom_path}", file=sys.stderr)
            print(
                f"Run ./setup/msxbasic.sh to prepare {_DEFAULT_ROM_FILE}\n"
                "or pass --rom PATH to use a different MSX1 BIOS+BASIC ROM.",
                file=sys.stderr,
            )
            return 2
        machine, config_path = _create_machine_config(rom_path)
        extra_rom_path = None
    else:
        print(
            "error: no ROM or machine specified.\n"
            f"Run ./setup/msxbasic.sh to prepare {_DEFAULT_ROM_FILE}\n"
            "or pass --rom PATH / --machine NAME.",
            file=sys.stderr,
        )
        return 2

    try:
        with OpenMSXBridge(machine=machine, extra_rom_path=extra_rom_path) as bridge:
            if args.run_loaded and args.program is None:
                print("error: --run-loaded requires PROGRAM.bas", file=sys.stderr)
                return 2
            if args.run_loaded and args.interactive:
                print("error: --run-loaded cannot be combined with --interactive", file=sys.stderr)
                return 2
            if args.run_loaded and args.program is not None:
                return run_loaded_batch(bridge, args.program)
            if args.interactive and args.program is not None:
                loaded_program_lines = run_load(bridge, args.program)
                run_interactive(bridge, loaded_program_lines=loaded_program_lines)
            elif args.program is not None:
                return run_batch(bridge, args.program)
            else:
                run_interactive(bridge)
    except KeyboardInterrupt:
        return 130

    return 0


def _create_machine_config(rom_path: Path) -> tuple[str, Path]:
    """Generate a minimal MSX1 machine config that uses *rom_path* directly.

    Returns ``(machine_name, config_path)``.  The config is written under
    ``runtime/msx-basic/machines/msx-basic/`` so openMSX finds it when
    ``OPENMSX_USER_DATA`` is set to ``runtime/msx-basic``.
    """
    import os

    machine_dir = _USER_DATA_DIR / "machines" / _MACHINE_NAME
    machine_dir.mkdir(parents=True, exist_ok=True)
    config_path = machine_dir / "hardwareconfig.xml"

    xml = f"""<?xml version="1.0" ?>
<!DOCTYPE msxconfig SYSTEM 'msxconfig2.dtd'>
<msxconfig>

  <info>
    <manufacturer>Generic</manufacturer>
    <code>MSX1-BASIC</code>
    <release_year>1983</release_year>
    <description>Generic MSX1 with user-provided BIOS+BASIC ROM.</description>
    <type>MSX</type>
    <region>eu</region>
  </info>

  <CassettePort/>

  <devices>

    <PPI id="ppi">
      <io base="0xA8" num="4"/>
      <sound>
        <volume>16000</volume>
      </sound>
      <keyboard_type>int</keyboard_type>
      <has_keypad>false</has_keypad>
      <key_ghosting>false</key_ghosting>
      <code_kana_locks>false</code_kana_locks>
      <graph_locks>false</graph_locks>
    </PPI>

    <VDP id="VDP">
      <io base="0x98" num="2" type="O"/>
      <io base="0x98" num="2" type="I"/>
      <version>TMS9918A</version>
      <vram>16</vram>
    </VDP>

    <PSG id="PSG">
      <type>YM2149</type>
      <io base="0xA0" num="2" type="O"/>
      <io base="0xA2" num="1" type="I"/>
      <sound>
        <volume>21000</volume>
      </sound>
    </PSG>

    <PrinterPort id="Printer Port">
      <io base="0x90" num="2"/>
    </PrinterPort>

    <primary slot="0">
      <ROM id="MSX BIOS with BASIC ROM">
        <rom>
          <filename>{rom_path}</filename>
        </rom>
        <mem base="0x0000" size="0x8000"/>
      </ROM>
    </primary>

    <primary external="true" slot="1"/>
    <primary external="true" slot="2"/>

    <primary slot="3">
      <RAM id="Main RAM">
        <mem base="0x0000" size="0x10000"/>
      </RAM>
    </primary>

  </devices>

</msxconfig>
"""
    if not config_path.is_file() or config_path.read_text() != xml:
        config_path.write_text(xml)

    # Point openMSX at our user data directory so it finds the machine config
    os.environ["OPENMSX_USER_DATA"] = str(_USER_DATA_DIR)

    return _MACHINE_NAME, config_path


if __name__ == "__main__":
    raise SystemExit(main())
