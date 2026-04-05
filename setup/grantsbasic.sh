#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOWNLOAD_DIR="${ROOT_DIR}/downloads/grants-basic"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<USAGE
Usage:
  $USAGE_NAME [--rom-zip PATH] [--update]

What it does:
  - Downloads Grant Searle's original ROM bundle or uses --rom-zip PATH.
  - Extracts ROM.HEX, INTMINI/BASIC asm+hex, and builds downloads/grants-basic/rom.bin.
  - Prints the measured rom.bin SHA-1 for audit purposes.
USAGE
}

case "${1-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

exec env PYTHONPATH="${ROOT_DIR}/src" python3 -m grants_basic.setup_bundle --download-dir "${DOWNLOAD_DIR}" "$@"
