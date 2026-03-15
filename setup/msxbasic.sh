#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOWNLOAD_DIR="${ROOT_DIR}/downloads"
MSX_DIR="${DOWNLOAD_DIR}/msx"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"
ARCHIVE_PATH="${DOWNLOAD_DIR}/blueMSXv282full.exe"
DOWNLOAD_URL="https://web.archive.org/web/20260219123305/http://bluemsx.msxblue.com/rel_download/blueMSXv282full.exe"
DEST_ROM="${MSX_DIR}/msx1-basic-bios.rom"
DEST_VG8020_ROM="${MSX_DIR}/vg8020-20_basic-bios1.rom"
TARGET_ROM_SHA256="1c85dac5536fa3ba6f2cb70deba02ff680b34ac6cc787d2977258bd663a99555"

die() {
  echo "error: $*" >&2
  exit 2
}

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--archive PATH] [--download-url URL] [--force-download]

What it does:
  1. Download blueMSX 2.8.2 Full if needed
  2. Extract the bundled MSX1 BIOS+BASIC ROM from the installer
  3. Write downloads/msx/msx1-basic-bios.rom

Options:
  --archive PATH       Use an existing blueMSX Full installer archive.
  --download-url URL   Override the archive download URL.
  --force-download     Re-download the installer even if it already exists.
EOF
}

archive_path="${ARCHIVE_PATH}"
download_url="${DOWNLOAD_URL}"
force_download=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive)
      [[ $# -ge 2 ]] || die "--archive requires a value"
      archive_path="$2"
      shift 2
      ;;
    --download-url)
      [[ $# -ge 2 ]] || die "--download-url requires a value"
      download_url="$2"
      shift 2
      ;;
    --force-download)
      force_download=1
      shift
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

mkdir -p "${DOWNLOAD_DIR}" "${MSX_DIR}"

if [[ "${force_download}" == "1" || ! -f "${archive_path}" ]]; then
  curl -fL --retry 3 --output "${archive_path}" "${download_url}"
fi

[[ -f "${archive_path}" ]] || die "archive not found: ${archive_path}"

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmpdir}"
}
trap cleanup EXIT

7z e -y "-o${tmpdir}" "${archive_path}" bluemsx.msi >/dev/null
[[ -f "${tmpdir}/bluemsx.msi" ]] || die "failed to extract bluemsx.msi"

cab_name="$(
  7z l "${tmpdir}/bluemsx.msi" |
    awk '
      /^-------------------/ {in_table = !in_table; next}
      in_table && $1 == "....." && NF >= 4 && $4 !~ /^!/ && $4 !~ /^\[/ {
        size = $2 + 0
        name = $4
        if (size > best) {
          best = size
          best_name = name
        }
      }
      END {
        if (best_name != "") {
          print best_name
        }
      }
    '
)"
[[ -n "${cab_name}" ]] || die "failed to locate the MSI cabinet payload"

7z e -y "-o${tmpdir}" "${tmpdir}/bluemsx.msi" "${cab_name}" >/dev/null
cab_path="${tmpdir}/${cab_name}"
[[ -f "${cab_path}" ]] || die "failed to extract cabinet payload"

mkdir -p "${tmpdir}/cab"
7z x -y "-o${tmpdir}/cab" "${cab_path}" >/dev/null

matched_rom="$(
  find "${tmpdir}/cab" -type f -size 32768c -print0 |
    while IFS= read -r -d '' candidate; do
      if [[ "$(sha256sum "${candidate}" | awk '{print $1}')" == "${TARGET_ROM_SHA256}" ]]; then
        printf '%s\n' "${candidate}"
        break
      fi
    done
)"

[[ -n "${matched_rom}" ]] || die "failed to locate the expected MSX1 BIOS+BASIC ROM in ${archive_path}"

cp -f "${matched_rom}" "${DEST_ROM}"
cp -f "${matched_rom}" "${DEST_VG8020_ROM}"

echo "Installed ${DEST_ROM}"
echo "Installed ${DEST_VG8020_ROM}"
if command -v openmsx >/dev/null 2>&1; then
  echo "openMSX is already installed"
else
  echo "note: openMSX is not installed; run/msxbasic.sh will require it"
fi
