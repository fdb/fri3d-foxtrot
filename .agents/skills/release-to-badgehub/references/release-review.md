# Foxtrot BadgeHub release review

Record evidence for the applicable checks; this is a reasoning pass, not a second scripted gate.

## Package and metadata

- Confirm manifest `fullname` equals the app directory and archive top-level directory.
- Confirm the semantic version is intentional and newer than the published BadgeHub version when that external fact is available.
- Confirm the name, publisher, descriptions, category, icon, entrypoint, and class describe the shipped app.
- BadgeHub must be configured for `mpos_api_0`; this is an upload-form choice.
- Confirm the archive contains assets and `.mpy` bytecode but no Python source. Preserve `-march=xtensawin`, the firmware-matched compiler pin, and `-O3` store optimization.
- Treat a MicroPythonOS or MicroPython pin change as a blocker until compatibility with field firmware is established.

## Startup and badge behavior

- Trace the manifest entrypoint to `FoxtrotActivity` and its initial screen.
- Check imports renamed or added in the release, including behavior when optional OS modules are absent.
- Review `SharedPreferences` keys and defaults for upgrades and persistence.
- Treat FID layout, CHAR order, SEQ, CRC/PRF, OTC encoding, RSSI gate, LoRa frequency/modulation, and message types as durable protocol data. Accidental changes are blockers.
- Confirm hardware never falls back to `FakeTrotRadio`, beaconing defaults on after app restart, and NeoPixels remain dark.
- Review MeshCore coexistence and shared-SPI locking for regressions.
- Check file-count and LittleFS impact for added assets.

## Generated assets and cross-project coupling

- Confirm `com.enigmeta.foxtrot/icon_64x64.png` is regenerated from `artwork/foxtrot.png`.
- When creature ids/order or wire behavior changes, compare against the hunter app and other fox implementations.
- Confirm generated outputs are committed rather than relying on developer-only generation during packaging.

## Licensing and hygiene

- Verify new code and artwork are distributable and credited where needed.
- Scan changed files for TODO/FIXME markers, conflict markers, stale app ids or versions, temporary endpoints, debug-only behavior, commented-out safety checks, and unexplained binaries.
- Require a clean tracked and untracked worktree so the artifact is reproducible from its reported commit.

## Exact-artifact smoke test

Install the exact `.mpk` on target firmware and verify: launch, home/vondstcode, ZENDT/LUISTER, creature selection, one BEACON/CODE_ENTRY/PROOF exchange with the hunter app, persistence after restart, radio recovery diagnostics, and dark NeoPixels. Local automation cannot replace this confirmation.
