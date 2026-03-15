#!/usr/bin/env bash
# Download N80_11.rom from Neo Kobe NEC PC-8001 archive on archive.org
set -euo pipefail

ZIP_URL="https://archive.org/download/Neo_Kobe_NEC_PC-8001_2016-02-25/Neo%20Kobe%20-%20NEC%20PC-8001%20%282016-02-25%29.zip"
INNER_7Z_PATTERN="*BIOS* PC-8001*ROM*.7z"
TARGET_ROM="N80_11.rom"
ZIPFILE="${TMPDIR:-/tmp}/neo_kobe_pc8001.zip"
WORKDIR="${TMPDIR:-/tmp}/neo_kobe_pc8001_work"
OUTDIR="${1:-.}"

cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

mkdir -p "$WORKDIR" "$OUTDIR"

# Download only if not already cached
if [[ ! -f "$ZIPFILE" ]]; then
    echo "==> Downloading archive (this may take a while)..."
    curl -L --progress-bar "$ZIP_URL" -o "$ZIPFILE"
else
    echo "==> Using cached zip: $ZIPFILE"
fi

echo "==> Extracting inner 7z and $TARGET_ROM (via python)..."
python3 - "$ZIPFILE" "$WORKDIR" "$TARGET_ROM" "$OUTDIR" <<'PYEOF'
import zipfile, sys, os, subprocess, re

zippath, workdir, target_rom, outdir = sys.argv[1:5]

with zipfile.ZipFile(zippath) as z:
    # Find "[BIOS] PC-8001 [ROM].7z" (not mkII)
    candidates = [n for n in z.namelist()
                  if n.endswith('.7z') and re.search(r'\[BIOS\].*PC-8001.*\[ROM\]', n)
                  and 'mkII' not in n]
    if not candidates:
        print("ERROR: BIOS ROM .7z not found in zip. Available .7z with BIOS:", file=sys.stderr)
        for n in z.namelist():
            if 'BIOS' in n and n.endswith('.7z'):
                print(" ", n, file=sys.stderr)
        sys.exit(1)

    inner_path = candidates[0]
    print(f"==> Found: {inner_path}")
    local_7z = os.path.join(workdir, os.path.basename(inner_path))
    with z.open(inner_path) as src, open(local_7z, 'wb') as dst:
        dst.write(src.read())
    print(f"==> Extracted inner 7z to {local_7z}")

subprocess.run(['7z', 'e', local_7z, target_rom, f'-o{outdir}', '-y'], check=True)
PYEOF

if [[ -f "$OUTDIR/$TARGET_ROM" ]]; then
    echo "==> Done: $OUTDIR/$TARGET_ROM"
    ls -lh "$OUTDIR/$TARGET_ROM"
else
    echo "ERROR: $TARGET_ROM not found. Files in 7z:" >&2
    7z l "$INNER_7Z_FILE" >&2
    exit 1
fi
