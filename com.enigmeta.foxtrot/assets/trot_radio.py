# trot_radio.py — the badge as LoRa fox. Everything that touches the SX1262
# or the wire format from foxhunt-spec-minimal.md lives here; foxtrot.py only
# draws what this module reports.
#
# ONE FOX, SOLE AUTHORITY. The protocol is the single-beacon minimal spec:
# three message types on the air — BEACON out, CODE_ENTRY in, PROOF out —
# and the fox validates every claim itself:
#   1. FID matches self (§6.1)
#   2. OTC matches the code on our display (§6.1)
#   3. proximity gate: packet RSSI >= RSSI_MIN (§6.3)
# On success PROOF goes straight back (§3.3) and the code rotates. There is
# no network, no relay, nothing else that needs to agree on the outcome. The
# unmodified Foxhunt hunter app completes a full find against this fox.
#
# BURST / SILENT (§5). The fox alternates T_BURST of beaconing (one BEACON
# every T_BCN) with T_SILENT of dark, listening for CODE_ENTRY in every gap
# either way. T_SILENT is the difficulty lever — and the duty-cycle lever:
# our default keeps the fox's share of the air under the EU 10% band limit
# (see the constants).
#
# ONE POWER. The spec beacons at +14 dBm and whispers PROOF at low power so
# replies don't carry past the claim radius. This app configures TX_POWER
# once and keeps it: the proximity that matters is enforced on OUR RX side
# (the RSSI gate on CODE_ENTRY, which the hunter sends at low power) — a
# loud PROOF is harmless, and a per-packet power swap is not worth its
# bring-up risk here.
#
# NO THREADS, NO INTERNAL POLLING LOOP. Same rule as fox-hunt's lora.py, for
# the same reason: the SX1262 driver shares an SPI host with the ST7789
# display, so every radio touch must happen inside lv_timer_handler(), from
# the screen's own tick, before that tick draws anything. poll() does one
# round — read a waiting packet, or health-check, then TX a beacon if one is
# due, then tick the pending PROOF repeats — and returns.
#
# NEVER FAKE ON HARDWARE. FakeTrotRadio exists for the desktop emulator and
# nowhere else: make_radio() picks it on sys.platform, not on whether the chip
# answered. A badge whose SX1262 stays silent says so on screen and keeps
# kicking it (reset -> begin -> verify, forever, on timers). A synthetic
# beacon counter on a badge that is not on the air is worse than no app.

import time

# ─────────────────────────── radio parameters (spec §7, all nodes fixed) ───
FREQ_MHZ = 869.4625
BW_KHZ = 125.0
SF = 7
CR = 5  # 4/5; the driver takes the denominator, RadioLib convention
SYNC_WORD = 0x12  # expands to 0x1424, RadioLib's private sync word
PREAMBLE = 8
TCXO_V = 3.0  # fri3d_2026 board TCXO
CURRENT_LIMIT = 140.0  # the known-working bring-up value shared by fox-hunt,
# fox-boss and the badge's own lora_chat.
TX_POWER = 14  # dBm — spec §7's fox BEACON power. Safe now that the antenna
# switch is driven correctly (see configure()): the PA has a real load. The
# lockups that once forced this down to +4 happened while the PA fired into
# the wrong side of the switch. If "mislukt" or the reset count in the footer
# climbs at this level, drop back a step — those two numbers say whether the
# chip is still with you.

RF_SW_PIN = 46  # fri3d_2026: gates the Wio-SX1262's internal antenna
# switch; high = switch enabled, ALWAYS. TX/RX routing is DIO2's alone.
REG_LORA_SYNC_WORD_MSB = 0x0740
PACKET_TYPE_LORA = 0x01
MODE_RX = 5
CHIP_MODES = {2: "STBY_RC", 3: "STBY_XOSC", 4: "FS", 5: "RX", 6: "TX"}

SETTLE_MS = 1000  # let the activity transition finish before touching SPI
MODE_CHECK_MS = 1000
MAX_REJECTS_BEFORE_RESET = 20
MAX_CONSECUTIVE_TX_FAILURES = 4
TX_DEADLINE_MS = 120  # generous vs. 31.0 ms airtime (§3) for TX_DONE to land
SPI_BUSY_TIMEOUT_MS = 300  # see fox-hunt lora.py: the driver's default is 5 s

# Bring-up retry loop (see _attempt). A wedged SX1262 on this badge often
# needs a reset pulse to answer at all, so the loop escalates: a plain
# begin() first, a reset before every attempt after that. It never gives up —
# a fox that cannot transmit has nothing else to do — but it backs off, so a
# badge with no daughterboard fitted is not pulsing the shared CH32 expander
# twice a second for the rest of the session.
RETRY_MS = 2000
RETRY_MAX_MS = 10000
RESET_HOLD_MS = 200
RESET_RELEASE_MS = 200

# Bit values taken from sx1262.py (kept local rather than importing the
# underscore-prefixed constants — same convention as fox-hunt).
IRQ_RX_ANY = 0b0000000010 | 0b0000100000 | 0b0001000000  # RX_DONE|HEADER_ERR|CRC_ERR
IRQ_TX_DONE = 0b0000000001
IRQ_TX_ANY = IRQ_TX_DONE | 0b1000000000  # TX_DONE|TIMEOUT

