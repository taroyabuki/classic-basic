#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${ROOT_DIR}/scripts/gwbasic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR] [--command DOSCMD]
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR] --file PROGRAM.bas
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR] --run --file PROGRAM.bas
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR] --run --file PROGRAM.bas --timeout SECONDS

What it does:
  1. Ensure the GW-BASIC 3.23 executable is available
  2. Prepare a dosemu2 runtime under /tmp
  3. Launch the real GW-BASIC executable under dosemu2

Notes:
  - The default DOS command is GWBASIC
  - Interactive mode is a text shell; Ctrl-D or SYSTEM exits it
  - PROGRAM.bas is assumed to be ASCII and may use LF, CRLF, or CR line endings
  - With --file PROGRAM.bas, this wrapper loads line-numbered source into the text shell so you can type RUN
  - With --run --file PROGRAM.bas, this wrapper runs it and exits after collecting the output
  - In --run mode, INPUT, LINE INPUT, INPUT$, and INKEY$ are rejected before execution
  - Use --timeout only when you want to cap file-run wall time; default is unlimited
EOF
}

archive_path="${DEFAULT_GWBASIC_ARCHIVE}"
exe_path="${DEFAULT_GWBASIC_EXE}"
runtime_dir="${DEFAULT_GWBASIC_RUNTIME}"
home_dir="${DEFAULT_GWBASIC_HOME}"
dos_command="gwbasic"
file_path=""
run_program=""
timeout_spec=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --archive)
      [[ $# -ge 2 ]] || die "--archive requires a value"
      archive_path="$2"
      shift 2
      ;;
    --runtime)
      [[ $# -ge 2 ]] || die "--runtime requires a value"
      runtime_dir="$2"
      shift 2
      ;;
    --home)
      [[ $# -ge 2 ]] || die "--home requires a value"
      home_dir="$2"
      shift 2
      ;;
    --command)
      [[ $# -ge 2 ]] || die "--command requires a value"
      dos_command="$2"
      shift 2
      ;;
    -f|--file)
      [[ $# -ge 2 ]] || die "$1 requires a value"
      [[ -z "${file_path}" ]] || die "only one --file PROGRAM.bas argument is supported"
      file_path="$2"
      shift 2
      ;;
    -r|--run)
      run_program="1"
      shift
      ;;
    --timeout)
      [[ $# -ge 2 ]] || die "--timeout requires a value"
      timeout_spec="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -i|--interactive)
      die "use --file PROGRAM.bas instead of $1"
      ;;
    *)
      die "use --file PROGRAM.bas instead of positional argument: $1"
      ;;
  esac
done

if [[ -n "${run_program}" && -z "${file_path}" ]]; then
  die "--run requires --file PROGRAM.bas"
fi
if [[ -n "${timeout_spec}" && -z "${run_program}" ]]; then
  die "--timeout requires --run --file PROGRAM.bas"
fi
if [[ -n "${file_path}" && "${dos_command}" != "gwbasic" ]]; then
  die "--file cannot be combined with --command"
fi

if [[ -n "${run_program}" ]]; then
  export CLASSIC_BASIC_QUIET_SETUP=1
  export CLASSIC_BASIC_DOSEMU_PTY=off
fi

prepare_gwbasic_runtime "${archive_path}" "${exe_path}" "${runtime_dir}" "${home_dir}"
if [[ -n "${file_path}" ]]; then
  staged_program="$(stage_gwbasic_program "${file_path}" "${runtime_dir}")"
  if [[ -n "${run_program}" ]]; then
    python3 "${ROOT_DIR}/src/gwbasic_program_check.py" "${runtime_dir}/drive_c/${staged_program}"
    if [[ -n "${timeout_spec}" ]]; then
      CLASSIC_BASIC_DOSEMU_BATCH_TIMEOUT="${timeout_spec}" run_gwbasic_file "${runtime_dir}" "${home_dir}" "${staged_program}"
    else
      run_gwbasic_file "${runtime_dir}" "${home_dir}" "${staged_program}"
    fi
  else
    run_gwbasic "${runtime_dir}" "${home_dir}" "gwbasic ${staged_program}" "${archive_path}"
  fi
  exit $?
fi
run_gwbasic "${runtime_dir}" "${home_dir}" "${dos_command}" "${archive_path}"
