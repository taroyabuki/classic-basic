#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "${ROOT_DIR}/scripts/basic-file-common.sh"
UPSTREAM_DIR="${ROOT_DIR}/third_party/RunCPM"
BUILD_DIR="${UPSTREAM_DIR}/RunCPM"
MASTER_DISK_ZIP="${UPSTREAM_DIR}/DISK/A0.ZIP"
CCP_DIR="${UPSTREAM_DIR}/CCP"
DEFAULT_RUNTIME_DIR="${ROOT_DIR}/runtime"
DEFAULT_MBASIC_PATH="${ROOT_DIR}/downloads/MBASIC.COM"
DEFAULT_MBASIC_ZIP_PATH="${ROOT_DIR}/downloads/mbasic.zip"
DEFAULT_MBASIC_DOWNLOAD_URL="http://www.retroarchive.org/cpm/lang/mbasic.zip"
DEFAULT_MBASIC_PROGRAM_NAME="RUNFILE.BAS"

die() {
  echo "error: $*" >&2
  exit 2
}

resolve_default_mbasic() {
  if [[ -f "${DEFAULT_MBASIC_PATH}" ]]; then
    printf '%s\n' "${DEFAULT_MBASIC_PATH}"
  fi
}

ensure_default_mbasic_downloaded() {
  local mbasic_dir
  local extracted_mbasic_path

  mkdir -p "$(dirname "${DEFAULT_MBASIC_PATH}")"
  mbasic_dir="$(dirname "${DEFAULT_MBASIC_PATH}")"
  extracted_mbasic_path="${mbasic_dir}/MBASIC.COM"

  if [[ -f "${DEFAULT_MBASIC_PATH}" ]]; then
    return
  fi

  python3 - "${DEFAULT_MBASIC_DOWNLOAD_URL}" "${DEFAULT_MBASIC_ZIP_PATH}" <<'PY'
import pathlib
import sys
import urllib.request

url = sys.argv[1]
zip_path = pathlib.Path(sys.argv[2])

zip_path.parent.mkdir(parents=True, exist_ok=True)

if not zip_path.exists():
    with urllib.request.urlopen(url) as response:
        zip_path.write_bytes(response.read())
PY

  rm -f "${extracted_mbasic_path}"
  7z e -y "-o${mbasic_dir}" "${DEFAULT_MBASIC_ZIP_PATH}" MBASIC.COM >/dev/null
  [[ -f "${extracted_mbasic_path}" ]] || die "failed to extract MBASIC.COM from ${DEFAULT_MBASIC_ZIP_PATH}"

  if [[ "${extracted_mbasic_path}" != "${DEFAULT_MBASIC_PATH}" ]]; then
    mv -f "${extracted_mbasic_path}" "${DEFAULT_MBASIC_PATH}"
  fi

  [[ -f "${DEFAULT_MBASIC_PATH}" ]] || die "failed to prepare MBASIC.COM at ${DEFAULT_MBASIC_PATH}"
}

ensure_upstream() {
  [[ -d "${BUILD_DIR}" ]] || die "RunCPM is missing at ${BUILD_DIR}"
  [[ -f "${MASTER_DISK_ZIP}" ]] || die "master disk zip is missing at ${MASTER_DISK_ZIP}"
}

vendor_runcpm() {
  local do_update="$1"

  if [[ ! -d "${UPSTREAM_DIR}/.git" ]]; then
    mkdir -p "${ROOT_DIR}/third_party"
    git clone https://github.com/MockbaTheBorg/RunCPM "${UPSTREAM_DIR}"
    return
  fi

  if [[ "${do_update}" == "1" ]]; then
    (
      cd "${UPSTREAM_DIR}" &&
      git -c safe.directory="${UPSTREAM_DIR}" pull --ff-only
    )
  fi
}

build_runcpm() {
  ensure_upstream
  (cd "${BUILD_DIR}" && make posix build)
  [[ -x "${BUILD_DIR}/RunCPM" ]] || die "RunCPM build did not produce an executable"
}

