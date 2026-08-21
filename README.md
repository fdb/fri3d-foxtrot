# Foxtrot

Turn a Fri3d 2026 badge into a **LoRa fox beacon** for the foxhunt game.

Normally a hunter chases dedicated fox transmitter hardware. Foxtrot is
the software stand-in: a MicroPythonOS app that

- **beacons** as a fox (spec §5: bursts of one BEACON every 250 ms,
  9.8 % duty),
- **answers claims itself**: CODE_ENTRY is validated on the badge — OTC
  match, RSSI ≥ −85 dBm proximity gate — and PROOF is issued directly
  (spec §6.1). The fox is the sole authority; no network, no WiFi.
- **shows the air**: every received packet with type, RSSI and validation
  verdict, next to the radio's own state and how many BEACONs the chip really
  clocked out.

The NeoPixels stay dark on purpose: a fox that glows can be found without a
radio. Nothing here lights up and nothing listens for other foxes.

The unmodified [fri3d-fox-hunt](../fri3d-fox-hunt) app completes a full
find against this beacon: read the 4-digit code off the Foxtrot screen,
type it in the hunter app, get PROOF.

## Run

```sh
scripts/run_on_mac.sh          # macOS SDL emulator (fake radio, scripted hunter)
scripts/deploy_to_badge.sh     # USB deploy to a badge; add --start to launch
scripts/format.sh              # Ruff + JSON formatting
```

The fox starts transmitting by itself, as **Vos** (CHAR 0). The main screen
keeps only the 4-digit claim code large and centred; the secret creature name
never appears there or in the packet log (BEACON entries show its numeric id).
Creature choice and the transmit switch live behind the small **INST.** button
so a stray tap cannot silence the fox. The creature chooser uses the same
rarity-coloured grid as Foxboss. Transmit always defaults to **AAN** when the
app starts; switching it off is deliberately temporary. The protocol spec is in
`foxhunt-spec-minimal.md`.

It transmits at the spec's +14 dBm. The footer shows failed sends and the
reset count — if either climbs, lower `TX_POWER` in `trot_radio.py` a step.

The fake radio runs on the emulator only. On a badge the app always drives
the real SX1262: if the chip does not answer, the footer says so and the app
keeps resetting and re-initialising it until it does.
