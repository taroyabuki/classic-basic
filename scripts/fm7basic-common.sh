#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

detect_default_fm7basic_source_dir() {
  local explicit="${CLASSIC_BASIC_FM7_SOURCE_DIR:-}"
  local candidate

  if [[ -n "${explicit}" ]]; then
    printf '%s\n' "${explicit}"
    return 0
  fi

  for candidate in \
    "${ROOT_DIR}/fm7basic" \
    "${ROOT_DIR}/../Fujitsu FM-7" \
    "${ROOT_DIR}/../fm7basic"
  do
    [[ -d "${candidate}" ]] || continue
    printf '%s\n' "${candidate}"
    return 0
  done

  printf '%s\n' "${ROOT_DIR}/fm7basic"
}

DEFAULT_FM7BASIC_SOURCE_DIR="$(detect_default_fm7basic_source_dir)"
DEFAULT_FM7BASIC_DOWNLOAD_DIR="${CLASSIC_BASIC_FM7_DOWNLOAD_DIR:-${ROOT_DIR}/downloads/fm7basic}"
DEFAULT_FM7BASIC_MAME_ROMPATH="${DEFAULT_FM7BASIC_DOWNLOAD_DIR}/mame-roms"
DEFAULT_FM7BASIC_MAME_ROMSET_DIR="${DEFAULT_FM7BASIC_MAME_ROMPATH}/fm7"
DEFAULT_FM77AV_MAME_ROMSET_DIR="${DEFAULT_FM7BASIC_MAME_ROMPATH}/fm77av"
DEFAULT_FM7BASIC_NEO_KOBE_ARCHIVE="${CLASSIC_BASIC_FM7_NEO_KOBE_ARCHIVE:-${DEFAULT_FM7BASIC_DOWNLOAD_DIR}/neo-kobe-emulator-pack-2013-08-17.7z}"
DEFAULT_FM7BASIC_NEO_KOBE_URL="${CLASSIC_BASIC_FM7_NEO_KOBE_URL:-https://archive.org/download/neo-kobe-emulator-pack-2013-08-17.7z/Neo%20Kobe%20emulator%20pack%202013-08-17.7z}"
DEFAULT_FM7BASIC_NEO_KOBE_VARIANT="${CLASSIC_BASIC_FM7_NEO_KOBE_VARIANT:-Fujitsu FM-7/xm7_3460/Win32}"

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

find_mame_command() {
  local explicit="${CLASSIC_BASIC_MAME:-}"
  local candidate

  if [[ -n "${explicit}" ]]; then
    [[ -x "${explicit}" ]] || return 1
    printf '%s\n' "${explicit}"
    return 0
  fi

  for candidate in "$(command -v mame 2>/dev/null || true)" /usr/games/mame; do
    [[ -n "${candidate}" ]] || continue
    [[ -x "${candidate}" ]] || continue
    printf '%s\n' "${candidate}"
    return 0
  done

  return 1
}

ensure_mame_with_apt() {
  find_mame_command >/dev/null 2>&1 && return 0
  command -v apt-get >/dev/null 2>&1 || return 1

  export DEBIAN_FRONTEND=noninteractive
  if [[ -z "${CLASSIC_BASIC_APT_UPDATED:-}" ]]; then
    run_as_root apt-get update
    CLASSIC_BASIC_APT_UPDATED=1
    export CLASSIC_BASIC_APT_UPDATED
  fi

  try_install_apt_package mame || return 1
  find_mame_command >/dev/null 2>&1
}

require_mame() {
  find_mame_command >/dev/null 2>&1 || die "MAME is not installed. Install the 'mame' package first."
}

mame_verify_romset() {
  local driver="$1"
  local mame_cmd

  mame_cmd="$(find_mame_command)" || return 1
  "${mame_cmd}" -rompath "${DEFAULT_FM7BASIC_MAME_ROMPATH}" -verifyroms "${driver}" >/dev/null 2>&1
}

verify_mame_romset() {
  local driver="$1"

  mame_verify_romset "${driver}" || die "prepared ${driver} ROM set does not match this MAME build"
}

require_command_path() {
  local command_name="$1"

  command -v "${command_name}" >/dev/null 2>&1 || die "missing required command: ${command_name}"
}

download_file() {
  local url="$1"
  local dest_path="$2"

  require_command_path curl
  mkdir -p "$(dirname "${dest_path}")"
  curl -fL --retry 3 --output "${dest_path}" "${url}"
}

