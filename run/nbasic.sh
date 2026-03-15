#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [pc8001-terminal-options]
  $USAGE_NAME [pc8001-terminal-options] --file PROGRAM.bas
  $USAGE_NAME [pc8001-terminal-options] --run --file PROGRAM.bas

What it does:
  - With no arguments, launch the PC-8001 N-BASIC ROM terminal.
  - With --file PROGRAM.bas, load the program into memory and drop into the interactive terminal.
  - With --run --file PROGRAM.bas, run the program through the PC-8001 ROM batch route.
  - Other options are forwarded to pc8001_terminal.
EOF
}

die() {
  echo "error: $*" >&2
  exit 2
}

file_path=""
run_program=0
args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    -f|--file)
      [[ $# -ge 2 ]] || die "$1 requires a value"
      [[ -z "${file_path}" ]] || die "only one --file PROGRAM.bas argument is supported"
      file_path="$2"
      shift 2
      ;;
    -r|--run)
      run_program=1
      shift
      ;;
    -i|--interactive)
      die "use --file PROGRAM.bas instead of $1"
      ;;
    --rom|--entry-point|--max-steps|--max-rounds|--trace|--breakpoint|--system-input-value|--show-state|--show-ports|--show-port-summary|--show-vram|--show-vram-summary|--port-log-limit|--summary-limit|--vram-start|--vram-stride|--vram-cell-width|--rows|--cols)
      args+=("$1")
      shift
      if [[ $# -gt 0 && "$1" != -* ]]; then
        args+=("$1")
        shift
      fi
      ;;
    --*)
      args+=("$1")
      shift
      if [[ $# -gt 0 && "$1" != -* ]]; then
        args+=("$1")
        shift
      fi
      ;;
    *)
      die "use --file PROGRAM.bas instead of positional argument: $1"
      ;;
  esac
done

if [[ "${run_program}" == "1" && -z "${file_path}" ]]; then
  die "--run requires --file PROGRAM.bas"
fi

if [[ -n "${file_path}" ]]; then
  if [[ "${run_program}" == "1" ]]; then
    args+=("${file_path}")
  else
    args+=(--interactive "${file_path}")
  fi
fi

exec env PYTHONPATH="${ROOT_DIR}/src" python3 -m pc8001_terminal "${args[@]}"
