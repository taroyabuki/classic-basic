#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOWNLOAD_DIR="${ROOT_DIR}/downloads"
MSX_DIR="${DOWNLOAD_DIR}/msx"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"
ARCHIVE_PATH="${DOWNLOAD_DIR}/neo-kobe-emulator-pack-2013-08-17.7z"
DOWNLOAD_URL="${CLASSIC_BASIC_MSXBASIC_NEO_KOBE_URL:-https://archive.org/download/neo-kobe-emulator-pack-2013-08-17.7z/Neo%20Kobe%20emulator%20pack%202013-08-17.7z}"
DEST_ROM="${MSX_DIR}/msx1-basic-bios.rom"
DEST_VG8020_ROM="${MSX_DIR}/vg8020-20_basic-bios1.rom"
TARGET_ROM_SHA256="1c85dac5536fa3ba6f2cb70deba02ff680b34ac6cc787d2977258bd663a99555"
ARCHIVE_MEMBER="MSX/OpenMSX 0.9.1/share/machines/Philips_VG_8020-20/roms/vg8020-20_basic-bios1.rom"

die() {
  echo "error: $*" >&2
  exit 2
}

run_as_root() {
  if [[ "${EUID}" == "0" ]]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    return 1
  fi
}

apt_package_has_candidate() {
  local package="$1"
  local apt_output
  apt_output="$(apt-cache policy "${package}" 2>/dev/null || true)"
  [[ -n "${apt_output}" ]] || return 1
  grep -Eq 'Candidate:[[:space:]]*\(none\)$' <<<"${apt_output}" && return 1
  grep -Eq 'Candidate:[[:space:]]*[^[:space:]]+' <<<"${apt_output}"
}

try_install_apt_package() {
  local package
  for package in "$@"; do
    if ! apt_package_has_candidate "${package}"; then
      continue
    fi
    if run_as_root apt-get install -y "${package}"; then
      return 0
    fi
  done
  return 1
}

ensure_command_available() {
  local command_name="$1"
  shift
  command -v "${command_name}" >/dev/null 2>&1 && return 0
  command -v apt-get >/dev/null 2>&1 || return 1

  export DEBIAN_FRONTEND=noninteractive
  if [[ -z "${CLASSIC_BASIC_APT_UPDATED:-}" ]]; then
    run_as_root apt-get update
    CLASSIC_BASIC_APT_UPDATED=1
    export CLASSIC_BASIC_APT_UPDATED
  fi

  try_install_apt_package "$@" || return 1
  command -v "${command_name}" >/dev/null 2>&1
}

ensure_prerequisites() {
  ensure_command_available curl curl || die "curl is not installed"
  ensure_command_available 7z p7zip-full 7zip || die "7z is not installed"
  ensure_command_available openmsx openmsx || die "openMSX is not installed"
}

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--archive PATH] [--download-url URL] [--force-download]

What it does:
  1. Download the Neo Kobe emulator pack if needed
  2. Extract ${ARCHIVE_MEMBER}
  3. Write downloads/msx/msx1-basic-bios.rom

Options:
  --archive PATH       Use an existing Neo Kobe emulator pack archive.
  --download-url URL   Override the archive download URL.
  --force-download     Re-download the archive even if it already exists.
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

ensure_prerequisites

if [[ "${force_download}" == "1" || ! -f "${archive_path}" ]]; then
  curl -fL --retry 3 --output "${archive_path}" "${download_url}"
fi

[[ -f "${archive_path}" ]] || die "archive not found: ${archive_path}"

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "${tmpdir}"
}
trap cleanup EXIT

7z e -y "-o${tmpdir}" "${archive_path}" "${ARCHIVE_MEMBER}" >/dev/null
matched_rom="${tmpdir}/vg8020-20_basic-bios1.rom"
[[ -f "${matched_rom}" ]] || die "failed to extract ${ARCHIVE_MEMBER}"
[[ "$(sha256sum "${matched_rom}" | awk '{print $1}')" == "${TARGET_ROM_SHA256}" ]] || \
  die "unexpected ROM hash for ${ARCHIVE_MEMBER}"

cp -f "${matched_rom}" "${DEST_ROM}"
cp -f "${matched_rom}" "${DEST_VG8020_ROM}"

echo "Installed ${DEST_ROM}"
echo "Installed ${DEST_VG8020_ROM}"
echo "openMSX is ready"