RX_EMPTY = 0
RX_READY = 1
RX_SUSPECT = 2

# CH32 expander config byte (fri3d_2026 only):
#   bit 4 = LoRa reset (1 = released)   bit 1 = LCD reset   bit 0 = AUX 3v3
EXPANDER_LORA_HELD = 0x03
EXPANDER_LORA_RUN = 0x13

# ─────────────────────────── beacon + claim timing (spec §5, §6) ────────────
T_BCN = 250  # ms between BEACONs within a burst (§5 default)
T_BURST = 15000  # ms of beaconing before going quiet (§5)
T_SILENT = 4000  # ms of dark between bursts. The spec defaults this to
# T_BCN (invisible seam, ~12% duty) and names it the knob for stricter duty
# rules; the EU P-band allows 10%, so we hold 15/19 of 31/250 = 9.8%. It is
# also the difficulty lever: stretch it and hunters must triangulate from
# sparse bursts instead of walking up a constant signal.
RSSI_MIN = -85.0  # CODE_ENTRY proximity gate (§6.3)
K = 0x5A  # shared secret byte (§2.3), mixed into PRF; never on the air.
# Placeholder until provisioning exists — must match the hunter ecosystem.
N_PROOF = 3  # total PROOF transmissions per claim (§3.3)
PROOF_REPEAT_MS = T_BCN + 40  # repeat spacing, clear of the beacon comb (§3.3)
T_OTC_ROTATE = 600000  # 10 min idle rotation (§6.1)

LOG_KEEP = 7  # RX debug lines the UI can show

# ───────────────────────── creature names (CHAR, spec §2.1) ─────────────────
# The CHAR half of the FID is the creature a hunter sees. Names come from
# fox-hunt's creatures.py roster, in id order; they are display only — the
# wire never carries a name. CHAR 0 (Vos) is what this beacon starts as.
CREATURE_NAMES = (
    "Vos",
    "Egel",
    "Kat",
    "Axolotl",
    "Capybara",
    "Koe",
    "Hond",
    "Eend",
    "Kip",
    "Koala",
    "Konijn",
    "Varken",
    "Knoricorn",
    "Glitch Vos",
    "Party Vos",
    "Zwarte Vos",
    "Everzwaan",
    "Kameleeuw",
    "Koekoekoek",
    "Konijlpaard",
    "Slakamander",
    "Tijghert",
    "Aap",
    "Giraf",
    "Papegaai",
    "Dolfenix",
    "Kraaiken",
)


def creature_name(char):
    if 0 <= char < len(CREATURE_NAMES):
        return CREATURE_NAMES[char]
    return "char %d" % char


# ───────────────────────────── wire format (spec §3) ────────────────────────
TYPE_BEACON = 0x1
TYPE_CODE_ENTRY = 0x2
TYPE_PROOF = 0x3

TYPE_NAMES = {
    TYPE_BEACON: "BEACON",
    TYPE_CODE_ENTRY: "CODE",
    TYPE_PROOF: "PROOF",
}

# Payload length per type (§3 table); parse rejects anything else — an
# unknown or wrong-length frame still shows in the log, as a raw hexdump.
TYPE_LEN = {
    TYPE_BEACON: 2,
    TYPE_CODE_ENTRY: 5,
    TYPE_PROOF: 5,
}


def make_fid(seq, char):
    """FID byte per spec §2.1: 5 bits character, 3 bits slot/sequence."""
    return ((char & 0x1F) << 3) | (seq & 0x07)


def fid_seq(fid):
    return fid & 0x07


def fid_char(fid):
    return (fid >> 3) & 0x1F


