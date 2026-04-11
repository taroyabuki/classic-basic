#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEFAULT_FM11BASIC_DOWNLOAD_DIR="${CLASSIC_BASIC_FM11_DOWNLOAD_DIR:-${ROOT_DIR}/downloads/fm11basic}"
DEFAULT_FM11BASIC_STAGING_DIR="${DEFAULT_FM11BASIC_DOWNLOAD_DIR}/staged"

DEFAULT_FM11BASIC_EMULATOR_URL="${CLASSIC_BASIC_FM11_EMULATOR_URL:-http://nanno.bf1.jp/softlib/program/fm11.zip}"
DEFAULT_FM11BASIC_EMULATOR_ARCHIVE="${CLASSIC_BASIC_FM11_EMULATOR_ARCHIVE:-${DEFAULT_FM11BASIC_DOWNLOAD_DIR}/fm11_x86.zip}"
DEFAULT_FM11BASIC_EMULATOR_SHA256="${CLASSIC_BASIC_FM11_EMULATOR_SHA256:-dfc27473bfd014d426b82ebfeb769542126186461e953c3de2a4495ff5301aa9}"
DEFAULT_FM11BASIC_EMULATOR_STAGE_DIR="${DEFAULT_FM11BASIC_STAGING_DIR}/emulator-x86"
DEFAULT_FM11BASIC_EMULATOR_EXE="${DEFAULT_FM11BASIC_EMULATOR_STAGE_DIR}/Fm11.exe"

DEFAULT_FM11BASIC_ROM_URL="${CLASSIC_BASIC_FM11_ROM_URL:-https://archive.org/download/mame-0.221-roms-merged/fm11.zip}"
DEFAULT_FM11BASIC_ROM_ARCHIVE="${CLASSIC_BASIC_FM11_ROM_ARCHIVE:-${DEFAULT_FM11BASIC_DOWNLOAD_DIR}/fm11.zip}"
DEFAULT_FM11BASIC_ROM_STAGE_DIR="${DEFAULT_FM11BASIC_STAGING_DIR}/roms"

DEFAULT_FM11BASIC_DISK_URL="${CLASSIC_BASIC_FM11_DISK_URL:-http://nanno.bf1.jp/softlib/man/fm11/fb2hd00.img}"
DEFAULT_FM11BASIC_DISK_ARCHIVE="${CLASSIC_BASIC_FM11_DISK_ARCHIVE:-${DEFAULT_FM11BASIC_DOWNLOAD_DIR}/fb2hd00.img}"
DEFAULT_FM11BASIC_DISK_SHA256="${CLASSIC_BASIC_FM11_DISK_SHA256:-7d8702b53141ec68c1fb7ee8be85d2f496714c01fe08cde489a34e00c3228497}"
DEFAULT_FM11BASIC_DISK_STAGE_DIR="${DEFAULT_FM11BASIC_STAGING_DIR}/disks"
DEFAULT_FM11BASIC_DISK_STAGE_PATH="${DEFAULT_FM11BASIC_DISK_STAGE_DIR}/fb2hd00.img"

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

