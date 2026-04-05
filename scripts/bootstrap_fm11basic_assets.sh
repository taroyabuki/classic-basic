#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${ROOT_DIR}/scripts/fm11basic-common.sh"

if [[ $# -ne 0 ]]; then
  echo "Usage: $0" >&2
  exit 2
fi

bootstrap_fm11basic_assets

echo "Prepared emulator: ${DEFAULT_FM11BASIC_EMULATOR_EXE}"
echo "Prepared ROMs: ${DEFAULT_FM11BASIC_ROM_STAGE_DIR}"
echo "Prepared disk: ${DEFAULT_FM11BASIC_DISK_STAGE_PATH}"