def crc8(data):
    """CRC-8, poly 0x07, init 0, MSB-first — the spec's §3.4 proof CRC,
    bit-for-bit the firmware's crc8()."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def prf_compute(k, fid, hid):
    """PRF over K‖FID‖HID_hi‖HID_lo (§3.4). Excludes OTC and EVT on purpose:
    stable per (fox, hunter) pair."""
    return crc8(bytes([k, fid, (hid >> 8) & 0xFF, hid & 0xFF]))


def build_beacon(fid):
    return bytes([TYPE_BEACON << 4, fid & 0xFF])


def build_proof(fid, hid, prf):
    return bytes(
        [TYPE_PROOF << 4, fid & 0xFF, (hid >> 8) & 0xFF, hid & 0xFF, prf & 0xFF]
    )


def parse(msg):
    """dict for any spec §3 message, or None for anything else — the caller
    logs unparsed frames as raw hexdumps, so nothing heard is hidden."""
    if len(msg) < 2:
        return None
    kind = (msg[0] >> 4) & 0xF
    if TYPE_LEN.get(kind) != len(msg):
        return None
    m = {"type": kind, "flags": msg[0] & 0x0F, "fid": msg[1]}
    if kind in (TYPE_CODE_ENTRY, TYPE_PROOF):
        m["hid"] = (msg[2] << 8) | msg[3]
    if kind == TYPE_CODE_ENTRY:
        m["otc"] = msg[4]
    elif kind == TYPE_PROOF:
        m["prf"] = msg[4]
    return m


def hexdump(pkt):
    return " ".join("%02X" % b for b in pkt)


# ────────────────────── OTC display encoding (spec §6.4) ────────────────────
def otc_to_code(otc):
    """Raw byte -> the 4-digit checksummed code a hunter reads and types."""
    b1 = (otc >> 5) & 0x7
    b2 = (otc >> 2) & 0x7
    b3 = otc & 0x7
    return (b1 + 2) * 1000 + (b2 + 1) * 100 + b3 * 10 + (b1 ^ b2 ^ b3)


def random_otc():
    try:
        import os

        return os.urandom(1)[0]
    except Exception:
        import random

        return random.randint(0, 255)


def describe(m, rssi):
    """One debug-log line for a parsed packet."""
    name = TYPE_NAMES.get(m["type"], "?%X" % m["type"])
    if m["type"] == TYPE_BEACON:
        # Creature names are secret. The debug log exposes only the numeric
        # CHAR id needed to diagnose what was actually received on the wire.
        return "%s s%d id%d %ddBm" % (
            name,
            fid_seq(m["fid"]),
            fid_char(m["fid"]),
            round(rssi),
        )
    if m["type"] == TYPE_CODE_ENTRY:
        return "%s hid%d otc%d %ddBm" % (name, m["hid"], m["otc"], round(rssi))
    return "%s hid%d prf%02X %ddBm" % (name, m["hid"], m["prf"], round(rssi))


# ───────────────────────────────── the link ─────────────────────────────────
class TrotRadio:
    """Real SX1262 backend. Bring-up, health checking and recovery are the
    proven path from fox-hunt's lora.py; only the traffic differs (we beacon
    and serve claims instead of hunting)."""

    def __init__(self):
        self.radio = None
        self.rf_sw = None
        self.is_fri3d = False
        self.available = False  # a radio chip answered a read
        self.ready = False  # configured, verified, in continuous RX
        self.status = "wachten"  # one short line for the UI, always current
        self.attempts = 0
        self.resets = 0

        self.seq = 1
        self.char = 0  # CHAR 0 = Vos (creature_name)
        self.otc = random_otc()
        self.otc_at = time.ticks_ms()
        self.beaconing = True
        self.beacons_sent = 0  # BEACONs the chip actually clocked out
        self.tx_fails = 0
        self.rx_count = 0
        self.note = ""  # claim status for the UI: "" or FOUND!

        self._last_beacon = 0
        self._last_health_check = 0
        self._consecutive_rejects = 0
        self._consecutive_tx_failures = 0
        self._poll_not_before = None
        self._tx_recovery_pending = False
        self._retry_ms = RETRY_MS
        self._retry_pending = False
        self._suspended = False
        self._burst_at = None  # ticks_ms origin of the current burst/silent cycle
        self._proof_pkt = None  # PROOF being repeated (§3.3), or None
        self._proof_left = 0
        self._proof_last = 0
        self._log = []  # newest-first describe() lines

    def fid(self):
        return make_fid(self.seq, self.char)

    def name(self):
        return creature_name(self.char)

    def otc_code(self):
        return otc_to_code(self.otc)

    def set_char(self, char):
        if char != self.char:
            self._defer_radio_work()
        self.char = char
        self._forget_claims()
        _save_prefs(self)

    def _forget_claims(self):
        """A new identity invalidates a PROOF still being repeated: it was
        minted against the old FID."""
        self._proof_pkt = None
        self._proof_left = 0
        self.note = ""

    def set_beaconing(self, on):
        if on != self.beaconing:
            self._defer_radio_work()
        if on and not self.beaconing:
            # ZENDT is a fresh operator action, not a continuation of the
            # burst/silent phase that happened before LUISTER. Reusing that
            # old phase can put the newly-enabled transmitter straight into
            # T_SILENT, making RX -> TX appear not to work for four seconds.
            now = time.ticks_ms()
            self._burst_at = None
            self._last_beacon = time.ticks_add(now, -T_BCN)
        self.beaconing = on
        _save_prefs(self)

    def _defer_radio_work(self):
        """Keep SX1262 SPI off the display bus while LVGL redraws controls."""
        deadline = time.ticks_add(time.ticks_ms(), SETTLE_MS)
        if (
            self._poll_not_before is None
            or time.ticks_diff(deadline, self._poll_not_before) > 0
        ):
            self._poll_not_before = deadline

    # ------------------------------------------------------------ lifecycle

    def start(self):
        """Called once, at import. Straight from fox-boss's boss_lora.py: the
        driver object existing is enough to try, and begin() is the first thing
        that touches the chip.

        NO PRESENCE PROBE. An SX1262 that has not been configured yet answers
        getPacketType() with 0xFF on this badge — the same value an empty bus
        gives — so gating bring-up on that read refuses to start a radio that
        is fitted and healthy. fox-boss and the badge's own lora_chat both go
        straight to begin(); verify() is what proves the chip is really
        there."""
        try:
            from mpos import LoRaManager, DeviceInfo
        except Exception as e:
            self.status = "geen mpos.LoRaManager"
            print("trot: mpos.LoRaManager unavailable:", repr(e))
            return

        self.is_fri3d = DeviceInfo.hardware_id == "fri3d_2026"
        self.radio = LoRaManager.radioChip
        if self.radio is None:
            self.status = "geen LoRa-chip aangesloten"
            print("trot: no LoRa radio fitted (LoRaManager.radioChip is None)")
            return
        self.available = True
        _load_prefs(self)
        self._stop_meshcore()  # the other driver on this chip — see below
        self._patch_spi_transfer()  # before ANY SPI traffic
        self._drive_rf_switch()

        # Deferred, not blocking: configuring the radio while LVGL animates
        # the screen transition puts display traffic on the shared bus
        # mid-SPI-transaction.
        self.status = "radio starten"
        self._later(SETTLE_MS, self._bring_up_and_watch)

    def _stop_meshcore(self):
        """Stop org.fri3d.meshcore's background radio service if it is
        running. Its boot service drives this same SX1262 from the background
        whenever its "background radio service" toggle is on, and this OS has
        no LoRaManager.acquire() arbitration: its RX watchdog re-arms — and
        reconfigures — the chip underneath us, which reads as random TX
        failures and lockups. Stopping goes through the manager's public
        stop(); the persistent toggle stays the user's, so MeshCore returns
        at the next boot and Foxtrot stops it again at the next launch."""
        try:
            import sys

            mod = sys.modules.get("meshcore_manager")
            if mod is None:
                return  # boot service never ran; nothing drives the radio
            manager = mod.MeshCoreManager.get_instance()
            if manager.is_running():
                manager.stop()
                print("trot: stopped MeshCore background radio service")
        except Exception as e:
            print("trot: could not stop MeshCore:", repr(e))

    def _drive_rf_switch(self):
        """Hold the Wio-SX1262's antenna-switch gate (RF_SW_PIN) high. It
        stays high for TX and RX alike — DIO2 does the routing (configure())."""
        if not self.is_fri3d:
            return
        try:
            from machine import Pin

            self.rf_sw = Pin(RF_SW_PIN, Pin.OUT)
            self.rf_sw.value(1)
        except Exception as e:
            print("trot: could not drive RF switch pin:", repr(e))

    def _later(self, ms, fn):
        """Run fn once, ms from now, off an lv.timer — never synchronously."""
        import lvgl as lv

        t = lv.timer_create(lambda _t: fn(), max(1, ms), None)
        t.set_repeat_count(1)

    # ── the retry loop ──────────────────────────────────────────────────
    # fox-boss gives up after bring_up()'s three attempts, because a game
    # master can leave the screen and come back. A fox has one job, so this
    # app keeps trying instead: while the radio is not ready, come back and
    # run the whole three-attempt bring-up again, backing off to RETRY_MAX_MS.

    def _bring_up_and_watch(self):
        self._retry_pending = False
        if self._suspended:
            return
        self.attempts += 1
        if self.bring_up():
            self._retry_ms = RETRY_MS
            return
        self._schedule_retry()

    def _schedule_retry(self):
        if self._suspended:
            return
        self._retry_pending = True
        delay = self._retry_ms
        self._retry_ms = min(RETRY_MAX_MS, self._retry_ms * 2)
        self._later(delay, self._bring_up_and_watch)

    def suspend(self):
        """Stop the retry loop while the screen is away. Its timers outlive the
        activity — MicroPythonOS keeps this module alive after the app leaves
        the foreground — and a background app pulsing the shared CH32 expander
        is not something the next app can defend itself against."""
        self._suspended = True

    def resume(self):
        self._suspended = False
        self._defer_radio_work()
        self._stop_meshcore()  # it may have been (re)started while we were away
        if (
            self.radio is not None
            and not self.ready
            and self._consecutive_tx_failures >= MAX_CONSECUTIVE_TX_FAILURES
        ):
            self._schedule_tx_recovery()
        elif self.radio is not None and not self.ready and not self._retry_pending:
            self._retry_pending = True
            self._later(SETTLE_MS, self._bring_up_and_watch)

    # ── bring-up, verbatim from fox-boss's boss_lora.py ──────────────────

    def bring_up(self, allow_hard_reset=True):
        detail = "not attempted"
        for attempt in range(3):
            try:
                self.configure()
            except Exception as e:
                detail = "configure raised: %r" % e
                print("trot: attempt %d %s" % (attempt + 1, detail))
                if allow_hard_reset and self.hard_reset():
                    detail += " (after hard reset)"
                time.sleep_ms(200)
                continue

            ok, detail = self.verify()
            print("trot: verify:", detail)
            if ok:
                self.ready = True
                self.status = "radio klaar"
                self._last_health_check = time.ticks_ms()
                return True
            if allow_hard_reset:
                self.hard_reset()
            time.sleep_ms(200)

        print("trot: radio setup failed after 3 attempts:", detail)
        self.ready = False
        self.status = "%s (ronde %d, %d reset)" % (
            self._short_failure(detail),
            self.attempts,
            self.resets,
        )
        return False

    def _short_failure(self, detail):
        if "raised" in detail or "DEAD" in detail:
            return "chip antwoordt niet"
        if "devErr=0x0000" not in detail:
            return "chip meldt een fout"
        if "sync=1424" not in detail:
            return "instellingen niet goed"
        return "ontvangst start niet"

    def hard_reset(self):
        """Pulse the SX1262 reset via the CH32 expander — fri3d_2026 has no
        ESP32-side reset pin. The only way to un-wedge the radio on this
        board."""
        expander = self._expander()
        if expander is None:
            print("trot: no io_expander, cannot hard reset")
            return False
        try:
            expander.config = EXPANDER_LORA_HELD
            time.sleep_ms(20)
            expander.config = EXPANDER_LORA_RUN
            time.sleep_ms(50)
            self.resets += 1
            print("trot: pulsed LoRa reset via expander (reset #%d)" % self.resets)
        except Exception as e:
            print("trot: hard reset failed:", repr(e))
            return False
        self._drive_rf_switch()  # a reset may have dropped the switch gate
        return True

    def _expander(self):
        try:
            import mpos

            return getattr(mpos, "io_expander", None)
        except Exception:
            return None

    def _patch_spi_transfer(self):
        """Make one SX1262 command one indivisible shared-bus transaction.

        The OS driver manages CS manually across several one-byte SPI calls,
        but SPI.Device normally releases the ESP32 bus after every call. A
        display DMA transfer can then land between the command and data byte
        while LoRa CS is still low. Locking the device makes those calls one
        transaction. Keep the existing shorter BUSY timeout too; sx1262.py is
        OS-owned, so both changes are instance-local here.
        """
        try:
            orig = self.radio.SPItransfer
        except AttributeError:
            print("trot: SPItransfer not found, busy-timeout patch skipped")
            return

        spi = getattr(self.radio, "spi", None)
        lock = getattr(spi, "lock", None)
        unlock = getattr(spi, "unlock", None)
        if lock is None or unlock is None:
            print("trot: SPI device lock unavailable; command isolation skipped")

        def _fast_transfer(
            cmd,
            cmdLen,
            write,
            dataOut,
            dataIn,
            numBytes,
            waitForBusy,
            timeout=SPI_BUSY_TIMEOUT_MS,
        ):
            locked = False
            try:
                if lock is not None and unlock is not None:
                    lock()
                    locked = True
                return orig(
                    cmd,
                    cmdLen,
                    write,
                    dataOut,
                    dataIn,
                    numBytes,
                    waitForBusy,
                    timeout,
                )
            finally:
                if locked:
                    unlock()

        self.radio.SPItransfer = _fast_transfer

    def configure(self):
        self.radio.begin(
            freq=FREQ_MHZ,
            bw=BW_KHZ,
            sf=SF,
            cr=CR,
            syncWord=SYNC_WORD,
            preambleLength=PREAMBLE,
            implicit=False,
            crcOn=True,
            tcxoVoltage=TCXO_V,
            useRegulatorLDO=False,
            blocking=True,
            currentLimit=CURRENT_LIMIT,
            power=TX_POWER,
        )
        # No callback: drops straight into continuous receive with DIO1
        # action cleared. We poll from poll(), never IRQ.
        self.radio.setBlockingCallback(False)

        # DIO2 stays an RF switch — begin() already set that, LEAVE IT. The
        # badge's LoRa is a Wio-SX1262 (badge_2026_hw schematic, RF Expansion
        # sheet), and its datasheet is explicit: TX/RX routing "is determined
        # solely by the voltage level on the SX1262's DIO2 pin" (high = TX),
        # while RF_SW_PIN merely gates the internal switch on and must stay
        # high. Calling setDio2AsRfSwitch(False) here — as the OS board file
        # and lora_chat do — pins the switch to the receive side: the chip
        # reports TX_DONE for every packet while the PA fires into the RX
        # path, and only close-range leakage ever arrives.
        if self.rf_sw is not None:
            self.rf_sw.value(1)  # switch gate on, permanently

    def chip_mode(self):
        """None means the radio isn't answering — getStatus() swallows SPI
        failures and returns 0x00, which is not a real mode."""
        status = self.radio.getStatus()
        if status in (0x00, 0xFF):
            return None
        return (status >> 4) & 0x07

    def verify(self):
        try:
            deverr = self.radio.getDeviceErrors()
            mode = self.chip_mode()
            sync = bytearray(2)
            self.radio.readRegister(REG_LORA_SYNC_WORD_MSB, memoryview(sync), 2)
        except Exception as e:
            return False, "readback raised: %r" % e

        detail = "mode=%s sync=%02x%02x devErr=0x%04x" % (
            "DEAD" if mode is None else CHIP_MODES.get(mode, mode),
            sync[0],
            sync[1],
            deverr,
        )
        ok = mode == MODE_RX and sync[0] == 0x14 and sync[1] == 0x24 and deverr == 0
        return ok, detail

    def health_check(self):
        """False means this poll must stop. fox-boss's version, plus one thing:
        where fox-boss leaves a dead chip dead, this hands it back to the retry
        loop."""
        mode = self.chip_mode()
        if mode == MODE_RX:
            return True

        if mode is None:
            print("trot: radio not responding (BUSY stuck?), hard resetting")
            self.hard_reset()
            if self.bring_up(allow_hard_reset=False):
                return True
            self._retry_ms = RETRY_MS
            self._schedule_retry()
            return False

        print("trot: found chip in %s, re-arming" % CHIP_MODES.get(mode, mode))
        try:
            self.radio.startReceive()
        except Exception as e:
            print("trot: re-arm failed:", repr(e))
            self.status = "ontvangst start niet meer"
            self._retry_ms = RETRY_MS
            self._schedule_retry()
            return False
        return True

    # ---------------------------------------------------------------- poll

    def poll(self):
        """One round, called from the screen's tick BEFORE it draws: read a
        waiting packet or health-check, transmit a beacon if one is due, tick
        the pending PROOF repeats."""
        if not self.ready:
            return
        if self._poll_not_before is not None:
            if time.ticks_diff(time.ticks_ms(), self._poll_not_before) < 0:
                return
            self._poll_not_before = None
        try:
            rx_state = self._rx_state()
            if rx_state == RX_READY:
                self.read_packet()
            elif rx_state == RX_SUSPECT:
                # Preserve the latch for a later clean read. In particular,
                # do not fall through to TX: startTransmit() uses the same
                # buffer base and clears every IRQ, destroying this packet.
                return
            elif (
                time.ticks_diff(time.ticks_ms(), self._last_health_check)
                >= MODE_CHECK_MS
            ):
                self._last_health_check = time.ticks_ms()
                if not self.health_check():
                    self.ready = False
                    return

            now = time.ticks_ms()
            if (
                self.beaconing
                and self._in_burst(now)
                and time.ticks_diff(now, self._last_beacon) >= T_BCN
            ):
                self._last_beacon = now
                if self._transmit(build_beacon(self.fid())):
                    self.beacons_sent += 1

            self._tick_proof(now)
        except Exception as e:
            print("trot: poll error:", repr(e))
            self.soft_recover()

    def _in_burst(self, now):
        """The §5 burst/silent alternation: beacon for T_BURST, dark for
        T_SILENT, listening for CODE_ENTRY throughout. One shared clock,
        advanced cycle by cycle so ticks_diff stays wrap-safe."""
        if self._burst_at is None:
            self._burst_at = now
        pos = time.ticks_diff(now, self._burst_at)
        if pos >= T_BURST + T_SILENT:
            self._burst_at = time.ticks_add(
                self._burst_at,
                ((pos // (T_BURST + T_SILENT)) * (T_BURST + T_SILENT)),
            )
            pos = time.ticks_diff(now, self._burst_at)
        return pos < T_BURST

    def rx_pending(self):
        """Compatibility boolean for callers that only need READY/other."""
        return self._rx_state() == RX_READY

    def _rx_state(self):
        """Classify the receive latch without equating suspect with empty.

        RX_SUSPECT is deliberately distinct: the IRQ stays latched for a
        later poll, and a transmitter must not overwrite its shared buffer.
        """
        irq = self.radio.getIrqStatus()
        if not (irq & IRQ_RX_ANY):
            self._consecutive_rejects = 0
            return RX_EMPTY

        if (
            self.radio.getIrqStatus() != irq
            or self.radio.getPacketType() != PACKET_TYPE_LORA
        ):
            self._consecutive_rejects += 1
            if self._consecutive_rejects >= MAX_REJECTS_BEFORE_RESET:
                print(
                    "trot: %d consecutive unreadable status reads, re-arming"
                    % self._consecutive_rejects
                )
                self._consecutive_rejects = 0
                self.soft_recover()
            return RX_SUSPECT

        self._consecutive_rejects = 0
        return RX_READY

    ERR_CRC = -7  # sx1262.py's ERR_CRC_MISMATCH

    def read_packet(self):
        try:
            msg, err = self.radio.recv()  # reads the buffer, re-arms RX
            if err != 0:
                # A frame arrived but did not survive: that is signal too —
                # a listening badge must show interference, not hide it.
                self._push_log(
                    "CRC-fout" if err == self.ERR_CRC else "RX-fout %d" % err
                )
                return
            if not msg:
                return
            # One GetPacketStatus read for RSSI: byte 0 is RssiPkt, half-dBm.
            status = self.radio.getPacketStatus()
            rssi = -((status >> 16) & 0xFF) / 2.0
            self._handle(msg, rssi)
        except Exception as e:
            print("trot: read error:", repr(e))
            self.soft_recover()

    def soft_recover(self):
        try:
            self.radio.clearIrqStatus()
            self.radio.startReceive()
        except Exception as e:
            print("trot: soft recover failed:", repr(e))

    def _record_tx_success(self):
        self._consecutive_tx_failures = 0

    def _record_tx_failure(self):
        self._consecutive_tx_failures += 1
        if self._consecutive_tx_failures >= MAX_CONSECUTIVE_TX_FAILURES:
            self._schedule_tx_recovery()

    def _schedule_tx_recovery(self):
        """Escalate a TX-only wedge that the RX mode check cannot see.

        A failed send is first given the cheap clear-IRQ/start-RX recovery. If
        several real TX attempts fail without one TX_DONE between them, stop
        polling immediately and defer a reset until display activity has had
        time to settle. This is deliberately separate from health_check(): a
        wedged send path can still accept startReceive() and report MODE_RX.
        """
        if self._tx_recovery_pending:
            return
        self.ready = False
        self.status = "TX herstellen"
        self._tx_recovery_pending = True
        self._later(SETTLE_MS, self._recover_after_tx_failures)

    def _recover_after_tx_failures(self):
        self._tx_recovery_pending = False
        if self._suspended:
            return

        print(
            "trot: %d consecutive TX failures, hard resetting"
            % self._consecutive_tx_failures
        )
        self.hard_reset()
        if self.bring_up():
            self.ready = True
            self._consecutive_tx_failures = 0
            self._retry_ms = RETRY_MS
            return

        self._retry_ms = RETRY_MS
        self._schedule_retry()

    def _transmit(self, data):
        """Leave continuous RX just long enough to clock out one packet, then
        return to it. Runs inline from poll() on the same call stack; display
        DMA still shares the SPI host, so keep this sequence compact.

        Returns True only when the chip actually reported TX_DONE. Callers
        count what went out, never what was attempted: a beacon counter that
        rises while every send() raises is exactly the lie this debug tool
        exists to expose.

        RF_SW_PIN is NOT touched here. It gates the Wio-SX1262's internal
        antenna switch and must stay high the whole time; TX/RX routing is
        DIO2's job alone (see configure()). Dropping the gate around a send —
        mistaking it for an RX-enable — puts the PA into an open switch
        mid-transmit, which locks the chip up within seconds."""
        t0 = time.ticks_ms()
        try:
            # One last receive check closes the race between poll()'s check
            # and this call. The SX1262 has one shared buffer at base 0;
            # startTransmit() writes TX bytes there and clears every IRQ.
            rx_state = self._rx_state()
            if rx_state == RX_SUSPECT:
                return False
            if rx_state == RX_READY:
                self.read_packet()

            # The pinned Python driver jumps straight from continuous RX to
            # startTransmit(). Current RadioLib explicitly enters STDBY_RC
            # first. Do the same, and reaffirm *automatic* DIO2 RF-switch
            # control so RX -> TX always changes the Wio module's antenna
            # route. True means automatic (high only in TX), not pin-high.
            self._driver_ok(self.radio.standby(), "standby")
            self._driver_ok(self.radio.setDio2AsRfSwitch(True), "DIO2 RF switch")

            send_result = self.radio.send(bytes(data))
            if isinstance(send_result, tuple) and len(send_result) > 1:
                self._driver_ok(send_result[1], "send")
            # send() returns once BUSY clears — that
            # covers issuing the command, not the transmission itself.
            irq = 0
            # Anchor the deadline AFTER send() returns: send() is itself a
            # pile of SPI commands whose BUSY waits can eat 100+ ms on a busy
            # bus, and a deadline anchored before it expires with the loop
            # never run — every real transmission then counts as failed.
            deadline = time.ticks_add(time.ticks_ms(), TX_DEADLINE_MS)
            while time.ticks_diff(deadline, time.ticks_ms()) > 0:
                irq = self.radio.getIrqStatus()
                if irq & IRQ_TX_ANY:
                    break
                time.sleep_ms(2)
            done = bool(irq & IRQ_TX_DONE)  # the chip's TIMEOUT bit is not "sent"
            if irq & IRQ_RX_ANY:
                # Defensive only: normal startTransmit() maps TX IRQs and the
                # chip cannot receive while transmitting. If another owner or
                # a garbled status nevertheless exposes RX here, drain before
                # the unconditional clear below.
                self.read_packet()
            self.radio.clearIrqStatus()
            self.radio.startReceive()
            if not done:
                self.tx_fails += 1
                self._log_tx_failure(irq, time.ticks_diff(time.ticks_ms(), t0))
                self._record_tx_failure()
            else:
                self._record_tx_success()
                # Sent replies show in the log so a claim reads as a
                # conversation; BEACONs stay out — at ~3/s they would drown
                # the log, and the counter already tells that story.
                kind = (data[0] >> 4) & 0xF
                if kind != TYPE_BEACON:
                    self._push_log("TX %s" % TYPE_NAMES.get(kind, "?%X" % kind))
            return done
        except Exception as e:
            print("trot: transmit failed:", repr(e))
            self.tx_fails += 1
            self._push_log("TX! %s" % type(e).__name__)
            self.soft_recover()
            self._record_tx_failure()
            return False

    @staticmethod
    def _driver_ok(result, operation):
        """Turn the driver's integer error convention into an exception."""
        if result not in (None, 0):
            raise RuntimeError("%s failed: %r" % (operation, result))

    def _log_tx_failure(self, irq, elapsed):
        """One log line naming why TX_DONE never came, straight from the chip:
        the final IRQ word, the mode it sits in, the device-error flags (PLL
        lock failure = 0x0040 — an RF/antenna problem, not a software one),
        and how long we actually waited (well past TX_DEADLINE_MS means the
        BUSY line stalled inside an SPI read). Reading them is worth the two
        extra SPI transactions: this line is the difference between debugging
        and guessing."""
        try:
            mode = self.chip_mode()
            deverr = self.radio.getDeviceErrors()
            self.radio.clearDeviceErrors()  # fresh flags for the next failure
        except Exception:
            mode, deverr = None, -1
        self._push_log(
            "TX? irq%04X m=%s e%04X %dms"
            % (irq, CHIP_MODES.get(mode, mode), deverr & 0xFFFF, elapsed)
        )

    # ------------------------------------------------- shared traffic logic
    # Everything below is radio-agnostic; FakeTrotRadio reuses it.

    def _push_log(self, line):
        self._log.insert(0, line)
        del self._log[LOG_KEEP:]

    def _handle(self, msg, rssi):
        """Every frame the antenna caught lands in the log — spec traffic
        described, everything else as a raw hexdump. A listening badge that
        only shows packets it already understands cannot debug the air."""
        self.rx_count += 1
        m = parse(msg)
        if m is None:
            self._push_log("?? %s %ddBm" % (hexdump(msg), round(rssi)))
            return
        self._push_log(describe(m, rssi))
        if m["type"] == TYPE_CODE_ENTRY and m["fid"] == self.fid():
            self._on_code_entry(m, rssi)

    def _on_code_entry(self, m, rssi):
        """The §6.1 procedure, whole: validate, PROOF immediately, rotate.
        The fox is the sole authority — no relay, no round trip, no waiting.

        A wrong code and a too-weak packet get the same total silence, so
        nothing leaks about which check failed. A duplicate CODE_ENTRY that
        arrives after the rotation carries a stale code and falls into that
        same silence; the hunter's own PROOF wait/retry covers it (§7.1)."""
        if rssi < RSSI_MIN:
            return
        if m["otc"] != self.otc:
            return
        prf = prf_compute(K, self.fid(), m["hid"])
        pkt = build_proof(self.fid(), m["hid"], prf)
        self._transmit(pkt)
        # Two automatic repeats (§3.3): reliability without a handshake.
        self._proof_pkt = pkt
        self._proof_left = N_PROOF - 1
        self._proof_last = time.ticks_ms()
        self._rotate_otc(time.ticks_ms())
        self.note = "FOUND! hid %d" % m["hid"]
        print("trot: proof hunter %d prf 0x%02X rssi %.1f" % (m["hid"], prf, rssi))

    def _tick_proof(self, now):
        if self._proof_left > 0 and self._proof_pkt is not None:
            if time.ticks_diff(now, self._proof_last) >= PROOF_REPEAT_MS:
                self._transmit(self._proof_pkt)
                self._proof_last = now
                self._proof_left -= 1
                if self._proof_left == 0:
                    self._proof_pkt = None

        # Idle OTC rotation (§6.1) — never while a claim is being answered.
        if (
            self._proof_pkt is None
            and time.ticks_diff(now, self.otc_at) >= T_OTC_ROTATE
        ):
            self._rotate_otc(now)

    def _rotate_otc(self, now):
        self.otc = random_otc()
        self.otc_at = now

    # --------------------------------------------------------------- reads

    def log_lines(self):
        return self._log


