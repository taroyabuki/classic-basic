#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${ROOT_DIR}/scripts/qbasic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR] [--command DOSCMD]
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR] --file PROGRAM.bas
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR] --run --file PROGRAM.bas
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR] --run --file PROGRAM.bas --timeout SECONDS

What it does:
  1. Ensure the QBasic 1.1 executable is available
  2. Prepare a dosemu2 runtime under /tmp
  3. Launch the real QBasic executable under dosemu2

Notes:
  - The default DOS command is QBASIC
  - In this wrapper, typing SYSTEM exits back out of dosemu2
  - PROGRAM.bas is assumed to be ASCII and may use either LF or CRLF line endings
  - With --file PROGRAM.bas, this wrapper loads it into the QBasic IDE and stays interactive
  - With --run --file PROGRAM.bas, this wrapper runs it with `/RUN`, redirects output to a DOS file, and exits after collecting it
  - Use --timeout only when you want to cap file-run wall time; default is unlimited
EOF
}

archive_path="${DEFAULT_QBASIC_ARCHIVE}"
exe_path="${DEFAULT_QBASIC_EXE}"
runtime_dir="${DEFAULT_QBASIC_RUNTIME}"
home_dir="${DEFAULT_QBASIC_HOME}"
dos_command="qbasic"
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
if [[ -n "${file_path}" && "${dos_command}" != "qbasic" ]]; then
  die "--file cannot be combined with --command"
fi

prepare_qbasic_runtime "${archive_path}" "${exe_path}" "${runtime_dir}" "${home_dir}"
if [[ -n "${file_path}" ]]; then
  staged_program="$(stage_qbasic_program "${file_path}" "${runtime_dir}")"
  if [[ -n "${run_program}" ]]; then
    if [[ -n "${timeout_spec}" ]]; then
      CLASSIC_BASIC_DOSEMU_BATCH_TIMEOUT="${timeout_spec}" run_qbasic_file "${runtime_dir}" "${home_dir}" "${staged_program}"
    else
      run_qbasic_file "${runtime_dir}" "${home_dir}" "${staged_program}"
    fi
  else
    run_qbasic "${runtime_dir}" "${home_dir}" "qbasic ${staged_program}"
  fi
  exit $?
fi
run_qbasic "${runtime_dir}" "${home_dir}" "${dos_command}"
