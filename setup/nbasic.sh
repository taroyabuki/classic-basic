#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEST_DIR="${ROOT_DIR}/downloads/pc8001"
DEST_ROM="${DEST_DIR}/N80_11.rom"
ARCHIVE_PATH="${DEST_DIR}/pc8001.zip"
DOWNLOAD_URL="${CLASSIC_BASIC_NBASIC_ROM_URL:-https://archive.org/download/mame-0.221-roms-merged/pc8001.zip}"
ARCHIVE_MEMBER="n80v110.rom"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
    cat <<EOF >&2
usage: $USAGE_NAME [PATH_TO_N80_11.rom]

If PATH is omitted, the script:
  1. Uses an existing downloads/pc8001/N80_11.rom if present
  2. Copies ./N80_11.rom or ./n80v110.rom if present
  3. Downloads pc8001.zip from archive.org
  4. Extracts ${ARCHIVE_MEMBER} as downloads/pc8001/N80_11.rom
EOF
}

case "${1-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

if [ "$#" -gt 1 ]; then
    usage
    exit 2
fi

mkdir -p "${DEST_DIR}"

if [ "$#" -eq 1 ]; then
    cp "$1" "${DEST_ROM}"
elif [ -f "${DEST_ROM}" ]; then
    :
elif [ -f "${ROOT_DIR}/N80_11.rom" ]; then
    cp "${ROOT_DIR}/N80_11.rom" "${DEST_ROM}"
elif [ -f "${ROOT_DIR}/n80v110.rom" ]; then
    cp "${ROOT_DIR}/n80v110.rom" "${DEST_ROM}"
else
    echo "==> Downloading ROM from archive.org..."
    if [ ! -f "${ARCHIVE_PATH}" ]; then
        curl -fL --retry 3 --output "${ARCHIVE_PATH}" "${DOWNLOAD_URL}"
    else
        echo "==> Using cached archive: ${ARCHIVE_PATH}"
    fi
    7z e -y "-o${DEST_DIR}" "${ARCHIVE_PATH}" "${ARCHIVE_MEMBER}" >/dev/null
    mv -f "${DEST_DIR}/${ARCHIVE_MEMBER}" "${DEST_ROM}"
fi

printf 'installed %s\n' "${DEST_ROM}"
