#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../scripts/6502-basic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME

What it does:
  1. Verify that the vendored OSI BASIC binary is present

This script does not launch the interpreter.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

ensure_6502_basic_assets

echo "Verified 6502 BASIC assets"
echo "OSI BASIC ROM: ${DEFAULT_6502_BASIC_ROM}"
echo "Run with: ./run/6502.sh"
