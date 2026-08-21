#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
cd ..

APP_DIR="com.enigmeta.foxtrot"
MANIFEST="$APP_DIR/MANIFEST.JSON"
DIST="dist"
base_override=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base)
            [[ $# -ge 2 ]] || { echo "error: --base requires a git ref" >&2; exit 2; }
            base_override="$2"
            shift 2
            ;;
        *)
            echo "usage: $0 [--base <git-ref>]" >&2
            exit 2
            ;;
    esac
done

for command in git unzip uv; do
    command -v "$command" >/dev/null || { echo "error: $command is required" >&2; exit 1; }
done
[[ -f "$MANIFEST" ]] || { echo "error: missing $MANIFEST" >&2; exit 1; }

version="$(uv run --no-project python -c "import json; print(json.load(open('$MANIFEST'))['version'])")"
app_id="$(uv run --no-project python -c "import json; print(json.load(open('$MANIFEST'))['fullname'])")"
entrypoint="$(uv run --no-project python -c "import json; print(json.load(open('$MANIFEST'))['activities'][0]['entrypoint'])")"
classname="$(uv run --no-project python -c "import json; print(json.load(open('$MANIFEST'))['activities'][0]['classname'])")"

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+([+-][0-9A-Za-z.-]+)?$ ]] || { echo "error: non-semantic version: $version" >&2; exit 1; }
[[ "$app_id" == "$APP_DIR" ]] || { echo "error: manifest fullname '$app_id' does not match '$APP_DIR'" >&2; exit 1; }
[[ -f "$APP_DIR/$entrypoint" ]] || { echo "error: missing entrypoint $APP_DIR/$entrypoint" >&2; exit 1; }
rg -q "class[[:space:]]+$classname\b" "$APP_DIR/$entrypoint" || { echo "error: entrypoint does not define $classname" >&2; exit 1; }

dirty="$(git status --porcelain=v1)"
if [[ -n "$dirty" ]]; then
    echo "error: release candidates require a clean worktree:" >&2
    printf '%s\n' "$dirty" >&2
    exit 1
fi

echo "Preparing BadgeHub candidate $app_id $version @ $(git rev-parse --short HEAD)"
scripts/format.sh --check
uvx pytest tests/ -q

generated_icon="$(mktemp)"
first_build="$(mktemp)"
trap 'rm -f "$generated_icon" "$first_build"' EXIT
cp "$APP_DIR/icon_64x64.png" "$generated_icon"
uv run scripts/make_icon.py
cmp -s "$generated_icon" "$APP_DIR/icon_64x64.png" || { echo "error: launcher icon differs from artwork/foxtrot.png" >&2; exit 1; }

scripts/build_mpk.sh
mpk="$DIST/${app_id}_${version}.mpk"
cp "$mpk" "$first_build"
first_sha="$(shasum -a 256 "$first_build" | awk '{print $1}')"
scripts/build_mpk.sh
second_sha="$(shasum -a 256 "$mpk" | awk '{print $1}')"
[[ "$first_sha" == "$second_sha" ]] || { echo "error: repeated builds differ" >&2; exit 1; }

archive_entries="$(unzip -Z1 "$mpk")"
[[ -n "$archive_entries" ]] || { echo "error: package is empty" >&2; exit 1; }
outside_entry="$(printf '%s\n' "$archive_entries" | awk -v prefix="$app_id/" 'index($0, prefix) != 1 { print; exit }')"
[[ -z "$outside_entry" ]] || { echo "error: archive path outside $app_id/: $outside_entry" >&2; exit 1; }
if printf '%s\n' "$archive_entries" | rg -q '\.py$|(^|/)(__pycache__|\.DS_Store)(/|$)'; then
    echo "error: package contains source or development artifacts" >&2
    exit 1
fi
cmp -s "$MANIFEST" <(unzip -p "$mpk" "$app_id/MANIFEST.JSON") || { echo "error: packaged manifest differs" >&2; exit 1; }

while IFS= read -r source; do
    relative="${source#"$APP_DIR/"}"
    compiled="$app_id/${relative%.py}.mpy"
    printf '%s\n' "$archive_entries" | rg -Fxq "$compiled" || { echo "error: missing $compiled" >&2; exit 1; }
done < <(find "$APP_DIR" -type f -name '*.py' | sort)

if [[ -n "$base_override" ]]; then
    git rev-parse --verify --quiet "${base_override}^{commit}" >/dev/null || { echo "error: unknown base $base_override" >&2; exit 1; }
    base="$base_override"
    base_reason="explicit --base"
else
    base="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
    if [[ -n "$base" ]]; then
        base_reason="latest reachable semantic-version tag"
    else
        base="$(git rev-list --max-parents=0 HEAD | tail -1)"
        base_reason="initial release (no reachable v* tag)"
    fi
fi

if [[ "$base_reason" == "initial release (no reachable v* tag)" ]]; then
    log_range="HEAD"
else
    log_range="$base..HEAD"
fi
diff_range="$base..HEAD"
context="$DIST/badgehub-release-${version}-context.md"
byte_size="$(wc -c <"$mpk" | tr -d ' ')"
{
    echo "# BadgeHub release context"
    echo
    echo "- App: $app_id"
    echo "- Version: $version"
    echo "- Commit: $(git rev-parse HEAD)"
    echo "- Worktree: clean"
    echo "- Baseline: $base ($base_reason)"
    echo "- Non-merge commits considered: $(git rev-list --count --no-merges "$log_range")"
    echo "- Artifact: $mpk"
    echo "- Bytes: $byte_size"
    echo "- SHA-256: $second_sha"
    echo "- Entrypoint: $entrypoint -> $classname"
    echo
    echo "## Changed files"
    echo
    echo '```text'
    git diff --name-status "$diff_range"
    echo '```'
    echo
    echo "## Diff summary"
    echo
    echo '```text'
    git diff --stat "$diff_range"
    echo '```'
    echo
    echo "## Non-merge commits (oldest first)"
    echo
    echo '```text'
    git log --reverse --no-merges --date=short --format='%h  %ad  %s' "$log_range"
    echo '```'
} >"$context"

echo "BadgeHub candidate is mechanically ready"
echo "  artifact: $mpk"
echo "  bytes:    $byte_size"
echo "  sha256:   $second_sha"
echo "  context:  $context"
echo "  baseline: $base ($base_reason)"
echo "Exact-artifact smoke testing on the target badge remains required."
