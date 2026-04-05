#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${ROOT_DIR}/scripts/fm11basic-common.sh"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
  cat <<EOF
Usage:
  $USAGE_NAME

What it does:
  1. Ensures the FM-11 runtime tools are installed when apt is available
  2. Downloads the upstream FM-11 emulator, ROM set, and F-BASIC 2HD disk image
  3. Stages the downloaded files under ${DEFAULT_FM11BASIC_STAGING_DIR}
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

ensure_command_available python3 python3 || die "python3 is not installed"
ensure_command_available curl curl || die "curl is not installed"
ensure_command_available unzip unzip || die "unzip is not installed"
ensure_command_available sha256sum coreutils || die "sha256sum is not installed"
ensure_command_available Xvfb xvfb || die "Xvfb is not installed"
ensure_command_available xdpyinfo x11-utils || die "xdpyinfo is not installed"
ensure_command_available xdotool xdotool || die "xdotool is not installed"
ensure_command_available xclip xclip || die "xclip is not installed"
ensure_wine_with_apt || require_command_path wine

bash "${ROOT_DIR}/scripts/bootstrap_fm11basic_assets.sh"

echo "Run with: ./run/fm11basic.sh"
