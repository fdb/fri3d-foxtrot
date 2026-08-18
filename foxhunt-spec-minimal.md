# FoxHunt — LoRa Fox Hunting Game
## Technical Specification — single-beacon minimal

Target hardware: any microcontroller paired with a LoRa radio module, for both
the fox and hunter devices. Nothing in this spec depends on a specific board or
radio part — only on the radio parameters in §4 being configurable and an RSSI
reading being available per received packet.

---

## 1. Overview

**One fox** (beacon) transmits short LoRa bursts on a single shared frequency.
**Hunters** carry receivers, locate the fox by RF direction finding, and prove
proximity via a low-power code exchange directly with the fox. The fox validates
each claim itself and replies immediately — there is no network to relay through,
nothing to synchronise with, and nothing else that needs to agree on the outcome.

```
 Hunter ──(low power, proximity)── Fox
   │  CODE_ENTRY ──────────────►│  validate OTC + RSSI
   │◄──────────── PROOF ────────│  (issued directly, no relay/ACK)
```

All nodes share one frequency and one set of radio parameters (see §4).

---

## 2. Addressing

### 2.1 Fox ID byte (`FID`)

One byte identifies the fox:

```
  bit 7 6 5 4 3 | 2 1 0
      CHAR      | SEQ
```

| Field | Bits | Meaning |
|---|---|---|
| `SEQ`  | 3 (LSB) | Fixed at `1` for the single fox in this deployment. |
| `CHAR` | 5 (MSB) | Character code 0–31 (fox, cat, dog, penguin, …). Reprogrammable in the field. |

The bit layout is kept identical to the multi-fox format so firmware and hunter
displays don't need to change if the deployment later grows a second fox. With
only one fox, `SEQ` never varies and there is no slot/central meaning to it.

Character table (game-defined, example): `0=fox, 1=cat, 2=dog, 3=penguin, …`. This
mapping is cosmetic and lives with the game master, not on the fox: the fox's own
display shows the raw `CHAR` id (`C<n>`), never a name.

`SEQ` is fixed; `CHAR` is the only reprogrammable field. Reprogramming the fox
(§6.1) changes `CHAR` only.

### 2.2 Hunter ID (`HID`)

**Two bytes** (big-endian in all payloads: `HID_hi`, `HID_lo`), assigned at
registration (1–65535; 0 reserved). How a deployment assigns `HID` (serial
console, dip switches, a companion app, etc.) is out of scope for this document —
this spec only covers the LoRa wire format and how the fox uses `HID` once
assigned.

### 2.3 Shared secret (`K`)

One secret byte, provisioned on the fox (never transmitted over the air). `K` is
mixed into the proof CRC (§3.3) so a PROOF cannot be trivially forged just by
reverse-engineering the over-the-air format. This is deliberately *not*
cryptographically strong — a determined attacker with the fox in hand could
extract it — it just raises the bar above passive sniffing. Hunter devices do
**not** hold `K`.

---

## 3. Message formats

All messages use LoRa explicit header + CRC; payloads are as below. First byte is
always `TYPE` (high nibble = type, low nibble reserved/unused unless noted).

| TYPE | Name | TX power | Length | Airtime |
|---|---|---|---|---|
| `0x1` | BEACON | high | 2 B | 31.0 ms |
| `0x2` | CODE_ENTRY | low (hunter) | 5 B | 31.0 ms |
| `0x3` | PROOF | low (fox) | 5 B | 31.0 ms |

Airtimes are per Semtech AN1200.13 at the §4 parameters.

### 3.1 BEACON — fox burst payload

```
 byte 0: 0x10
 byte 1: FID
```

Sent repeatedly (§5). Format unchanged from the multi-fox spec.

### 3.2 CODE_ENTRY — hunter → fox (low power)

```
 byte 0: 0x20
 byte 1: FID        (target fox, as read from display/beacon)
 byte 2: HID_hi
 byte 3: HID_lo
 byte 4: OTC        (one-time code, the raw byte 0–255)
```

`OTC` on the wire is the raw byte. What the fox *displays* and the hunter *types*
is a 4-digit checksummed rendering of that byte (§7); the hunter device converts
before transmitting. Nothing in the protocol sees the 4-digit form. Format
unchanged from the multi-fox spec.

### 3.3 PROOF — fox → hunter (low power)