pick_first_existing() {
  local path

  for path in "$@"; do
    [[ -f "${path}" ]] || continue
    printf '%s\n' "${path}"
    return 0
  done

  return 1
}

copy_required_asset() {
  local dest_path="$1"
  shift
  local source_path

  source_path="$(pick_first_existing "$@")" || die "missing required asset for ${dest_path##*/}"
  mkdir -p "$(dirname "${dest_path}")"
  cp -f "${source_path}" "${dest_path}"
}

copy_optional_asset() {
  local dest_path="$1"
  shift
  local source_path

  source_path="$(pick_first_existing "$@")" || return 0
  mkdir -p "$(dirname "${dest_path}")"
  cp -f "${source_path}" "${dest_path}"
}

prepare_fm7basic_romset() {
  local source_dir="$1"
  local dest_dir="${2:-${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}}"

  [[ -d "${source_dir}" ]] || die "source directory not found: ${source_dir}"
  copy_required_asset "${dest_dir}/fbasic300.rom" "${source_dir}/FBASIC30.ROM" "${source_dir}/fbasic30.rom"
  copy_required_asset "${dest_dir}/boot_bas.rom" "${source_dir}/BOOT_BAS.ROM" "${source_dir}/boot_bas.rom"
  copy_required_asset "${dest_dir}/boot_dos_a.rom" "${source_dir}/BOOT_DOS.ROM" "${source_dir}/boot_dos.rom"
  copy_required_asset "${dest_dir}/subsys_c.rom" "${source_dir}/SUBSYS_C.ROM" "${source_dir}/subsys_c.rom"
  copy_optional_asset "${dest_dir}/kanji.rom" "${source_dir}/KANJI.ROM" "${source_dir}/kanji.rom"
}

prepare_fm77av_romset() {
  local source_dir="$1"
  local dest_dir="${2:-${DEFAULT_FM77AV_MAME_ROMSET_DIR}}"

  [[ -d "${source_dir}" ]] || die "source directory not found: ${source_dir}"
  copy_required_asset "${dest_dir}/initiate.rom" "${source_dir}/INITIATE.ROM" "${source_dir}/initiate.rom"
  copy_required_asset "${dest_dir}/fbasic30.rom" "${source_dir}/FBASIC30.ROM" "${source_dir}/fbasic30.rom"
  copy_required_asset "${dest_dir}/subsys_a.rom" "${source_dir}/SUBSYS_A.ROM" "${source_dir}/subsys_a.rom"
  copy_required_asset "${dest_dir}/subsys_b.rom" "${source_dir}/SUBSYS_B.ROM" "${source_dir}/subsys_b.rom"
  copy_required_asset "${dest_dir}/subsys_c.rom" "${source_dir}/SUBSYS_C.ROM" "${source_dir}/subsys_c.rom"
  copy_required_asset "${dest_dir}/subsyscg.rom" "${source_dir}/SUBSYSCG.ROM" "${source_dir}/subsyscg.rom"
  copy_optional_asset "${dest_dir}/kanji.rom" \
    "${source_dir}/KANJI1.ROM" \
    "${source_dir}/KANJI.ROM" \
    "${source_dir}/kanji1.rom" \
    "${source_dir}/kanji.rom"
}

