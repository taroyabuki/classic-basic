#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME
  $USAGE_NAME -f PROGRAM.bas
  $USAGE_NAME --file PROGRAM.bas
  $USAGE_NAME -r -f PROGRAM.bas [--timeout SECONDS]
  $USAGE_NAME --run -f PROGRAM.bas [--timeout SECONDS]
  $USAGE_NAME --run --file PROGRAM.bas [--timeout SECONDS]

What it does:
  - With no --file, start FM-11 F-BASIC interactively
  - With -f/--file PROGRAM.bas, load a line-numbered BASIC file and stay interactive
  - With -r/--run -f/--file PROGRAM.bas, run the file, print output, and exit
  - Use --timeout only when you want to cap file-run wall time; default is unlimited
EOF
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
  esac
fi

export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
exec python3 -m fm11basic "$@"
