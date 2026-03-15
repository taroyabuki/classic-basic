#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/../scripts/runcpm-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME [--runtime DIR] [--mbasic PATH] [--update]

What it does:
  1. Clone or update RunCPM
  2. Build RunCPM for Linux/posix
  3. Prepare runtime/A/0
  4. Download MBASIC.ZIP if needed and copy MBASIC.COM

If --mbasic is omitted, downloads/MBASIC.COM is used when present.
Otherwise the script downloads MBASIC.ZIP from retroarchive and extracts MBASIC.COM.
EOF
}

runtime_dir="${DEFAULT_RUNTIME_DIR}"
mbasic_path="$(resolve_default_mbasic || true)"
vendor_update=0

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
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

if [[ -z "${mbasic_path}" ]]; then
  ensure_default_mbasic_downloaded
  mbasic_path="${DEFAULT_MBASIC_PATH}"
fi

vendor_runcpm "${vendor_update}"
prepare_runtime "${runtime_dir}" "${mbasic_path}" "" 0