extract_fm7basic_assets_from_neo_kobe() {
  local source_archive="$1"
  local dest_dir="$2"
  local variant_dir="${3:-${DEFAULT_FM7BASIC_NEO_KOBE_VARIANT}}"
  local tmpdir

  [[ -f "${source_archive}" ]] || die "Neo Kobe archive not found: ${source_archive}"
  require_command_path 7z

  tmpdir="$(mktemp -d)"
  mkdir -p "${dest_dir}"
  7z x -y "-o${tmpdir}" "${source_archive}" \
    "${variant_dir}/FBASIC30.ROM" \
    "${variant_dir}/BOOT_BAS.ROM" \
    "${variant_dir}/BOOT_DOS.ROM" \
    "${variant_dir}/SUBSYS_C.ROM" \
    "${variant_dir}/KANJI.ROM" \
    "${variant_dir}/INITIATE.ROM" \
    "${variant_dir}/SUBSYS_A.ROM" \
    "${variant_dir}/SUBSYS_B.ROM" \
    "${variant_dir}/SUBSYSCG.ROM" \
    "${variant_dir}/KANJI1.ROM" >/dev/null
  copy_required_asset "${dest_dir}/FBASIC30.ROM" "${tmpdir}/${variant_dir}/FBASIC30.ROM"
  copy_optional_asset "${dest_dir}/BOOT_BAS.ROM" "${tmpdir}/${variant_dir}/BOOT_BAS.ROM"
  copy_optional_asset "${dest_dir}/BOOT_DOS.ROM" "${tmpdir}/${variant_dir}/BOOT_DOS.ROM"
  copy_required_asset "${dest_dir}/SUBSYS_C.ROM" "${tmpdir}/${variant_dir}/SUBSYS_C.ROM"
  copy_optional_asset "${dest_dir}/KANJI1.ROM" "${tmpdir}/${variant_dir}/KANJI1.ROM"
  copy_optional_asset "${dest_dir}/KANJI.ROM" "${tmpdir}/${variant_dir}/KANJI.ROM"
  copy_optional_asset "${dest_dir}/INITIATE.ROM" "${tmpdir}/${variant_dir}/INITIATE.ROM"
  copy_optional_asset "${dest_dir}/SUBSYS_A.ROM" "${tmpdir}/${variant_dir}/SUBSYS_A.ROM"
  copy_optional_asset "${dest_dir}/SUBSYS_B.ROM" "${tmpdir}/${variant_dir}/SUBSYS_B.ROM"
  copy_optional_asset "${dest_dir}/SUBSYSCG.ROM" "${tmpdir}/${variant_dir}/SUBSYSCG.ROM"
  rm -rf "${tmpdir}"
}

is_fm7basic_prepared() {
  [[ -d "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}" ]] || return 1
  [[ -f "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/fbasic300.rom" ]] || return 1
  [[ -f "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/boot_bas.rom" ]] || return 1
  [[ -f "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/boot_dos_a.rom" ]] || return 1
  [[ -f "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/subsys_c.rom" ]] || return 1
}

ensure_fm7basic_romset() {
  local driver="${1:-fm7}"

  require_mame

  case "${driver}" in
    fm7)
      [[ -d "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}" ]] || die "FM7-BASIC ROM set not prepared. Run ./setup/fm7basic.sh first."
      [[ -f "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/fbasic300.rom" ]] || die "missing ${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/fbasic300.rom"
      [[ -f "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/boot_bas.rom" ]] || die "missing ${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/boot_bas.rom"
      [[ -f "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/boot_dos_a.rom" ]] || die "missing ${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/boot_dos_a.rom"
      [[ -f "${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/subsys_c.rom" ]] || die "missing ${DEFAULT_FM7BASIC_MAME_ROMSET_DIR}/subsys_c.rom"
      ;;
    fm77av)
      [[ -d "${DEFAULT_FM77AV_MAME_ROMSET_DIR}" ]] || die "FM77AV ROM set not prepared. Run ./setup/fm7basic.sh first."
      [[ -f "${DEFAULT_FM77AV_MAME_ROMSET_DIR}/initiate.rom" ]] || die "missing ${DEFAULT_FM77AV_MAME_ROMSET_DIR}/initiate.rom"
      [[ -f "${DEFAULT_FM77AV_MAME_ROMSET_DIR}/fbasic30.rom" ]] || die "missing ${DEFAULT_FM77AV_MAME_ROMSET_DIR}/fbasic30.rom"
      [[ -f "${DEFAULT_FM77AV_MAME_ROMSET_DIR}/subsys_a.rom" ]] || die "missing ${DEFAULT_FM77AV_MAME_ROMSET_DIR}/subsys_a.rom"
      [[ -f "${DEFAULT_FM77AV_MAME_ROMSET_DIR}/subsys_b.rom" ]] || die "missing ${DEFAULT_FM77AV_MAME_ROMSET_DIR}/subsys_b.rom"
      [[ -f "${DEFAULT_FM77AV_MAME_ROMSET_DIR}/subsys_c.rom" ]] || die "missing ${DEFAULT_FM77AV_MAME_ROMSET_DIR}/subsys_c.rom"
      [[ -f "${DEFAULT_FM77AV_MAME_ROMSET_DIR}/subsyscg.rom" ]] || die "missing ${DEFAULT_FM77AV_MAME_ROMSET_DIR}/subsyscg.rom"
      [[ -f "${DEFAULT_FM77AV_MAME_ROMSET_DIR}/kanji.rom" ]] || die "missing ${DEFAULT_FM77AV_MAME_ROMSET_DIR}/kanji.rom"
      ;;
    *)
      die "unsupported FM7-BASIC driver: ${driver} (expected fm7 or fm77av)"
      ;;
  esac
}
