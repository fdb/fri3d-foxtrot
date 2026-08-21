---
name: release-to-badgehub
description: Prepare, audit, package, and document Foxtrot releases for BadgeHub. Use when asked to make a release, build or verify a bytecode-only .mpk, prepare a BadgeHub upload, check release readiness, bump or confirm a release version, or draft release notes from this repository's commits.
---

# Release Foxtrot to BadgeHub

Prepare the current manifest version as a reproducible, bytecode-only BadgeHub package. Automate facts and checks first; reserve conversation for judgments and external facts the repository cannot prove.

## Prepare the candidate

1. Read `CLAUDE.md` and preserve its radio, bytecode, hardware, and privacy constraints.
2. Run `scripts/prepare_badgehub_release.sh` from the repository root. Pass `--base <ref>` only when the user supplied a baseline or the detected baseline is clearly wrong. The script intentionally refuses dirty worktrees.
3. Let the script derive the version, app id, entrypoint, output name, commit, and notes range.
4. Fix mechanical repository failures authorized by the release request, then rerun the affected check and the complete release command. Never weaken or skip a check.
5. Read `dist/badgehub-release-<version>-context.md` completely.

The upload artifact is `dist/com.enigmeta.foxtrot_<version>.mpk`. Do not produce or suggest a source `.mpk`; BadgeHub releases from this repository are bytecode-only.

## Review the release

Read [references/release-review.md](references/release-review.md) completely and follow it. Inspect the actual contents of:

- `com.enigmeta.foxtrot/MANIFEST.JSON`;
- `scripts/build_mpk.sh` and `scripts/get_mpy_cross.sh`;
- `.github/workflows/build-mpk.yml`;
- the manifest entrypoint and `FoxtrotActivity`;
- `com.enigmeta.foxtrot/assets/trot_radio.py`;
- `foxhunt-spec-minimal.md`.

Inspect every changed file in the detected range at least at diff level. Open surrounding implementation for changes involving radio state, wire formats, persistence, startup, generated assets, or packaging. Separate conclusions into blockers, needs confirmation, and observations.

## Draft Dutch BadgeHub copy

Provide:

1. A one-sentence Dutch short description aimed at badge owners.
2. A concise Dutch paragraph headed `Release <version>:` that describes player-visible outcomes. Use concrete terms such as `vos`, `LoRa`, `vondstcode`, `ZENDT`, and `LUISTER` where useful.

Do not expose the shared secret, internal protocol details, commit hashes, test plumbing, or implementation trivia. Verify every claimed feature in the reviewed code or diff.

## Finish the release packet

Return:

- readiness and blockers;
- version, commit, baseline, artifact path, byte size, and SHA-256;
- automated check results and manual review conclusions;
- paste-ready Dutch BadgeHub copy;
- only the remaining external steps.

The script proves package structure and reproducibility, not runtime compatibility. Require final confirmation that the exact `.mpk` was installed and smoke-tested on a target Fri3d badge. For Foxtrot, check launch, the displayed vondstcode, ZENDT/LUISTER, creature selection, one real hunter claim followed by PROOF, persistence after restart, and that NeoPixels remain dark. Do not upload, tag, or publish until that confirmation exists unless the user explicitly accepts the hardware-testing risk.