```
 byte 0: 0x30
 byte 1: FID
 byte 2: HID_hi
 byte 3: HID_lo
 byte 4: PRF        (proof byte: CRC-8 of K‖FID‖HID_hi‖HID_lo, poly 0x07)
```

Issued **immediately** once the fox validates the CODE_ENTRY (§6.1) — there is no
central or cloud to check with, so validation and reply happen on the fox in a
single step. The hunter device stores `(FID, PRF)` as its local proof of the find;
this fox is the sole authority on whether a find happened.

Reliability is bought with **`N_PROOF = 3` total transmissions at a short, fixed
gap**, not a handshake: the hunter has no key to authenticate a request for one.
Each repeat goes out `PROOF_REPEAT_MS` after the last, bounded at `3·T_BCN`, so
all three transmissions finish in well under a second.

---

## 4. Radio parameters (fixed)

| Param | Value |
|---|---|
| Frequency | 869.4625 MHz (low edge of EU P-band, 10 % duty; isolates cleanly from Meshtastic) |
| SF / BW / CR | SF7 / 125 kHz / 4:5 (coding rate expressed as numerator:denominator — check which convention your radio library expects) |
| Preamble | 8 symbols |
| Sync word | private (0x12) |
| CRC | on |
| Airtime | 31.0 ms for 2–5 B payloads |
| HP TX power | +14 dBm (config) |
| LP TX power | −9 dBm (config; the practical minimum on most modules; tune so range ≈ 5–10 m) |
| `RSSI_MIN` | −85 dBm (config; CODE_ENTRY proximity gate, see §6.3) |

---

## 5. Beacon timing

With a single fox there is no slot schedule, no phase to acquire, and nothing to
synchronise against — but the fox still alternates a **burst** of beaconing with
a **silent** period, the same two-tier shape as the multi-fox schedule. There's
just nothing else on the channel for those two tiers to coordinate against.

| Const | Default | Meaning |
|---|---|---|
| `T_BCN` | 250 ms | Interval between individual BEACON packets *within* a burst |
| `T_BURST` | 15 s | How long the fox beacons continuously before going quiet |
| `T_SILENT` | 250 ms | How long the fox stays quiet between bursts |

The fox repeats BEACON every `T_BCN` for `T_BURST`, then goes silent for
`T_SILENT`, then starts the next burst — indefinitely. **`T_SILENT` defaults to
the same value as `T_BCN`**, so at defaults the seam between one burst and the
next is the same length as any other gap between beacons — the burst/silent
split is there structurally, but nothing about default operation actually
looks different from continuous transmission. The fox listens for CODE_ENTRY in
every gap, burst or silent, so it's always reachable either way.

Overall duty ≈ `(T_BURST / (T_BURST + T_SILENT)) × (airtime / T_BCN)`. At the
defaults that's ≈12 %, which comfortably fits typical regional duty-cycle
allowances for this kind of short, low-duty telemetry traffic; if your local
regulations are stricter, check the math against them before deploying.

**Tuning `T_BURST` and `T_SILENT` sets the difficulty level.** They're the two
levers, and they push in different ways:

- **`T_SILENT` — the main difficulty lever.** This is how long the fox goes
  completely dark between bursts. At the 250 ms default it's imperceptible;
  stretch it to seconds or minutes and hunters get a burst of bearings, then a
  long wait with nothing to home in on, forcing them to remember and
  triangulate from sparse data instead of just walking up a constant signal.
  Growing `T_SILENT` also lowers overall duty, so it's the knob to reach for if
  local duty-cycle rules are tighter than the default assumes.
- **`T_BURST` — how much hunters get per "window."** Once `T_SILENT` is long
  enough to matter, `T_BURST` controls how much usable signal shows up when the
  fox does wake up. A short burst gives hunters only a brief chance to get a
  fix before it goes quiet again, compounding the difficulty of a long
  `T_SILENT`; a long burst is more forgiving, letting them take their time
  during each active window. `T_BURST` alone (with `T_SILENT` left at default)
  doesn't change difficulty much, since the seam back into the next burst stays
  invisible.

There's no lower bound on either besides `T_BCN`/airtime itself, and no fixed
upper bound — a long enough `T_SILENT` with a short `T_BURST` turns the hunt
into a real endurance/patience exercise.

---

## 6. Fox behaviour

### 6.1 CODE_ENTRY handling

Accepted any time the fox is not mid-transmission. On receipt:

1. Validate `FID` matches self, `OTC` matches the currently displayed code, and
   packet RSSI ≥ `RSSI_MIN` (§6.3).
