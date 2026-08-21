#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
cd ..

# Build the only supported BadgeHub package: deterministic, ESP32-S3
# MicroPython bytecode with no Python source in the archive.
APP_ID="com.enigmeta.foxtrot"
APP_SRC="$PWD/$APP_ID"
DIST="$PWD/dist"

command -v uv >/dev/null || { echo "error: uv is required" >&2; exit 1; }
command -v zip >/dev/null || { echo "error: zip is required" >&2; exit 1; }

IN_TREE_MPY_CROSS="$HOME/Source/MicroPythonOS/lvgl_micropython/lib/micropython/mpy-cross/build/mpy-cross"
if [[ -z "${MPY_CROSS:-}" && -x "$IN_TREE_MPY_CROSS" ]]; then
    MPY_CROSS="$IN_TREE_MPY_CROSS"
fi
MPY_CROSS="${MPY_CROSS:-$(scripts/get_mpy_cross.sh)}"
[[ -x "$MPY_CROSS" ]] || { echo "error: mpy-cross not executable: $MPY_CROSS" >&2; exit 1; }

version="$(uv run --no-project python -c "import json; print(json.load(open('$APP_SRC/MANIFEST.JSON'))['version'])")"
stage_root="$(mktemp -d)"
trap 'rm -rf "$stage_root"' EXIT
stage="$stage_root/$APP_ID"
cp -R "$APP_SRC" "$stage"
find "$stage" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$stage" -name '.DS_Store' -delete

while IFS= read -r source; do
    "$MPY_CROSS" -s "${source#"$stage/"}" -O3 -march=xtensawin -o "${source%.py}.mpy" "$source"
    rm "$source"
done < <(find "$stage" -type f -name '*.py' | sort)

mkdir -p "$DIST"
mpk="$DIST/${APP_ID}_${version}.mpk"
rm -f "$mpk"
find "$stage_root" -exec touch -t 202501010000.00 {} \;
(
    cd "$stage_root"
    { find "$APP_ID" -type d; find "$APP_ID" -type f; } | sort | TZ=CET zip -q -X -r0 "$mpk" -@
)

files="$(find "$stage" -type f | wc -l | tr -d ' ')"
echo "built $mpk"
echo "  $files files, $(wc -c <"$mpk" | tr -d ' ') bytes ($version, $(git rev-parse --short HEAD 2>/dev/null || echo unversioned))"
