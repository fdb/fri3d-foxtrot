#!/usr/bin/env bash
# Format the project's Python and JSON sources.
#
# Python  → Ruff (Astral's formatter), run via `uvx` so nothing is installed
#           into the project — this repo has no Python env; the code is
#           MicroPython that runs on-device.
# JSON    → the stdlib `json.tool` (Astral ships no JSON formatter), run through
#           `uv` so we never invoke a bare `python`. 2-space indent, key order
#           preserved.
#
# Usage:
#   scripts/format.sh            # format in place
#   scripts/format.sh --check    # report what's unformatted, change nothing
#                                 # (exit 1 if anything would change — for CI)
set -euo pipefail

cd "$(dirname "$0")/.."

mode="write"
case "${1:-}" in
    "")        mode="write" ;;
    --check)   mode="check" ;;
    *)         echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

# ── Python: Ruff ─────────────────────────────────────────────────────
# In --check mode ruff exits non-zero when files differ; capture that instead
# of letting `set -e` abort before the JSON pass runs.
py_fail=0
if [[ "$mode" == "check" ]]; then
    uvx ruff format --check . || py_fail=1
else
    uvx ruff format .
fi

# ── JSON: stdlib json.tool ───────────────────────────────────────────
fail=0
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    formatted="$(uv run --no-project python -m json.tool --indent 2 "$f")"
    if [[ "$mode" == "check" ]]; then
        if ! diff -u "$f" <(printf '%s\n' "$formatted") >/dev/null; then
            echo "would reformat: $f"
            fail=1
        fi
    else
        printf '%s\n' "$formatted" >"$f"
        echo "formatted: $f"
    fi
done < <(git ls-files '*.json' '*.JSON')

if [[ "$mode" == "check" && ( "$py_fail" -ne 0 || "$fail" -ne 0 ) ]]; then
    echo "unformatted files found — run scripts/format.sh" >&2
    exit 1
fi
