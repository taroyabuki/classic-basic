#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../scripts/gwbasic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--archive PATH] [--runtime DIR] [--home DIR]

What it does:
  1. Download the GW-BASIC 3.23 archive if needed
  2. Extract GWBASIC.EXE from the DOS floppy image
  3. Prepare a dosemu2 runtime under /tmp

The runtime is kept in /tmp by default because dosemu2 drops privileges
internally and cannot write under /root.
EOF
}

archive_path="${DEFAULT_GWBASIC_ARCHIVE}"
exe_path="${DEFAULT_GWBASIC_EXE}"
runtime_dir="${DEFAULT_GWBASIC_RUNTIME}"
home_dir="${DEFAULT_GWBASIC_HOME}"

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
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

prepare_gwbasic_runtime "${archive_path}" "${exe_path}" "${runtime_dir}" "${home_dir}"
