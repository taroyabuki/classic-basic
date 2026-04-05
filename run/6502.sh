#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${ROOT_DIR}/scripts/6502-basic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--rom PATH] [--memory-size N] [--terminal-width N] [--max-steps N]
  $USAGE_NAME [--rom PATH] [--exec STATEMENTS] [--memory-size N] [--terminal-width N] [--max-steps N]
  $USAGE_NAME [--rom PATH] [--memory-size N] [--terminal-width N] [--max-steps N] --file PROGRAM.bas
  $USAGE_NAME [--rom PATH] [--memory-size N] [--terminal-width N] [--max-steps N] --run --file PROGRAM.bas

What it does:
  1. Boot OSI Microsoft BASIC for 6502 on a vendored 6502 emulator
  2. Auto-answer the initial MEMORY SIZE? / TERMINAL WIDTH? prompts
  3. With no --file/--exec, start the interactive terminal
  4. With --file PROGRAM.bas, load the program and drop into the interactive terminal
  5. With --run --file PROGRAM.bas, run the program and exit
  6. With --exec, execute statements in direct mode and exit

Examples:
  $USAGE_NAME
  $USAGE_NAME --exec 'PRINT 2+2'
  $USAGE_NAME --file demo.bas
  $USAGE_NAME --run --file demo.bas
EOF
}

rom_path="${DEFAULT_6502_BASIC_ROM}"
exec_text=""
file_path=""
run_program=""
memory_size="8191"
terminal_width="80"
max_steps="5000000000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --rom)
      [[ $# -ge 2 ]] || die "--rom requires a value"
      rom_path="$2"
      shift 2
      ;;
    --exec)
      [[ $# -ge 2 ]] || die "--exec requires a value"
      exec_text="$2"
      shift 2
      ;;
    --memory-size)
      [[ $# -ge 2 ]] || die "--memory-size requires a value"
      memory_size="$2"
      shift 2
      ;;
    --terminal-width)
      [[ $# -ge 2 ]] || die "--terminal-width requires a value"
      terminal_width="$2"
      shift 2
      ;;
    --max-steps)
      [[ $# -ge 2 ]] || die "--max-steps requires a value"
      max_steps="$2"
      shift 2
      ;;
    -f|--file)
      [[ $# -ge 2 ]] || die "$1 requires a value"
      [[ -z "${file_path}" ]] || die "only one --file PROGRAM.bas argument is supported"
      file_path="$2"
      shift 2
      ;;
    -r|--run)
      run_program="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -i|--interactive)
      die "use --file PROGRAM.bas instead of $1"
      ;;
    *)
      die "use --file PROGRAM.bas instead of positional argument: $1"
      ;;
  esac
done

if [[ -n "${run_program}" && -z "${file_path}" ]]; then
  die "--run requires --file PROGRAM.bas"
fi

ensure_6502_basic_assets

args=(--rom "${rom_path}" --memory-size "${memory_size}" --terminal-width "${terminal_width}" --max-steps "${max_steps}")
if [[ -n "${exec_text}" ]]; then
  args+=(--exec "${exec_text}")
fi
if [[ -n "${file_path}" ]]; then
  if [[ -n "${run_program}" ]]; then
    args+=("${file_path}")
  else
    args+=(--interactive "${file_path}")
  fi
elif [[ -z "${exec_text}" ]]; then
  args+=(--interactive)
fi

PYTHONPATH="${ROOT_DIR}/src" exec python3 -m z80_basic.osi_basic "${args[@]}"
