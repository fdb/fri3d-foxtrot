# Foxtrot — working notes

Foxtrot turns a Fri3d 2026 badge into a **LoRa fox** for the foxhunt
game, implementing `foxhunt-spec-minimal.md`: one fox, three message types
(BEACON out, CODE_ENTRY in, PROOF out), and the fox as sole authority — it
validates a claim itself (FID match, OTC match, RSSI proximity gate) and
answers PROOF directly. No network, no relay, no WiFi anywhere. The
unmodified Foxhunt hunter app completes a full find against this fox.

It is also a debug tool: everything received is shown with RSSI and a
validation verdict, next to the radio's own state and the count of BEACONs
the chip really clocked out.

**It transmits or it listens, and it never glows.** The protected settings
screen toggles ZENDT/UIT; the radio sits in continuous RX either way, and the
log shows every frame heard — decoded spec traffic, raw hexdump for the rest,
CRC failures named. The NeoPixels stay dark: a fox that lights up is findable
without a radio. Every count on screen is a fact, never an intention;
`_transmit()` returns whether TX_DONE actually landed, only then does the
beacon counter move, and a failed TX logs the chip's own diagnosis (IRQ word,
mode, device errors, elapsed ms).

**The main app is [fri3d-fox-hunt](../fri3d-fox-hunt)** (usually at
`~/Projects/fri3d-fox-hunt`) — the player app that hunters run. Read its
`CLAUDE.md` first: the emulator/deploy discipline there (MicroPythonOS
checkouts, killing emulators, badge serial rules) applies here unchanged.
This repo is deliberately small — no accounts, no server, no personas.

## What lives where

- `com.enigmeta.foxtrot/assets/foxtrot.py` — three screens in the dark
  command-deck flip of Foxhunt's design language: a display-only home with only
  a large centred claim code, protected settings, and fox-boss's 3-wide
  creature grid. The creature name is secret: home never shows it and BEACON
  log lines expose only the numeric CHAR id. Home has only one small `INST.`
  action, without an always-visible default focus ring; creature and transmit
  controls are one screen deeper so a stray tap cannot silence the fox. Every
  screen keeps polling the radio while it is foregrounded.
- `com.enigmeta.foxtrot/assets/trot_radio.py` — everything radio: the
  SX1262 bring-up path proven in fox-hunt's `lora.py` (busy-timeout
  monkeypatch, RF switch, expander reset, health checks), the spec §3 wire
  format, and the §6.1 claim handling. `RADIO` is the singleton.
- `foxhunt-spec-minimal.md` — the single-beacon protocol spec this app
  implements. **The full multi-fox spec must never enter this repo or its
  git history** — it is security-sensitive for the live game. This is the
  only spec document that belongs here.
- `artwork/foxtrot.aseprite` + `foxtrot.png` — the 16×16 pixel-art source of
  the launcher icon. `uv run scripts/make_icon.py` scales it 4× with
  nearest-neighbour into `com.enigmeta.foxtrot/icon_64x64.png`. Edit the art,
  run the script; never touch the generated PNG by hand.
- `scripts/run_on_mac.sh` — SDL emulator (live symlink; edits show on next
  run). `scripts/deploy_to_badge.sh` — USB deploy. `scripts/format.sh` —
  Ruff + json.tool.
- `docs/radio-handoff.md` — the cross-repo bug handoff for fox-hunt and
  fox-boss.

## Bring-up is fox-boss's, and it must stay that way

`start()` → `bring_up()` → `configure()` → `verify()` is a port of
fri3d-foxboss's `boss_lora.py`, which is the most reliable radio code on this
badge. Two things about it are load-bearing:

**No presence probe.** The driver object existing is enough to call `begin()`.
An SX1262 that has not been configured yet answers `getPacketType()` with
`0xFF` on this badge — the same value an empty bus gives — so a probe gate
refuses to start a radio that is fitted and healthy. That gate cost 24 rounds
of bring-up on a working badge without ever reaching `begin()` once.
`verify()` (mode=RX, sync=1424, devErr=0) is what proves the chip is there.

**One reset between attempts, not before the first.** `bring_up()` tries
three times, pulsing the CH32 expander between tries.

On top of fox-boss, this app retries the whole three-attempt round forever
while the radio is not ready (backing off 2 s → 10 s), because a fox with no
transmitter has nothing else to do. fox-boss stops instead — a game master
can leave the screen and come back.

## The antenna switch, and TX power

The LoRa module is a Seeed Wio-SX1262 (badge_2026_hw, RF Expansion sheet).
Its datasheet: TX/RX routing is decided **solely by DIO2** (high = TX, the
module hardwires it to the switch), and RF_SW/GPIO46 only **gates the switch
on** — hold it high always. So `configure()` keeps `begin()`'s
`setDio2AsRfSwitch(True)`; calling `setDio2AsRfSwitch(False)` — as the OS
board file, lora_chat, fox-hunt and fox-boss all do — pins the switch to the
RX side: TX_DONE reports success while the PA fires into the receive path and
only close-range leakage radiates. And never toggle GPIO46 around a send:
cutting the switch gate mid-transmit locks the chip up within seconds.