class FakeTrotRadio(TrotRadio):
    """DESKTOP ONLY (see the module header). No SX1262 exists, so poll()
    synthesizes the other side: a wandering second fox to listen to, and one
    scripted hunter that claims our current code ~15 s in. TX becomes a
    print. The claim logic above runs unchanged, so the whole CODE_ENTRY ->
    PROOF path is exercisable on the mac."""

    def __init__(self):
        super().__init__()
        self._fake_claim_at = None
        self._fake_rssi = -70.0
        self._fake_next = 0

    def start(self):
        self.available = True
        self.ready = True
        _load_prefs(self)
        self.status = "FAKE radio (desktop)"
        self._fake_claim_at = time.ticks_add(time.ticks_ms(), 15000)

    def poll(self):
        now = time.ticks_ms()
        if (
            self.beaconing
            and self._in_burst(now)
            and time.ticks_diff(now, self._last_beacon) >= T_BCN
        ):
            self._last_beacon = now
            self.beacons_sent += 1

        if time.ticks_diff(now, self._fake_next) >= 0:
            # Another fox (Hond) wandering in and out of range.
            import random

            self._fake_next = time.ticks_add(now, 700)
            self._fake_rssi = max(
                -110.0, min(-45.0, self._fake_rssi + random.randint(-4, 4))
            )
            self._handle(build_beacon(make_fid(1, 6)), self._fake_rssi)

        if (
            self._fake_claim_at is not None
            and time.ticks_diff(now, self._fake_claim_at) >= 0
        ):
            self._fake_claim_at = None
            pkt = bytes([TYPE_CODE_ENTRY << 4, self.fid(), 0, 42, self.otc])
            self._handle(pkt, -60.0)

        self._tick_proof(now)

    def _transmit(self, data):
        print("trot(fake): TX", hexdump(data))
        return True


