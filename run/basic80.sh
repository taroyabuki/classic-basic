#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${ROOT_DIR}/scripts/runcpm-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--runtime DIR] [--mbasic PATH] [--update]
  $USAGE_NAME [--runtime DIR] [--mbasic PATH] [--update] --file PROGRAM.bas
  $USAGE_NAME [--runtime DIR] [--mbasic PATH] [--update] --run --file PROGRAM.bas
  $USAGE_NAME [--runtime DIR] [--mbasic PATH] [--update] --run --file PROGRAM.bas --timeout SECONDS

What it does:
  1. Ensure RunCPM is present and built
  2. Prepare runtime with MBASIC.COM
  3. Launch MBASIC.COM on RunCPM
  4. With --file PROGRAM.bas, load the program and stay in interactive MBASIC
  5. With --run --file PROGRAM.bas, run the program and exit when it returns to CP/M
  6. Use --timeout only when you want to cap file-run wall time; default is unlimited

If --mbasic is omitted and downloads/MBASIC.COM exists, that file is used.
PROGRAM.bas is assumed to be ASCII and may use either LF or CRLF line endings.
EOF
}

runtime_dir="${DEFAULT_RUNTIME_DIR}"
mbasic_path="$(resolve_default_mbasic || true)"
vendor_update=0
file_path=""
run_program=0
timeout_spec=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --runtime)
      [[ $# -ge 2 ]] || die "--runtime requires a value"
      runtime_dir="$2"
      shift 2
      ;;
    --mbasic)
      [[ $# -ge 2 ]] || die "--mbasic requires a value"
      mbasic_path="$2"
      shift 2
      ;;
    --update)
      vendor_update=1
      shift
      ;;
    -f|--file)
      [[ $# -ge 2 ]] || die "$1 requires a value"
      [[ -z "${file_path}" ]] || die "only one --file PROGRAM.bas argument is supported"
      file_path="$2"
      shift 2
      ;;
    -r|--run)
      run_program=1
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

[[ -n "${mbasic_path}" ]] || die "MBASIC.COM is required; use --mbasic PATH"
if [[ "${run_program}" == "1" && -z "${file_path}" ]]; then
  die "--run requires --file PROGRAM.bas"
fi
if [[ -n "${timeout_spec}" && "${run_program}" != "1" ]]; then
  die "--timeout requires --run --file PROGRAM.bas"
fi

if [[ "${run_program}" == "1" ]]; then
  export CLASSIC_BASIC_QUIET_SETUP=1
fi

vendor_runcpm "${vendor_update}"
prepare_runtime "${runtime_dir}" "${mbasic_path}" ""
export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -n "${file_path}" ]]; then
  staged_program="$(copy_basic_program "${file_path}" "${runtime_dir}/A/0")"
  if [[ "${run_program}" != "1" ]]; then
    printf 'MBASIC\n' > "${runtime_dir}/AUTOEXEC.TXT"
    (
      sleep 1
      rm -f "${runtime_dir}/AUTOEXEC.TXT"
    ) &
    if [[ -t 0 ]]; then
      startup_command="$(printf 'LOAD "%s"\rLIST\r' "${staged_program}")"
      exec python3 -m basic80_interactive --runtime "${runtime_dir}" --startup-command "${startup_command}"
    fi
    exec < <(
      printf 'LOAD "%s"\rLIST\r' "${staged_program}"
      cat
    )
    launch_shell "${runtime_dir}"
    exit $?
  fi
  printf 'MBASIC %s\n' "${staged_program}" > "${runtime_dir}/AUTOEXEC.TXT"
  (
    sleep 1
    rm -f "${runtime_dir}/AUTOEXEC.TXT"
  ) &
  if [[ -n "${timeout_spec}" ]]; then
    exec env CLASSIC_BASIC_RUNCPM_BATCH_TIMEOUT="${timeout_spec}" python3 "${ROOT_DIR}/src/runcpm_batch_exit.py" \
      --runtime "${runtime_dir}" \
      --intermediate-prompt "Ok" \
      --intermediate-command "SYSTEM" \
      --output-filter basic80
  fi
  exec python3 "${ROOT_DIR}/src/runcpm_batch_exit.py" \
    --runtime "${runtime_dir}" \
    --intermediate-prompt "Ok" \
    --intermediate-command "SYSTEM" \
    --output-filter basic80
fi
if [[ -t 0 ]]; then
  exec python3 -m basic80_interactive --runtime "${runtime_dir}"
fi
launch_shell "${runtime_dir}"