The RX→TX boundary is explicit. The pinned Python driver omits the
`standby()` that current RadioLib performs before `startTransmit()`, so
Foxtrot enters STDBY_RC itself and reaffirms
`setDio2AsRfSwitch(True)` before every send. That argument means automatic
switch control (DIO2 high only while the chip is in TX), not a permanently
high DIO2. A final tri-state RX-latch check drains a valid packet and defers
TX on a suspect read, because TX and RX share buffer base 0 and the driver's
send path clears all IRQs.

Switching LUISTER→ZENDT starts a fresh beacon burst. Do not reuse the old
burst clock: it may currently be in `T_SILENT`, which makes a newly enabled
transmitter look dead even though it is only waiting out the old cycle.

`TX_POWER = 14` dBm, spec §7's fox BEACON power. The lockups that once
forced it down to +4 happened while the PA fired into the wrong side of the
switch; with the switch right, +14 holds. If `mislukt` or the reset count in
the footer climbs, drop back a step.

## MeshCore is an optional conflicting owner

The OS ships `org.fri3d.meshcore` with a boot service: when its "background
radio service" toggle is on (per badge, persistent, set from its Me tab), a
`MeshCoreManager` drives this same SX1262 from the background — its RX
watchdog re-arms and reconfigures the chip underneath whoever else uses it,
and this OS release has no `LoRaManager.acquire()` arbitration. The symptom
is random TX failures and lockups on one badge while an identical badge is
clean. Foxtrot's compatibility check only looks in `sys.modules`; it neither
requires nor imports/installs MeshCore. If an already-loaded manager reports
itself running, Foxtrot asks it to stop and leaves the user's persistent
toggle untouched. This is not OS-level arbitration: current MeshCore can
still have delayed work after `stop()` returns. A general solution belongs
in `LoRaManager.acquire()/release()`, not in another app-specific import.

## Creature persists; transmit defaults on

`SharedPreferences("com.enigmeta.foxtrot")` stores `char`, so a configured
creature survives restarts. Beaconing deliberately does not: every app start
begins with transmit enabled. A temporary bench listener therefore becomes a
findable fox again after restart, even if it was switched off accidentally.

## The fake radio is desktop-only

`make_radio()` chooses on `sys.platform`, not on whether the chip answered.
On a badge the app is always the real `TrotRadio`: if the SX1262 does not
answer it says so on screen and keeps kicking it — `begin()` → `verify()`,
with a CH32 expander reset between attempts, backing off to 10 s and never
giving up. A `FakeTrotRadio` on hardware is
the worst possible failure: the screen reads "ZENDT" with a rising beacon
count while nothing is on the air.

Fox-hunt deliberately does NOT reset on its own (its `lora.py` notes that
pulsing the shared expander on a cold boot can restart the badge; it waits
for the player to pick WORD JAGER). Foxtrot is a bench tool with one job, so
here the loop is automatic. If a badge ever restarts itself while Foxtrot
sits on "chip antwoordt niet", that trade is why.

## Identity: CHAR is the creature, SEQ is fixed

One FID byte carries both (spec §2.1): 5 bits CHAR, 3 bits SEQ. CHAR is
what a hunter sees as a creature — `CREATURE_NAMES` mirrors fox-hunt's
`creatures.py` roster in id order, and the fox starts at CHAR 0, **Vos**.
SEQ is fixed at 1 in this deployment (§2.1); nothing changes it. Identity
changes through the settings screen's creature grid only — the minimal spec
has no over-the-air reconfiguration path (§6.2).

## App layout: flat, at the app root

`MANIFEST.JSON` and `icon_64x64.png` sit at the app root. This is the layout
MicroPythonOS prefers: `mpos/app/app.py` probes `apps/<fullname>/MANIFEST.JSON`
and `apps/<fullname>/icon_64x64.png` first, and only then falls back to the
nested `META-INF/` and `res/mipmap-mdpi/` paths, logging a deprecation warning
when it does.

Foxtrot targets current firmware only. Do not add the nested directories back
for older builds — a badge that predates root-layout support cannot launch
this app, and that is deliberate.

## The emulator is finicky

It can crash or hang without telling you. Always run it with a hard
timeout and always verify it is gone afterwards: `pgrep -fl
lvgl_micropy_macOS`, then `kill -9` what survives — SIGTERM is often not
enough, and it never exits on stdin EOF. Kill only PIDs you started
(`pkill -f` is machine-wide). Full recipe: fri3d-fox-hunt's
`docs/emulator-testing.md`.

## Module naming

MicroPythonOS caches modules by bare name in one `sys.modules`, shared
across apps — two apps each shipping a `lora.py` interfere. Every module in
this app carries an app-unique name (`foxtrot`, `trot_radio`); never add a
generically named module here.

## Keep in sync

The wire format, CRC-8/PRF, OTC display encoding, RSSI gate and shared
secret `K` (placeholder `0x5A`) must match the hunter ecosystem
(fox-hunt's `lora.py`). The Pixelify fonts in `assets/fonts/` are copies
from fox-hunt.

## Beacon timing is the difficulty dial

`T_BCN`/`T_BURST`/`T_SILENT` (spec §5) shape the burst/silent alternation.
Our `T_SILENT = 4000 ms` keeps duty at 9.8%, under the EU 10% band limit —
the spec's own default (250 ms) sits at ~12% and says to reach for
`T_SILENT` where rules are stricter. Stretching `T_SILENT` further is how a
deployment makes the hunt harder.
