#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEFAULT_6502_BASIC_DIR="${ROOT_DIR}/third_party/msbasic"
DEFAULT_6502_BASIC_ROM="${DEFAULT_6502_BASIC_DIR}/osi.bin"

die() {
  echo "error: $*" >&2
  exit 2
}

ensure_6502_basic_assets() {
  command -v python3 >/dev/null 2>&1 || die "python3 is required"
  [[ -f "${DEFAULT_6502_BASIC_ROM}" ]] || die "OSI BASIC ROM not found: ${DEFAULT_6502_BASIC_ROM}"
}