_PREFS_APP = "com.enigmeta.foxtrot"


def _load_prefs(radio):
    """Restore the creature, but always start with transmission enabled.

    Silencing the badge is useful for bench work, but persisting that setting
    makes one accidental tap survive every restart and leaves the fox
    unfindable. ``TrotRadio.__init__`` supplies the safe True default.
    """
    try:
        from mpos import SharedPreferences

        prefs = SharedPreferences(_PREFS_APP)
        radio.char = prefs.get_int("char", radio.char)
    except Exception as e:
        print("trot: prefs load failed:", repr(e))


def _save_prefs(radio):
    try:
        from mpos import SharedPreferences

        e = SharedPreferences(_PREFS_APP).edit()
        e.put_int("char", radio.char)
        e.commit()
    except Exception as e:
        print("trot: prefs save failed:", repr(e))


def on_badge():
    """True on real badge hardware. This, and never a failed probe, decides
    whether the fake radio is allowed: a badge whose SX1262 is missing or
    wedged must say so, not invent traffic (module header)."""
    import sys

    return sys.platform == "esp32"


def make_radio():
    r = TrotRadio() if on_badge() else FakeTrotRadio()
    r.start()
    return r


# Shared singleton — created at import, same convention as fox-hunt's LINK.
RADIO = make_radio()
