#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${ROOT_DIR}/scripts/fm7basic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--rom-dir DIR]

What it does:
  1. Ensures MAME is installed, using apt when available
  2. Prepares a MAME fm77av ROM set in downloads/fm7basic/mame-roms/fm77av
  3. If the ROMs are missing, downloads the Neo Kobe emulator pack and extracts
     the FM-77AV-compatible XM7 ROM files first
  4. Verifies the prepared ROM set against the local MAME build

Default source directory:
  ${DEFAULT_FM7BASIC_SOURCE_DIR}

Neo Kobe source:
  ${DEFAULT_FM7BASIC_NEO_KOBE_URL}
  variant: ${DEFAULT_FM7BASIC_NEO_KOBE_VARIANT}
EOF
}

rom_dir="${DEFAULT_FM7BASIC_SOURCE_DIR}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --rom-dir)
      [[ $# -ge 2 ]] || die "--rom-dir requires a value"
      rom_dir="$2"
      shift 2
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

if [[ ! -f "${rom_dir}/FBASIC30.ROM" || ! -f "${rom_dir}/INITIATE.ROM" || ! -f "${rom_dir}/SUBSYS_A.ROM" || ! -f "${rom_dir}/SUBSYS_B.ROM" || ! -f "${rom_dir}/SUBSYS_C.ROM" || ! -f "${rom_dir}/SUBSYSCG.ROM" ]]; then
  if [[ ! -f "${DEFAULT_FM7BASIC_NEO_KOBE_ARCHIVE}" ]]; then
    download_file "${DEFAULT_FM7BASIC_NEO_KOBE_URL}" "${DEFAULT_FM7BASIC_NEO_KOBE_ARCHIVE}"
  fi
  extract_fm7basic_assets_from_neo_kobe "${DEFAULT_FM7BASIC_NEO_KOBE_ARCHIVE}" "${rom_dir}"
fi

ensure_mame_with_apt || require_mame
prepare_fm77av_romset "${rom_dir}" "${DEFAULT_FM77AV_MAME_ROMSET_DIR}"
if ! mame_verify_romset fm77av; then
  if [[ "${rom_dir}" == "${DEFAULT_FM7BASIC_SOURCE_DIR}" ]]; then
    if [[ ! -f "${DEFAULT_FM7BASIC_NEO_KOBE_ARCHIVE}" ]]; then
      download_file "${DEFAULT_FM7BASIC_NEO_KOBE_URL}" "${DEFAULT_FM7BASIC_NEO_KOBE_ARCHIVE}"
    fi
    extract_fm7basic_assets_from_neo_kobe "${DEFAULT_FM7BASIC_NEO_KOBE_ARCHIVE}" "${rom_dir}"
    prepare_fm77av_romset "${rom_dir}" "${DEFAULT_FM77AV_MAME_ROMSET_DIR}"
  fi
  verify_mame_romset fm77av
fi

echo "Prepared FM77AV ROM set at ${DEFAULT_FM77AV_MAME_ROMSET_DIR}"
echo "MAME ROM path: ${DEFAULT_FM7BASIC_MAME_ROMPATH}"
echo "Run with: ./run/fm7basic.sh"
