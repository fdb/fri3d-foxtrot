#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Deploy the Foxtrot app onto a USB-connected Fri3d badge running
# MicroPythonOS. Apps are plain files on the device's LittleFS, so this does
# NOT touch firmware — it stages a badge-clean copy (desktop cruft dropped)
# and pushes only what differs, over mpremote's raw REPL.
#
# Installing over a *running* app is not enough on its own, so this also:
#   1. returns the badge to the launcher, so no activity holds the old code;
#   2. deletes the files the source no longer has, and only those (a plain
#      copy never deletes, so orphans pile up until LittleFS is full);
#   3. drops the app's modules from sys.modules, so the next launch re-imports
#      from disk instead of reusing the previous run's cached modules.
#
# Everything badge-side rides mpremote's raw REPL, NOT an aioREPL: raw REPL
# has no echo, so bulk deploy traffic doesn't crawl at the aioREPL's
# measured ~115 B/s while LVGL starves the reader.
#
# Usage: scripts/deploy_to_badge.sh [--start] [--force] [--port /dev/cu.usbmodemXXX]
#
#   --start          launch the app on the badge after installing
#   --force          override the two provenance guards: replace a build
#                    deployed from a DIFFERENT source checkout, and/or
#                    deploy a DIRTY working tree (stamped as such)
#   --port PORT      serial port (default: auto-detect /dev/cu.usbmodem*)
#
# Env overrides:
#   BADGE_PORT       same as --port

PROJECT_DIR="$(pwd)"
APP_ID="com.enigmeta.foxtrot"
APP_SRC="$PROJECT_DIR/$APP_ID"

START=0
FORCE=0
PORT="${BADGE_PORT:-}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --start) START=1; shift ;;
        --force) FORCE=1; shift ;;
        --port)  PORT="${2:-}"; shift 2 ;;
        -h|--help)
            sed -n '5,31p' "$0"; exit 0 ;;
        *) echo "error: unknown argument '$1'" >&2; exit 2 ;;
    esac
done

# ── Provenance: WHAT is being deployed, not just whether it differs ──
# "Up to date" is only half an answer: up to date WITH WHAT? More than one
# checkout of this project may exist, and each one's copy of this script
# would happily sync the badge to its own tree and truthfully report "no
# change" — while the developer believes they are testing the other tree's
# work. So every deploy stamps where it came from (#src line in .deploy.sha:
# source dir, git commit, dirty flag), every run prints both identities, and
# a deploy from a different directory than the badge's current build REFUSES
# without --force. A tree without git deploys as "unversioned clean".
git_head=$(git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null || echo unversioned)
if [[ -n "$(git -C "$PROJECT_DIR" status --porcelain 2>/dev/null)" ]]; then
    git_state="dirty"
else
    git_state="clean"
fi
SRC_ID="$PROJECT_DIR $git_head $git_state"

# ── Refuse to deploy uncommitted work ────────────────────────────────
# The #src stamp names a commit; a dirty deploy makes that sha a lie — the
# badge would run code no commit names. --force deploys anyway, and the
# stamp stays marked dirty.
if [[ "$git_state" == "dirty" && "$FORCE" -ne 1 ]]; then
    echo "error: working tree is dirty — the badge would carry code that no commit" >&2
    echo "       names. Commit first, or re-run with --force to deploy anyway" >&2
    echo "       (stamped dirty)." >&2
    exit 1
fi

# ── Sanity checks ────────────────────────────────────────────────────
[[ -d "$APP_SRC" ]] || { echo "error: app dir not found: $APP_SRC" >&2; exit 1; }
command -v uvx >/dev/null || { echo "error: 'uvx' is required (https://docs.astral.sh/uv/)" >&2; exit 1; }