ensure_apt_updated() {
  command -v apt-get >/dev/null 2>&1 || return 1
  export DEBIAN_FRONTEND=noninteractive
  if [[ -z "${CLASSIC_BASIC_APT_UPDATED:-}" ]]; then
    run_as_root apt-get update
    CLASSIC_BASIC_APT_UPDATED=1
    export CLASSIC_BASIC_APT_UPDATED
  fi
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

require_command_path() {
  local command_name="$1"

  command -v "${command_name}" >/dev/null 2>&1 || die "missing required command: ${command_name}"
}

ensure_command_available() {
  local command_name="$1"
  shift

  command -v "${command_name}" >/dev/null 2>&1 && return 0
  ensure_apt_updated || return 1
  try_install_apt_package "$@" || return 1
  command -v "${command_name}" >/dev/null 2>&1
}

ensure_wine_with_apt() {
  command -v wine >/dev/null 2>&1 && return 0
  command -v apt-get >/dev/null 2>&1 || return 1
  ensure_apt_updated || return 1

  if command -v dpkg >/dev/null 2>&1 && apt_package_has_candidate wine32; then
    if ! dpkg --print-foreign-architectures 2>/dev/null | grep -qx 'i386'; then
      run_as_root dpkg --add-architecture i386
      CLASSIC_BASIC_APT_UPDATED=""
      ensure_apt_updated || return 1
    fi
    try_install_apt_package wine32 || true
  fi

  try_install_apt_package wine wine64 wine-stable || return 1
  command -v wine >/dev/null 2>&1
}

verify_sha256() {
  local file_path="$1"
  local expected_sha256="$2"
  local actual_sha256

  actual_sha256="$(sha256sum "${file_path}" | awk '{print $1}')"
  [[ "${actual_sha256}" == "${expected_sha256}" ]] || die "SHA256 mismatch for ${file_path}"
}

download_file() {
  local url="$1"
  local dest_path="$2"

  require_command_path curl
  mkdir -p "$(dirname "${dest_path}")"
  curl -fL --retry 3 --output "${dest_path}" "${url}"
}

fetch_archive() {
  local url="$1"
  local dest_path="$2"

  if [[ -s "${dest_path}" ]]; then
    return 0
  fi
  download_file "${url}" "${dest_path}"
}

replace_dir_from_zip() {
  local archive_path="$1"
  local target_dir="$2"

  require_command_path unzip
  mkdir -p "${target_dir}"
  find "${target_dir}" -mindepth 1 -delete
  unzip -oq "${archive_path}" -d "${target_dir}"
}

require_file() {
  local file_path="$1"
  [[ -f "${file_path}" ]] || die "expected staged file is missing: ${file_path}"
}

copy_file() {
  local source_path="$1"
  local destination_path="$2"

  mkdir -p "$(dirname "${destination_path}")"
  cp -f "${source_path}" "${destination_path}"
}

fm11basic_assets_are_staged() {
  local file_path

  for file_path in \
    "${DEFAULT_FM11BASIC_EMULATOR_EXE}" \
    "${DEFAULT_FM11BASIC_DISK_STAGE_PATH}" \
    "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/boot6809.rom" \
    "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/boot8088.rom" \
    "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/kanji.rom" \
    "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/subsys.rom" \
    "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/subsys_e.rom"
  do
    [[ -f "${file_path}" ]] || return 1
  done
}

bootstrap_fm11basic_assets() {
  if fm11basic_assets_are_staged; then
    return 0
  fi

  require_command_path curl
  require_command_path unzip
  require_command_path sha256sum

  mkdir -p "${DEFAULT_FM11BASIC_DOWNLOAD_DIR}" "${DEFAULT_FM11BASIC_STAGING_DIR}"

  fetch_archive "${DEFAULT_FM11BASIC_EMULATOR_URL}" "${DEFAULT_FM11BASIC_EMULATOR_ARCHIVE}"
  fetch_archive "${DEFAULT_FM11BASIC_ROM_URL}" "${DEFAULT_FM11BASIC_ROM_ARCHIVE}"
  fetch_archive "${DEFAULT_FM11BASIC_DISK_URL}" "${DEFAULT_FM11BASIC_DISK_ARCHIVE}"

  verify_sha256 "${DEFAULT_FM11BASIC_EMULATOR_ARCHIVE}" "${DEFAULT_FM11BASIC_EMULATOR_SHA256}"
  verify_sha256 "${DEFAULT_FM11BASIC_DISK_ARCHIVE}" "${DEFAULT_FM11BASIC_DISK_SHA256}"

  replace_dir_from_zip "${DEFAULT_FM11BASIC_EMULATOR_ARCHIVE}" "${DEFAULT_FM11BASIC_EMULATOR_STAGE_DIR}"
  require_file "${DEFAULT_FM11BASIC_EMULATOR_EXE}"

  replace_dir_from_zip "${DEFAULT_FM11BASIC_ROM_ARCHIVE}" "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/boot6809.rom"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/boot8088.rom"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/kanji.rom"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/subsys.rom"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/subsys_e.rom"

  copy_file "${DEFAULT_FM11BASIC_DISK_ARCHIVE}" "${DEFAULT_FM11BASIC_DISK_STAGE_PATH}"
  require_file "${DEFAULT_FM11BASIC_DISK_STAGE_PATH}"
}

require_fm11basic_assets() {
  require_file "${DEFAULT_FM11BASIC_EMULATOR_EXE}"
  require_file "${DEFAULT_FM11BASIC_DISK_STAGE_PATH}"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/boot6809.rom"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/boot8088.rom"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/kanji.rom"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/subsys.rom"
  require_file "${DEFAULT_FM11BASIC_ROM_STAGE_DIR}/subsys_e.rom"
}
