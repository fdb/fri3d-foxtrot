#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

# Build the exact mpy-cross pinned by the target MicroPythonOS release.
# The resulting bytecode must match the firmware installed on Fri3d badges.
MPOS_REF_DEFAULT="0.17.2"
EXPECTED_MICROPYTHON="78ff170de9e32c79db6e64d3e33d2bd60002bdcd"

CACHE="${MPY_CROSS_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/foxtrot/mpy-cross}"
MPOS_REF="${MPOS_REF:-$MPOS_REF_DEFAULT}"
SRC="$CACHE/src"

log() { echo "$@" >&2; }

cached="$CACHE/${MICROPYTHON_COMMIT:-$EXPECTED_MICROPYTHON}/mpy-cross"
if [[ -x "$cached" && -z "${FORCE:-}" ]]; then
    echo "$cached"
    exit 0
fi

command -v git >/dev/null || { log "error: git is required"; exit 1; }
command -v make >/dev/null || { log "error: make is required"; exit 1; }
command -v cc >/dev/null || command -v gcc >/dev/null || { log "error: a C compiler is required"; exit 1; }

fetch_at() {
    local url="$1"
    local dir="$2"
    local ref="$3"
    mkdir -p "$dir"
    git -C "$dir" rev-parse --git-dir >/dev/null 2>&1 || git -C "$dir" init -q
    git -C "$dir" remote get-url origin >/dev/null 2>&1 || git -C "$dir" remote add origin "$url"
    git -C "$dir" fetch -q --depth 1 origin "$ref"
    git -C "$dir" checkout -q FETCH_HEAD
}

pin_of() {
    git -C "$1" ls-tree HEAD "$2" | awk '{print $3}'
}

if [[ -n "${MICROPYTHON_COMMIT:-}" ]]; then
    mp="$MICROPYTHON_COMMIT"
    log "using pinned micropython $mp"
else
    log "resolving bytecode target from MicroPythonOS $MPOS_REF..."
    fetch_at https://github.com/MicroPythonOS/MicroPythonOS.git "$SRC/mpos" "$MPOS_REF"
    lvgl="$(pin_of "$SRC/mpos" lvgl_micropython)"
    fetch_at https://github.com/MicroPythonOS/lvgl_micropython "$SRC/lvgl_micropython" "$lvgl"
    mp="$(pin_of "$SRC/lvgl_micropython" lib/micropython)"
    log "  MicroPythonOS $MPOS_REF -> lvgl_micropython ${lvgl:0:8} -> micropython ${mp:0:8}"
    if [[ "$mp" != "$EXPECTED_MICROPYTHON" ]]; then
        log "error: MicroPythonOS $MPOS_REF pins $mp, expected $EXPECTED_MICROPYTHON"
        log "verify target-firmware bytecode compatibility before changing the pin"
        exit 1
    fi
fi

out="$CACHE/$mp/mpy-cross"
if [[ -x "$out" && -z "${FORCE:-}" ]]; then
    echo "$out"
    exit 0
fi

log "building mpy-cross from micropython ${mp:0:8}..."
fetch_at https://github.com/micropython/micropython "$SRC/micropython" "$mp"
make -C "$SRC/micropython/mpy-cross" -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)" >&2
mkdir -p "$(dirname "$out")"
cp "$SRC/micropython/mpy-cross/build/mpy-cross" "$out"
log "  $("$out" --version 2>&1 | head -1)"
echo "$out"
