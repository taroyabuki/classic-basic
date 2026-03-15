#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../scripts/6502-basic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--workspace DIR]

What it does:
  1. Verify that the vendored OSI BASIC binary is present
  2. Create a workspace directory for 6502 BASIC source files

This script does not launch the interpreter.
EOF
}

workspace_dir="${DEFAULT_6502_BASIC_WORKSPACE}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      [[ $# -ge 2 ]] || die "--workspace requires a value"
      workspace_dir="$2"
      shift 2
      ;;
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
prepare_6502_basic_workspace "${workspace_dir}"

echo "Prepared 6502 BASIC workspace at ${workspace_dir}"
echo "OSI BASIC ROM: ${DEFAULT_6502_BASIC_ROM}"
echo "Run with: ./run/6502.sh"
