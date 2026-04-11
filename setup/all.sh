#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

SETUP_SCRIPTS=(
  6502.sh
  basic80.sh
  nbasic.sh
  n88basic.sh
  fm7basic.sh
  fm11basic.sh
  gwbasic.sh
  msxbasic.sh
  qbasic.sh
  grantsbasic.sh
)

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME

What it does:
  1. Run every setup script in ./setup with default arguments
  2. Stop immediately if any setup step fails

Scripts:
$(printf '  - %s\n' "${SETUP_SCRIPTS[@]}")
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

for script_name in "${SETUP_SCRIPTS[@]}"; do
  echo "==> Running ./setup/${script_name}"
  bash "${SCRIPT_DIR}/${script_name}"
done

echo "Completed setup for all BASIC environments"
