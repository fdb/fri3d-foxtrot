#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Run the Foxtrot app on the macOS SDL emulator.
#
# Same recipe as fri3d-fox-hunt/scripts/run_on_mac.sh, minus the persona
# machinery (Foxtrot has no accounts): make sure the prebuilt MicroPythonOS
# package is on disk, symlink the app into its apps/ dir, and hand off to the
# package's run_desktop.sh. The emulator reads the working tree live through
# the symlink, so edits show up on the next run with no copy step.
#
# Usage: scripts/run_on_mac.sh [-- <extra run_desktop.sh args>]
#
# Environment:
#   MPOS_DIR        prebuilt MicroPythonOS dir (default: ~/MicroPythonOS)
#   MPOS_PKG_URL    download URL for the prebuilt macOS package zip

PROJECT_DIR="$(pwd)"
APP_ID="com.enigmeta.foxtrot"
APP_SRC="$PROJECT_DIR/$APP_ID"
MPOS_DIR="${MPOS_DIR:-$HOME/MicroPythonOS}"
MPOS_PKG_URL="${MPOS_PKG_URL:-https://debleser.s3-eu-central-1.amazonaws.com/2026-fri3d-badge/MicroPythonOS-macOS-0.12.0.zip}"
RUN_DESKTOP="$MPOS_DIR/scripts/run_desktop.sh"
BINARY="$MPOS_DIR/lvgl_micropython/build/lvgl_micropy_macOS"
LINK="$MPOS_DIR/internal_filesystem/apps/$APP_ID"

[[ -d "$APP_SRC" ]] || { echo "error: app dir not found: $APP_SRC" >&2; exit 1; }

# ── Ensure the prebuilt MicroPythonOS package is on disk ──────────────
if [[ ! -f "$BINARY" || ! -f "$RUN_DESKTOP" ]]; then
    echo "Prebuilt MicroPythonOS not found at $MPOS_DIR — downloading..."
    parent="$(dirname "$MPOS_DIR")"
    tmp_zip="$(mktemp -t MicroPythonOS.XXXXXX).zip"
    trap 'rm -f "$tmp_zip"' EXIT

    echo "  GET $MPOS_PKG_URL"
    curl -fL --progress-bar -o "$tmp_zip" "$MPOS_PKG_URL"

    echo "  Unzipping into $parent/"
    mkdir -p "$parent"
    unzip -q -o "$tmp_zip" -d "$parent"

    [[ -f "$BINARY" && -f "$RUN_DESKTOP" ]] || {
        echo "error: package unpacked but expected files are missing:" >&2
        echo "       $BINARY" >&2
        echo "       $RUN_DESKTOP" >&2
        exit 1
    }
    chmod +x "$BINARY"
    echo "  Installed MicroPythonOS at $MPOS_DIR"
fi

# ── Ensure the app is linked into MicroPythonOS's apps/ ───────────────
if [[ ! -e "$LINK" && ! -L "$LINK" ]]; then
    echo "Linking $APP_ID into $MPOS_DIR/internal_filesystem/apps/"
    ln -s "$APP_SRC" "$LINK"
elif [[ -L "$LINK" && ! -e "$LINK" ]]; then
    # Dangling symlink — its target moved (a rename of this repo, say). The
    # launcher then finds no manifest and reports "can't find app". Relink.
    echo "Relinking $APP_ID: link pointed at missing $(readlink "$LINK")"
    ln -sfn "$APP_SRC" "$LINK"
elif [[ "$(readlink "$LINK" 2>/dev/null)" != "$APP_SRC" ]]; then
    echo "warning: $LINK exists but does not point at $APP_SRC" >&2
    echo "         leaving it as-is; remove it if you want this script to relink." >&2
fi

[[ $# -gt 0 && "$1" == "--" ]] && shift

echo "Running $APP_ID on the macOS emulator..."
exec "$RUN_DESKTOP" "$APP_ID" "$@"