# ── Auto-detect serial port ──────────────────────────────────────────
if [[ -z "$PORT" ]]; then
    ports=(/dev/cu.usbmodem*)
    if [[ ! -e "${ports[0]}" ]]; then
        echo "error: no /dev/cu.usbmodem* device found. Is the badge plugged in?" >&2
        echo "       Pass --port or set BADGE_PORT to override." >&2
        exit 1
    fi
    if [[ ${#ports[@]} -gt 1 ]]; then
        echo "error: multiple serial devices found; pass --port to pick one:" >&2
        printf '       %s\n' "${ports[@]}" >&2
        exit 1
    fi
    PORT="${ports[0]}"
fi
[[ -e "$PORT" ]] || { echo "error: serial port not found: $PORT" >&2; exit 1; }

# ── Stage a badge-clean copy ─────────────────────────────────────────
STAGE_ROOT="$(mktemp -d)"
trap 'rm -rf "$STAGE_ROOT"' EXIT
STAGE="$STAGE_ROOT/$APP_ID"

cp -R "$APP_SRC" "$STAGE"
# Desktop-only cruft: CPython bytecode caches and Finder metadata.
find "$STAGE" -name '__pycache__' -type d -prune -exec rm -rf {} +
find "$STAGE" -name '.DS_Store' -delete

echo "Deploying $APP_ID from $PROJECT_DIR @ $git_head ($git_state) -> $PORT"

# Every phase prints its elapsed time: the deploy is a serial-port pipeline
# whose cost lives in places invisible from the outside (REPL handshakes,
# on-badge stat walks), so without these numbers a regression is
# undiagnosable — a 7-minute deploy looks exactly like a 1-minute one.
t0=$SECONDS
phase() { echo "[t+$((SECONDS - t0))s] $*"; }

MP=(uvx mpremote connect "$PORT")
APP_DIR="apps/$APP_ID"

# ── Precompile the staged tree to .mpy ───────────────────────────────
# Shipping bytecode moves the badge's cold-start compile cost here, where it
# is free. Three choices that matter:
#   - The OS's IN-TREE mpy-cross, never a pip one: the .mpy format must
#     match the firmware's bytecode version, and the in-tree binary comes
#     from the same checkout that built the firmware.
#   - -march=xtensawin, so a viper/native module would carry ESP32-S3
#     machine code. Plain modules are unaffected.
#   - -O2, NOT -O3: the only extra thing O3 strips is the line-number
#     table, and a badge traceback without line numbers is useless. -s
#     embeds the repo-relative path for the same reason.
# The entrypoint compiles too: execute_script imports by module name, so
# foxtrot.mpy loads the same as foxtrot.py did.
# No mpy-cross -> ship source, exactly as before; the emulator always runs
# source via its symlink and never sees this transform either way.
MPY_CROSS="${MPY_CROSS:-/Users/fdb/Source/MicroPythonOS/lvgl_micropython/lib/micropython/mpy-cross/build/mpy-cross}"
if [[ -x "$MPY_CROSS" ]]; then
    n_mpy=0
    while IFS= read -r f; do
        "$MPY_CROSS" -s "${f#"$STAGE/"}" -O2 -march=xtensawin -o "${f%.py}.mpy" "$f"
        rm "$f"
        n_mpy=$((n_mpy + 1))
    done < <(find "$STAGE" -name '*.py' -type f)
    phase "Precompiled $n_mpy modules to .mpy."
else
    echo "note: mpy-cross not found at $MPY_CROSS; deploying .py source." >&2
fi

# ── Stop the app, and learn what the badge currently holds ───────────
# One round trip: stop the app first (overwriting files under a live activity
# leaves it running old code with new code on disk; onDestroy also lets it
# drop its timers), then report per-file state.
#
# The badge's state normally comes from .deploy.sha, a manifest this script
# ships inside the app dir with every install ("sha16 size path" per line).
# Hashing every file costs ~0.28s apiece of open() overhead; reading one
# manifest costs one. The manifest is only TRUSTED after a stat walk proves
# it: exactly the same paths on disk, every size matching. That check is what
# makes it safe against every way the manifest can lie — an interrupted
# install truncates a file (size differs), a pruned orphan lingers in the
# lines (path gone), a store install rmtrees the dir (manifest gone too,
# which is why it lives *inside* the app dir). Any doubt → full hash walk,
# same "F <sha16> <path>" output either way. 16 hex digits of SHA-256 is far
# past collision risk for a set this size.
phase "Reading badge state..."
cat > "$STAGE_ROOT/exec1.py" <<PY
from mpos import AppManager
AppManager.restart_launcher()
import binascii, hashlib, os
root = "$APP_DIR"
man = {}
try:
    with open(root + "/.deploy.sha") as fh:
        for line in fh:
            if line.startswith("#src "):
                # Provenance stamp: which checkout+commit installed this build.
                print("SRC", line[5:].strip())
                continue
            parts = line.split()
            if len(parts) == 3:
                man[parts[2]] = (parts[0], int(parts[1]))
except OSError:
    pass
disk = {}
stack = [root]
while stack:
    p = stack.pop()
    try:
        names = os.listdir(p)
    except OSError:
        continue
    for n in names:
        f = p + "/" + n
        st = os.stat(f)
        if st[0] & 0x4000:
            stack.append(f)
        else:
            rel = f[len(root) + 1:]
            if rel != ".deploy.sha":
                disk[rel] = st[6]
ok = bool(man) and len(man) == len(disk)
ok = ok and all(r in disk and man[r][1] == disk[r] for r in man)
print("MAN", "ok" if ok else "stale")
if ok:
    for r in man:
        print("F", man[r][0], r)
else:
    buf = bytearray(1024)
    mv = memoryview(buf)
    for r in disk:
        h = hashlib.sha256()
        with open(root + "/" + r, "rb") as fh:
            while True:
                k = fh.readinto(buf)
                if not k:
                    break
                h.update(mv[:k])
        print("F", binascii.hexlify(h.digest()).decode()[:16], r)
PY
"${MP[@]}" run "$STAGE_ROOT/exec1.py" > "$STAGE_ROOT/remote.txt"

# ── Work out what actually differs ───────────────────────────────────
# The REPL speaks CRLF, so strip the \r or every path fails to match its twin.
# comm compares whole lines, so both .sha files must be sorted as whole lines --
# sorting them by path instead silently mismatches neighbours.
tr -d '\r' < "$STAGE_ROOT/remote.txt" > "$STAGE_ROOT/remote.clean"
man_state=$(sed -n 's/^MAN //p' "$STAGE_ROOT/remote.clean" | tail -1)

# ── The provenance guard itself ──────────────────────────────────────
# The badge reports the #src stamp of whatever is installed. A different
# source DIRECTORY means a different checkout: replacing its build is a
# takeover, not an update, so it must be said out loud and confirmed with
# --force. A different commit from the SAME directory is just work to
# deploy — no ceremony. No stamp at all is a pre-provenance or store
# install: nothing to compare against, deploy normally.
# stamp_ours: the badge's stamp exists AND names this checkout. Anything
# else (foreign stamp being forced over, no stamp at all) must fall through
# to the install phase so a correct stamp gets written — otherwise a forced
# takeover with identical files would leave the foreign stamp in place and
# demand --force forever after.
remote_src=$(sed -n 's/^SRC //p' "$STAGE_ROOT/remote.clean" | tail -1)
stamp_ours=0
if [[ -n "$remote_src" ]]; then
    phase "Badge holds: $remote_src"
    remote_dir="${remote_src%% *}"
    if [[ "$remote_dir" != "$PROJECT_DIR" ]]; then
        if [[ "$FORCE" -ne 1 ]]; then
            echo "error: the badge's build was deployed from a DIFFERENT checkout:" >&2
            echo "         badge:  $remote_src" >&2
            echo "         here:   $SRC_ID" >&2
            echo "       Deploying would replace that build with this tree's." >&2
            echo "       Re-run with --force if that is what you want." >&2
            exit 1
        fi
        phase "--force: replacing the $remote_dir build with this tree's."
    else
        stamp_ours=1
    fi
else
    phase "Badge build has no provenance stamp (pre-provenance or store install)."
fi
sed -n 's/^F //p' "$STAGE_ROOT/remote.clean" | sort > "$STAGE_ROOT/remote.sha"
cut -d' ' -f2- "$STAGE_ROOT/remote.sha" | sort > "$STAGE_ROOT/remote.lst"
(cd "$STAGE" && find . -type f) | sed 's|^\./||' | sort > "$STAGE_ROOT/stage.lst"
# The stage manifest is both halves of the scheme: its "sha path" columns are
# this run's side of the diff, and shipped whole as .deploy.sha it is what the
# NEXT run's stat walk validates instead of hashing. Same digest, same
# truncation, so the two sides are directly comparable.
(cd "$STAGE" && while IFS= read -r f; do
    printf '%s %s %s\n' "$(shasum -a 256 "$f" | cut -c1-16)" \
        "$(stat -f%z "$f")" "$f"
done < "$STAGE_ROOT/stage.lst") > "$STAGE_ROOT/stage.man"
awk '{print $1, $3}' "$STAGE_ROOT/stage.man" | sort > "$STAGE_ROOT/stage.sha"

# On the badge but no longer in the source.
comm -23 "$STAGE_ROOT/remote.lst" "$STAGE_ROOT/stage.lst" > "$STAGE_ROOT/orphans.lst"
# In the source but missing or different on the badge. Comparing whole
# "<sha> <path>" lines catches both cases in one pass: a changed file has no
# matching line, and a missing one has no line at all.
comm -23 "$STAGE_ROOT/stage.sha" "$STAGE_ROOT/remote.sha" | cut -d' ' -f2- \
    | sort > "$STAGE_ROOT/changed.lst"
changed_count=$(wc -l < "$STAGE_ROOT/changed.lst" | tr -d ' ')
orphan_count=$(wc -l < "$STAGE_ROOT/orphans.lst" | tr -d ' ')

# Nothing to copy, nothing to prune, manifest already proven by the stat walk:
# the whole deploy was one REPL trip. (--start still needs the second trip.)
if [[ "$changed_count" -eq 0 && "$orphan_count" -eq 0 \
      && "$man_state" == "ok" && "$stamp_ours" -eq 1 && "$START" -ne 1 ]]; then
    phase "No file changed; badge already matches this tree ($PROJECT_DIR)."
    phase "Done."
    exit 0
fi

# ── Install just the difference ──────────────────────────────────────
# `mpremote fs cp -r` merges rather than replaces — handing it a tree holding
# only the changed files updates exactly those and leaves the rest alone.
# The manifest (provenance header + "sha16 size path" lines) is pushed in a
# SEPARATE cp AFTER the delta lands, and that order is what makes a torn
# install honest: kill the deploy mid-copy and the badge still holds the OLD
# manifest, so the next run diffs against old hashes and simply re-sends
# what this run meant to — where a new manifest over half-copied files would
# vouch for content it cannot see (the stat walk checks sizes, and a stale
# file of the same size would pass as current forever). Pushing it before
# the prune below is still deliberate: die in between and the manifest names
# files the disk still holds, which the stat walk catches. A missing
# manifest (store install rmtree'd the dir) just means one slow first
# deploy. It retries because mpremote's raw-REPL handshake sometimes times
# out when the badge is busy.
# A badge whose stamp is not ours (missing, or foreign under --force)
# forces one manifest refresh so the correct stamp lands; after that the
# fast path applies again.
need_install=0
[[ "$changed_count" -gt 0 || "$orphan_count" -gt 0 || "$man_state" != "ok" \
   || "$stamp_ours" -ne 1 ]] && need_install=1
installed=1
if [[ "$need_install" -eq 1 ]]; then
    if [[ "$changed_count" -gt 0 ]]; then
        phase "Copying $changed_count changed file(s) + manifest:"
        sed 's/^/  + /' "$STAGE_ROOT/changed.lst"
    else
        phase "Refreshing manifest..."
    fi
    DELTA="$STAGE_ROOT/delta/$APP_ID"
    mkdir -p "$DELTA"
    while IFS= read -r f; do
        mkdir -p "$DELTA/$(dirname "$f")"
        cp "$STAGE/$f" "$DELTA/$f"
    done < "$STAGE_ROOT/changed.lst"
    { echo "#src $SRC_ID"; cat "$STAGE_ROOT/stage.man"; } > "$STAGE_ROOT/deploy.sha"
    installed=0
    for attempt in 1 2 3; do
        if { [[ "$changed_count" -eq 0 ]] \
               || "${MP[@]}" fs cp -r "$DELTA" :/apps/ >/dev/null; } \
           && "${MP[@]}" fs cp "$STAGE_ROOT/deploy.sha" \
                  ":$APP_DIR/.deploy.sha" >/dev/null; then
            installed=1
            break
        fi
        echo "install attempt $attempt/3 failed; letting the badge settle..." >&2
        sleep 5
    done
fi
if [[ "$installed" -ne 1 ]]; then
    echo "error: install failed three times. The badge keeps the previous copy of" >&2
    echo "       $APP_ID — re-run to repair it." >&2
    # A torn copy can leave a same-size stale file the stat walk cannot see.
    # Drop the manifest (best effort) so the next run hashes every file.
    "${MP[@]}" exec "import os
try:
    os.remove('$APP_DIR/.deploy.sha')
except OSError:
    pass" >/dev/null 2>&1 || true
    echo "       (manifest dropped: the next run re-hashes every file.)" >&2
    exit 1
fi

# ── Prune, verify, refresh, evict the old modules, and launch ────────
# All of it in one round trip. Orphans are deleted here, after the install,
# so the freshly pushed manifest already describes the pruned tree.
# The badge verifies its OWN copy against the manifest it just received, so
# it can refuse to launch a bad install without a second trip back to ask:
# path set + size for every file (a stat walk, ~free), plus a full SHA-256
# of exactly the files this run copied (~0.28s each; a typical deploy copies
# a handful). The hash is not paranoia: `fs cp` reporting success is a
# statement about the transport, not about the flash — a badge-side hiccup
# mid-write can leave a truncated (or, torn mid-overwrite, a stale
# same-size) file behind a clean exit code. On any mismatch the badge
# deletes the manifest itself — a manifest that vouches for bad bytes must
# not survive to be trusted — and the app is not started; the next run's
# hash walk repairs everything.
# refresh_apps makes AppManager re-read the manifests.
# The eviction is the actual restart fix — AppManager.execute_script only drops
# the *entrypoint* from sys.modules, so a relaunch re-imports the new foxtrot.py
# while its `import boss_radio` still hits the previous run's cached module.
# Match on __file__ to catch exactly ours, whatever they are named.
if [[ "$orphan_count" -gt 0 ]]; then
    phase "Pruning $orphan_count stale file(s), verifying + evicting..."
    sed 's/^/  - /' "$STAGE_ROOT/orphans.lst"
else
    phase "Install done; verifying + evicting..."
fi
{
    echo "import binascii, gc, hashlib, os, sys"
    echo "root = \"$APP_DIR\""
    echo "orphans = ["
    sed 's|.*|    "&",|' "$STAGE_ROOT/orphans.lst"
    echo "]"
    echo "copied = ["
    sed 's|.*|    "&",|' "$STAGE_ROOT/changed.lst"
    echo "]"
    cat <<PY
for p in orphans:
    try:
        os.remove(root + "/" + p)
    except OSError as e:
        print("could not remove", p, e)
if orphans:
    # Deepest-first, so a directory is only tried once its children are
    # gone. rmdir refuses a non-empty one, which is exactly the guard we
    # want.
    dirs = []
    stack = [root]
    while stack:
        p = stack.pop()
        for n in os.listdir(p):
            f = p + "/" + n
            if os.stat(f)[0] & 0x4000:
                dirs.append(f)
                stack.append(f)
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass
man = {}
try:
    with open(root + "/.deploy.sha") as fh:
        for line in fh:
            if line.startswith("#src "):
                continue
            parts = line.split()
            if len(parts) == 3:
                man[parts[2]] = (parts[0], int(parts[1]))
except OSError:
    pass
disk = {}
stack = [root]
while stack:
    p = stack.pop()
    for x in os.listdir(p):
        f = p + "/" + x
        st = os.stat(f)
        if st[0] & 0x4000:
            stack.append(f)
        elif f != root + "/.deploy.sha":
            disk[f[len(root) + 1:]] = st[6]
bad = []
if not man:
    bad.append("manifest missing or unreadable")
for r in man:
    if r not in disk:
        bad.append("missing: " + r)
    elif man[r][1] != disk[r]:
        bad.append("size: %s is %d bytes, manifest says %d" % (r, disk[r], man[r][1]))
for r in disk:
    if r not in man:
        bad.append("extra: " + r)
buf = bytearray(1024)
mv = memoryview(buf)
for r in copied:
    if r not in man or man[r][1] != disk.get(r, -1):
        continue  # already flagged above; hashing it would double-report
    h = hashlib.sha256()
    with open(root + "/" + r, "rb") as fh:
        while True:
            k = fh.readinto(buf)
            if not k:
                break
            h.update(mv[:k])
    if binascii.hexlify(h.digest()).decode()[:16] != man[r][0]:
        bad.append("hash: " + r)
if bad:
    try:
        os.remove(root + "/.deploy.sha")
    except OSError:
        pass
    for b in bad:
        print("BAD", b)
    print("VERIFY failed")
else:
    print("VERIFY ok %d %d" % (len(disk), len(copied)))
from mpos import AppManager
if $need_install:
    # only after a real install: re-reads every app's manifest, ~10s
    AppManager.refresh_apps()
stale = [k for k, m in sys.modules.items()
         if getattr(m, "__file__", "").startswith(root + "/")]
for k in stale:
    del sys.modules[k]
gc.collect()
print("EVICTED", len(stale), ",".join(sorted(stale)))
if $START and not bad:
    AppManager.start_app("$APP_ID")
    print("STARTED")
PY
} > "$STAGE_ROOT/exec2.py"
"${MP[@]}" run "$STAGE_ROOT/exec2.py" > "$STAGE_ROOT/verify.txt"
tr -d '\r' < "$STAGE_ROOT/verify.txt" > "$STAGE_ROOT/verify.lst"
if ! grep -q '^VERIFY ok' "$STAGE_ROOT/verify.lst"; then
    echo "error: post-install verification FAILED — the badge's copy does not match" >&2
    echo "       what this run staged:" >&2
    sed -n 's/^BAD /         /p' "$STAGE_ROOT/verify.lst" >&2
    echo "       The badge dropped its manifest; re-run this script to repair." >&2
    exit 1
fi
read -r v_files v_hashed <<< "$(sed -n 's/^VERIFY ok //p' "$STAGE_ROOT/verify.lst" | tail -1)"
phase "Verified $v_files files against the manifest ($v_hashed copied file(s) hash-checked)."
sed -n 's/^EVICTED /Evicted cached modules: /p' "$STAGE_ROOT/verify.lst"
if [[ "$START" -eq 1 ]]; then
    grep -q '^STARTED' "$STAGE_ROOT/verify.lst" \
        || { echo "error: app did not start on badge." >&2; exit 1; }
    echo "Started $APP_ID on badge."
fi

phase "Done."