extract_master_disk() {
  local destination="$1"
  python3 - "${MASTER_DISK_ZIP}" "${destination}" <<'PY'
import pathlib
import sys
import zipfile

zip_path = pathlib.Path(sys.argv[1])
destination = pathlib.Path(sys.argv[2])
destination.mkdir(parents=True, exist_ok=True)

with zipfile.ZipFile(zip_path) as archive:
    archive.extractall(destination)
PY
}

copy_program() {
  local program_path="$1"
  local drive_dir="$2"
  local program_name

  [[ -f "${program_path}" ]] || die "program not found: ${program_path}"

  program_name="$(basename "${program_path}")"
  program_name="${program_name^^}"
  cp -f "${program_path}" "${drive_dir}/${program_name}"
  printf '%s\n' "${program_name}"
}

copy_basic_program() {
  local program_path="$1"
  local drive_dir="$2"
  local program_name="${3:-${DEFAULT_MBASIC_PROGRAM_NAME}}"

  normalize_basic_program "${program_path}" "${drive_dir}/${program_name}"
  printf '%s\n' "${program_name}"
}

prepare_runtime() {
  local runtime_dir="$1"
  local program_path="${2:-}"
  local autoexec_command="${3:-}"
  local allow_default_autoexec="${4:-1}"
  local copied_program=""

  build_runcpm

  mkdir -p "${runtime_dir}/A/0"
  cp -f "${BUILD_DIR}/RunCPM" "${runtime_dir}/RunCPM"
  cp -f "${CCP_DIR}"/CCP-* "${runtime_dir}/"
  extract_master_disk "${runtime_dir}/A/0"

  if [[ -n "${program_path}" ]]; then
    copied_program="$(copy_program "${program_path}" "${runtime_dir}/A/0")"
    if [[ -z "${autoexec_command}" && "${allow_default_autoexec}" == "1" ]]; then
      autoexec_command="${copied_program%.*}"
    fi
  fi

  if [[ -n "${autoexec_command}" ]]; then
    printf '%s\n' "${autoexec_command}" > "${runtime_dir}/AUTOEXEC.TXT"
  else
    rm -f "${runtime_dir}/AUTOEXEC.TXT"
  fi

  echo "Prepared RunCPM runtime at ${runtime_dir}" >&2
  echo "Drive A user 0: ${runtime_dir}/A/0" >&2
  if [[ -n "${copied_program}" ]]; then
    echo "Copied program: ${copied_program}" >&2
  fi
  if [[ -n "${autoexec_command}" ]]; then
    echo "AUTOEXEC: ${autoexec_command}" >&2
  fi
}

launch_shell() {
  local runtime_dir="$1"
  [[ -x "${runtime_dir}/RunCPM" ]] || die "runtime is not prepared: ${runtime_dir}"
  (cd "${runtime_dir}" && exec ./RunCPM)
}

launch_with_one_shot_autoexec() {
  local runtime_dir="$1"
  local autoexec_path="${runtime_dir}/AUTOEXEC.TXT"

  if [[ -f "${autoexec_path}" ]]; then
    (
      sleep 1
      rm -f "${autoexec_path}"
    ) &
  fi

  launch_shell "${runtime_dir}"
}

launch_batch_with_one_shot_autoexec() {
  local runtime_dir="$1"
  local autoexec_path="${runtime_dir}/AUTOEXEC.TXT"
  local timeout_spec="${CLASSIC_BASIC_RUNCPM_BATCH_TIMEOUT:-}"

  if [[ -f "${autoexec_path}" ]]; then
    (
      sleep 1
      rm -f "${autoexec_path}"
    ) &
  fi

  if [[ -n "${timeout_spec}" ]]; then
    python3 "${ROOT_DIR}/src/runcpm_batch_exit.py" --runtime "${runtime_dir}" --timeout "${timeout_spec}"
  else
    python3 "${ROOT_DIR}/src/runcpm_batch_exit.py" --runtime "${runtime_dir}"
  fi
}
