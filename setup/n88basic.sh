#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEST_DIR="${ROOT_DIR}/downloads/n88basic"
ROM_DIR="${DEST_DIR}/roms"
ARCHIVE_PATH="${DEST_DIR}/neo-kobe-emulator-pack-2013-08-17.7z"
DOWNLOAD_URL="${CLASSIC_BASIC_N88BASIC_NEO_KOBE_URL:-https://archive.org/download/neo-kobe-emulator-pack-2013-08-17.7z/Neo%20Kobe%20emulator%20pack%202013-08-17.7z}"
ARCHIVE_PREFIX="NEC PC-8801/M88kai_2013-06-23"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"
QUASI88_DIR="${ROOT_DIR}/vendor/quasi88"
QUASI88_BIN="${QUASI88_DIR}/quasi88.sdl2"

ROM_NAMES=(
  N88.ROM
  N88_0.ROM
  N88_1.ROM
  N88_2.ROM
  N88_3.ROM
  N80.ROM
  DISK.ROM
  KANJI1.ROM
  KANJI2.ROM
  FONT.ROM
)

declare -A ARCHIVE_ROM_MAP=(
  [N88.ROM]="n88.rom"
  [N88_0.ROM]="n88_0.rom"
  [N88_1.ROM]="n88_1.rom"
  [N88_2.ROM]="n88_2.rom"
  [N88_3.ROM]="n88_3.rom"
  [N80.ROM]="n80.rom"
  [DISK.ROM]="disk.rom"
  [KANJI1.ROM]="KANJI1.ROM"
  [KANJI2.ROM]="KANJI2.ROM"
  [FONT.ROM]="font.rom"
)

usage() {
  cat <<EOF >&2
usage: $USAGE_NAME [PATH_TO_ROM_DIR]

If PATH is omitted, the script:
  1. Uses an existing downloads/n88basic/roms if all required ROMs are present
  2. Downloads the Neo Kobe emulator pack from archive.org if needed
  3. Extracts PC-8801 N88/N80 ROMs into downloads/n88basic/roms
  4. Verifies vendor/quasi88/quasi88.sdl2 exists, or builds the bundled QUASI88 0.7.3 tree with make
EOF
}

die() {
  echo "error: $*" >&2
  exit 2
}

copy_from_dir() {
  local src_dir="$1"
  mkdir -p "${ROM_DIR}"
  for rom_name in "${ROM_NAMES[@]}"; do
    local src_name="${ARCHIVE_ROM_MAP[$rom_name]}"
    if [[ ! -f "${src_dir}/${src_name}" ]]; then
      die "missing required ROM in ${src_dir}: ${src_name}"
    fi
    cp "${src_dir}/${src_name}" "${ROM_DIR}/${rom_name}"
  done
}

have_all_roms() {
  local rom_name=""
  for rom_name in "${ROM_NAMES[@]}"; do
    [[ -f "${ROM_DIR}/${rom_name}" ]] || return 1
  done
}

extract_from_archive() {
  mkdir -p "${DEST_DIR}" "${ROM_DIR}"
  if [[ ! -f "${ARCHIVE_PATH}" ]]; then
    echo "==> Downloading ROMs from Neo Kobe archive on archive.org..."
    curl -fL --retry 3 --output "${ARCHIVE_PATH}" "${DOWNLOAD_URL}"
  else
    echo "==> Using cached archive: ${ARCHIVE_PATH}"
  fi

  local member=""
  local extract_dir="${DEST_DIR}/extract.$$"
  rm -rf "${extract_dir}"
  mkdir -p "${extract_dir}"
  for rom_name in "${ROM_NAMES[@]}"; do
    member="${ARCHIVE_PREFIX}/${ARCHIVE_ROM_MAP[$rom_name]}"
    7z e -y "-o${extract_dir}" "${ARCHIVE_PATH}" "${member}" >/dev/null
    cp -f "${extract_dir}/${ARCHIVE_ROM_MAP[$rom_name]}" "${ROM_DIR}/${rom_name}"
  done
  rm -rf "${extract_dir}"
}

ensure_quasi88() {
  if [[ -x "${QUASI88_BIN}" ]]; then
    echo "==> Using bundled QUASI88 build: ${QUASI88_BIN}"
    return
  fi
  if [[ ! -d "${QUASI88_DIR}" ]]; then
    die "QUASI88 source tree is missing: ${QUASI88_DIR}"
  fi
  echo "==> Building QUASI88..."
  make -C "${QUASI88_DIR}" SDL2_CONFIG=./sdl2-config-linux
  [[ -x "${QUASI88_BIN}" ]] || die "failed to build QUASI88 at ${QUASI88_BIN}"
}

case "${1-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

if [[ "$#" -gt 1 ]]; then
  usage
  exit 2
fi

if [[ "$#" -eq 1 ]]; then
  copy_from_dir "$1"
elif have_all_roms; then
  :
else
  extract_from_archive
fi

ensure_quasi88

echo "Prepared N88-BASIC ROM set at ${ROM_DIR}"
echo "QUASI88 binary: ${QUASI88_BIN}"
echo "Run with: ./run/n88basic.sh"
