# Handoff: SX1262 radio bugs found in Foxtrot that fox-hunt and fox-boss share

Audience: whoever works on `fri3d-foxhunt` or `fri3d-foxboss` next. Everything
below was found and verified badge-to-badge in this repo (fri3d-foxtrot);
`com.enigmeta.foxtrot/assets/trot_radio.py` and `foxtrot.py` are the working
reference implementation for every fix.

## 1. The antenna switch is wrong in every app (TX barely radiates)

The badge's LoRa is a Seeed **Wio-SX1262** module (badge_2026_hw, RF
Expansion sheet). Its datasheet is explicit:

- TX/RX routing is decided **solely by the SX1262's DIO2 pin** (high = TX).
  The module hardwires DIO2 to the switch; the chip raises it by itself in
  TX mode when `setDio2AsRfSwitch(True)` is configured — which the driver's
  `begin()` already does.
- The module's RF_SW pin (badge GPIO46) only **gates the switch on**. It is
  not an RX-enable. Hold it high permanently. Never toggle it around a send:
  cutting the gate mid-transmit puts the PA into an open switch and locks
  the chip up within seconds.

The OS board file's comment ("high = enable receiver mode") is a misreading,
and `lora_chat`, fox-hunt's `lora.py` and fox-boss's `boss_lora.py` all copy
the same bug: `configure()` calls `self.radio.setDio2AsRfSwitch(False)`.
That pins the switch to the RX side forever. The chip then reports TX_DONE
for every packet while the PA fires into the receive path — RX works
perfectly, TX only reaches a receiver centimeters away on leakage. This is
why both apps' transmissions "work on the bench" but have no real range.

**Fix (one deletion per repo):** remove the `setDio2AsRfSwitch(False)` call
from `configure()`. Keep driving GPIO46 high; that part is right.

## 2. MeshCore drives the same chip from the background

The OS ships `org.fri3d.meshcore` with a boot service. When its per-badge,
persistent "background radio service" toggle is on (Me tab), a
`MeshCoreManager` runs at every boot, and its RX watchdog re-arms and
reconfigures the SX1262 underneath any other app. This OS release has **no**
`LoRaManager.acquire()` arbitration (that lands with MicroPythonOS#229) —
it is a free-for-all, and the symptom is maddening: random TX failures and
chip lockups on one badge while an identical badge is clean.

**Fix:** at radio start (and on activity resume), stop a running manager
through its public API — see `trot_radio._stop_meshcore()`. Check
`sys.modules.get("meshcore_manager")`; if the manager `is_running()`, call
`stop()`. Leave the user's persistent toggle alone.

## 3. Display DMA vs. the radio's manual-CS SPI (the contention fox-hunt knew about)

The ST7789 flush is asynchronous DMA on the same SPI host as the SX1262, and
the SX1262 driver holds CS low via GPIO across multiple `spi.write()` calls,
which the SPI host cannot arbitrate. The single-thread "poll, then draw"
discipline does NOT fully protect: the LVGL refresh timer can return while
its last flush DMA is still in flight, and the next tick's radio transaction
collides with it. Every unconditional `set_text()` keeps that window open —
Foxtrot went from ~72 % failed sends to ~0 with two UI changes:

- **Change-guard every widget write**: cache the last text/colour pushed per
  label and skip the LVGL call when unchanged (`foxtrot.py`, `_set()` /
  `_set_color()`). A steady-state tick must invalidate nothing.
- **Throttle high-churn areas**: Foxtrot's packet log repaints at most every
  500 ms no matter how fast packets arrive. Fox-hunt's hunt screen (RSSI
  meters updated every tick) and fox-boss's fox rows deserve the same audit:
  any label rewritten with an unchanged value, every tick, is bus pressure.

## 4. Two `_transmit()` bugs both apps inherited from the same ancestor

Both repos' `_transmit()` has this shape — and both halves hide a bug:

- **Deadline anchor.** Start the TX_DONE wait deadline **after** `send()`
  returns, not before. `send()` is a pile of SPI commands whose BUSY waits
  can eat 100+ ms on a busy bus; a deadline anchored before it expires with
  the wait loop never having run, so every real transmission is treated as
  failed — and the code then re-arms RX mid-flight or miscounts.
- **IRQ clearing eats received packets.** After the TX wait, the code calls
  `clearIrqStatus()` then `startReceive()`. If a frame landed during the
  wait (RX_DONE set alongside/instead of TX_DONE), the clear silently
  discards it. For a fox that is fatal — it eats the hunter's CODE_ENTRY
  retry exactly while answering the previous one. Check `IRQ_RX_ANY` before
  clearing and read the packet out first (see Foxtrot's `_transmit()`).

## 5. Diagnostics worth porting

- Count only chip-confirmed sends: success = `irq & TX_DONE`, and surface a
  failure line with the chip's own state — final IRQ word, `chip_mode()`,
  `getDeviceErrors()` (PLL lock = 0x0040 means an RF/antenna problem), and
  elapsed ms (≫ deadline means BUSY stalls). One log line separates
  "software raced the bus" from "the antenna is loose" in the field.
- TX power: with the switch fixed, the spec's +14 dBm holds. The historical
  "+14 wedges the chip" observations date from the PA firing into the wrong
  side of the switch — do not carry them forward.

## Verified end state in this repo

Two badges side by side: beacons decoded both directions at −20 dBm, a full
claim handshake captured in both packet logs, failure counters near zero
over hundreds of beacons. Wire format untouched — every
fix is bring-up, arbitration, or UI-side bus hygiene, so nothing here
changes protocol compatibility with fri3d-fox-firmware.
