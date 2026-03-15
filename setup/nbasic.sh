#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEST_DIR="${ROOT_DIR}/downloads/pc8001"
DEST_ROM="${DEST_DIR}/N80_11.rom"
USAGE_NAME="${CLASSIC_BASIC_USAGE_NAME:-$0}"

usage() {
    cat <<EOF >&2
usage: $USAGE_NAME [PATH_TO_N80_11.rom]

If PATH is omitted, the script:
  1. Uses an existing downloads/pc8001/N80_11.rom if present
  2. Copies ./N80_11.rom or ./n80v110.rom if present
  3. Downloads the ROM from the Neo Kobe archive on archive.org
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
    echo "==> Downloading ROM from Neo Kobe archive on archive.org..."
    ZIP_URL="https://archive.org/download/Neo_Kobe_NEC_PC-8001_2016-02-25/Neo%20Kobe%20-%20NEC%20PC-8001%20%282016-02-25%29.zip"
    ZIPFILE="${TMPDIR:-/tmp}/neo_kobe_pc8001.zip"
    WORKDIR="${TMPDIR:-/tmp}/neo_kobe_pc8001_work"
    _cleanup() { rm -rf "$WORKDIR"; }
    trap _cleanup EXIT

    mkdir -p "$WORKDIR"

    if [ ! -f "$ZIPFILE" ]; then
        curl -L --progress-bar "$ZIP_URL" -o "$ZIPFILE"
    else
        echo "==> Using cached zip: $ZIPFILE"
    fi

    python3 - "$ZIPFILE" "$WORKDIR" "$DEST_DIR" <<'PYEOF'
import zipfile, sys, os, subprocess, re

zippath, workdir, outdir = sys.argv[1:4]

with zipfile.ZipFile(zippath) as z:
    candidates = [n for n in z.namelist()
                  if n.endswith('.7z') and re.search(r'\[BIOS\].*PC-8001.*\[ROM\]', n)
                  and 'mkII' not in n]
    if not candidates:
        print("ERROR: BIOS ROM .7z not found in zip.", file=sys.stderr)
        sys.exit(1)
    inner_path = candidates[0]
    print(f"==> Found: {inner_path}")
    local_7z = os.path.join(workdir, os.path.basename(inner_path))
    with z.open(inner_path) as src, open(local_7z, 'wb') as dst:
        dst.write(src.read())

subprocess.run(['7z', 'e', local_7z, 'N80_11.rom', f'-o{outdir}', '-y'], check=True)
PYEOF
fi

printf 'installed %s\n' "${DEST_ROM}"
