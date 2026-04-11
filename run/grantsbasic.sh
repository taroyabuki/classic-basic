#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<USAGE
Usage:
  $USAGE_NAME [--rom PATH] [--max-steps N] [--boot-step-budget N] [--prompt-step-budget N]
  $USAGE_NAME [--rom PATH] [--max-steps N] [--boot-step-budget N] [--prompt-step-budget N] --file PROGRAM.bas
  $USAGE_NAME [--rom PATH] [--max-steps N] [--boot-step-budget N] [--prompt-step-budget N] --run --file PROGRAM.bas [--timeout SECONDS]

What it does:
  - Runs Grant Searle's Z80 SBC ROM BASIC in a terminal.
  - Without --run, interactive mode exits on Ctrl-D.
  - With --file PROGRAM.bas, uploads the program line-by-line and stays interactive.
  - With --run --file PROGRAM.bas, uploads the program, RUNs it, and exits.
USAGE
}

case "${1-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

exec env PYTHONPATH="${ROOT_DIR}/src" python3 -m grants_basic "$@"
