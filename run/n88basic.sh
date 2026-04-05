#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME
  $USAGE_NAME --file PROGRAM.bas
  $USAGE_NAME --run --file PROGRAM.bas
  $USAGE_NAME --run --file PROGRAM.bas --timeout SECONDS

What it does:
  - With no --file, start N88-BASIC interactively.
  - With --file PROGRAM.bas, load the file into memory and stay interactive with Ok ready for RUN.
  - With --run --file PROGRAM.bas, load, RUN, print the output, and exit.
  - Use --timeout only when you want to cap file-run wall time; default is unlimited.
EOF
}

die() {
  echo "error: $*" >&2
  exit 2
}

file_path=""
run_program=0
timeout_spec=""
args=()

while [[ $# -gt 0 ]]; do
  case "$1" in
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
    --*)
      args+=("$1")
      shift
      if [[ $# -gt 0 && "$1" != -* ]]; then
        args+=("$1")
        shift
      fi
      ;;
    *)
      die "use --file PROGRAM.bas instead of positional argument: $1"
      ;;
  esac
done

if [[ "${run_program}" == "1" && -z "${file_path}" ]]; then
  die "--run requires --file PROGRAM.bas"
fi
if [[ -n "${timeout_spec}" && "${run_program}" != "1" ]]; then
  die "--timeout requires --run --file PROGRAM.bas"
fi

export PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}"
if [[ -n "${file_path}" ]]; then
  if [[ "${run_program}" == "1" ]]; then
    if [[ -n "${timeout_spec}" ]]; then
      exec env CLASSIC_BASIC_N88_BATCH_TIMEOUT="${timeout_spec}" python3 -m n88basic_cli "${args[@]}" --run --file "${file_path}"
    fi
    exec python3 -m n88basic_cli "${args[@]}" --run --file "${file_path}"
  fi
  exec python3 -m n88basic_cli "${args[@]}" --file "${file_path}"
fi
exec python3 -m n88basic_cli "${args[@]}"