2. On success: reply PROOF (§3.3) immediately, rotate the OTC.
3. On mismatch or weak RSSI: silently ignore (indistinguishable from a wrong
   code, so no information leaks).

The OTC byte is regenerated (random, seeded from RF noise) after every
successful PROOF, and otherwise every 10 minutes — never while a claim is being
answered, which would change the code under the hunter mid-verification.

### 6.2 Display / reprogramming

The fox's display shows the raw `CHAR` id (`C<n>`) + the current 4-digit code
(§7) + a claim indicator (FOUND!). Game masters set `CHAR` and the beacon timing
constants (`T_BCN`, `T_BURST`, `T_SILENT`, §5) directly on the device (serial
console, on-device menu, whatever the hardware supports); changes take effect
on the next beacon. There is no over-the-air reconfiguration path — retuning
the fox is always done on the device itself.

### 6.3 Proximity check (RSSI gate)

To prevent a hunter radioing a code to a distant friend, the fox reads the RSSI
of each CODE_ENTRY packet and requires `RSSI ≥ RSSI_MIN` (default **−85 dBm**,
configurable per deployment). Combined with the hunter's low TX power (−9 dBm),
this bounds the claim radius to roughly 5–15 m line-of-sight. Calibrate
`RSSI_MIN` on site: hold a hunter device at the maximum acceptable distance and
set the threshold ~5 dB above the measured value.

---

## 7. Hunter device

Any LoRa-capable receiver with a small display/input (or a phone bridging over
BLE — out of scope here). RX-only for direction finding (RSSI of BEACONs). The
device converts the typed 4-digit code to the OTC byte before transmitting, and
must apply the checksum check below.

### 7.1 CODE_ENTRY / PROOF UX

After sending CODE_ENTRY, wait for PROOF (typically well under a second, since
the fox answers directly, no relay hop). If nothing arrives within ~1 s, retry
CODE_ENTRY (same code) up to 5× at 1 s intervals, then tell the user to re-check
the code or move closer.

### 7.2 One-time code display encoding

The OTC is a byte on the wire (§3.2). What a human reads and types is a 4-digit
rendering with a checksum digit, which makes mistyping self-detecting and makes
the code space look larger than 256:

```
 d1 = bits 7-5, +2   -> 2..9        d3 = bits 2-0, no offset -> 0..7
 d2 = bits 4-2, +1   -> 1..8        d4 = b1 ^ b2 ^ b3        -> 0..7
```

Codes span **2100–9877** and never lead with 0 or 1. Two measured properties:

- **Every single-digit typo is rejected** — all 9216 substitutions across all 256
  codes fail the checksum. This is the property that matters for a keypad.
- **512 codes validate, but only 256 are canonical.** Bit 2 appears in both `d2`'s
  field (as its LSB) and `d3`'s (as its MSB), and decoding ORs them, so a code
  where those disagree still passes the checksum and decodes to a byte that
  re-encodes differently (e.g. `2144` → byte 4, but byte 4 encodes as `2245`).
  None is reachable by a single typo, but two errors could land on one. **The
  hunter device must decode, re-encode, and reject any mismatch** rather than
  trusting the checksum alone.

---

## 8. Startup procedure

The fox powers up and immediately starts beaconing on the schedule set by
`T_BCN`/`T_BURST`/`T_SILENT` (§5) — there is nothing to wait for and nothing to
acquire. Hunter devices are RX-only until a find; no registration handshake
with the fox is needed.

Mid-game `CHAR` or beacon-timing changes are made directly on the device (§6.2)
and take effect on the fox's next beacon.

---

## 9. Out of scope / future

- Real cryptographic signing (OTC + proximity + shared-secret PRF is the
  anti-cheat for v1; `K` deters casual forgery, not a determined attacker).
- Multiple foxes, slot scheduling, mesh relay, and a central/heartbeat node —
  deliberately removed from this document; reintroducing them is a larger
  design that would bring back synchronisation and relay concerns this spec
  avoids entirely.
- Over-the-air reconfiguration of the fox — done on-device instead.
- Cloud scoreboards / hunter registration infrastructure — how `HID` is
  assigned and how finds are recorded beyond the fox's own memory is left to
  the deployment.
- The hunter device firmware in full. The wire formats (§3) and the display
  encoding (§7.2) are self-contained, so it needs only those plus the §7.1 UX.
- GPS, absolute time, RSSI reporting — deliberately excluded.
