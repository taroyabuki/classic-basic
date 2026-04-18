#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${ROOT_DIR}/scripts/fm7basic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--driver fm77av|fm7] [mame-options]
  $USAGE_NAME [--driver fm77av|fm7] --file PROGRAM.bas [mame-options]
  $USAGE_NAME [--driver fm77av|fm7] --run --file PROGRAM.bas [--timeout SECONDS] [mame-options]

What it does:
  - With no --file, start the host-side interactive helper and exit on Ctrl-D
  - With --file PROGRAM.bas, preload a line-numbered BASIC file, then stay interactive until Ctrl-D
  - With --run --file PROGRAM.bas, run the file in batch mode and read console RAM output
  - Additional arguments are forwarded to MAME
EOF
}

args=()
default_mame_args=(
  -midiprovider
  none
)
driver="fm77av"
file_path=""
run_program=0
timeout_spec=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --driver)
      [[ $# -ge 2 ]] || die "--driver requires a value"
      driver="$2"
      shift 2
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
    --exec|--interactive)
      die "use --file/--run instead of $1"
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

if [[ "${run_program}" == "1" && -z "${file_path}" ]]; then
  die "--run requires --file PROGRAM.bas"
fi
if [[ -n "${timeout_spec}" && "${run_program}" != "1" ]]; then
  die "--timeout requires --run --file PROGRAM.bas"
fi
if [[ -n "${file_path}" ]]; then
  resolved_file_path="$(resolve_fm7basic_program_path "${file_path}" || true)"
  [[ -n "${resolved_file_path}" ]] || die "program not found: ${file_path}"
  file_path="${resolved_file_path}"
fi

ensure_fm7basic_romset "${driver}"
if [[ "${driver}" == "fm7" ]]; then
  echo "warning: --driver fm7 is experimental; the default supported path is fm77av" >&2
fi
mame_cmd="$(find_mame_command)"

helper_args=(
  -m fm7basic_cli
  --mame-command "${mame_cmd}"
  --driver "${driver}"
  --rompath "${DEFAULT_FM7BASIC_MAME_ROMPATH}"
)
if [[ -n "${file_path}" ]]; then
  helper_args+=(--file "${file_path}")
fi
if [[ "${run_program}" == "1" ]]; then
  helper_args+=(--run)
fi
if [[ -n "${timeout_spec}" ]]; then
  helper_args+=(--timeout "${timeout_spec}")
fi
if [[ "${#default_mame_args[@]}" -gt 0 || "${#args[@]}" -gt 0 ]]; then
  helper_args+=(-- "${default_mame_args[@]}" "${args[@]}")
fi
exec env PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 "${helper_args[@]}"
