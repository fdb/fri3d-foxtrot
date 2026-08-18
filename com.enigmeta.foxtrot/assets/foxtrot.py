# foxtrot.py — debug beacon UI: one screen, everything on the air visible.
#
# The badge is the FOX: trot_radio.py beacons and answers CODE_ENTRY
# itself — it is the sole authority on a find (see its module header). This screen
# shows the beacon identity (the creature a hunter sees and the 4-digit code
# to type), whether the chip is really transmitting, and a rolling log of
# every frame received, with RSSI and a validation verdict.
#
# ONE TOGGLE, TWO ROLES. The TX panel switches the badge between ZENDT
# (beaconing fox) and LUISTER (pure receiver): the radio sits in continuous
# RX either way — LUISTER only silences our own beacon, so a second badge
# can be held next to a transmitting one and show every frame it hears in
# the log, raw hexdump and CRC failures included. Nothing lights the
# NeoPixels: a fox that glows is a fox anyone can find without a radio.
#
# The tick calls RADIO.poll() FIRST, before touching any widget: every SPI
# transaction the radio needs finishes before this tick draws, on the same
# call stack, on the one UI thread (see trot_radio.py's header).

import time

import lvgl as lv
from mpos import Activity
from mpos import FontManager

import trot_radio

# ---- dark palette (fox-boss values) ----------------------------------------
BG = 0x14100B
CARD = 0x221B12
EDGE = 0x4A3B24
TEXT = 0xEFE0BB
MUTED = 0x8A7D5E
GOLD = 0xE8B23A
GREEN = 0x7DBE58
TERRA = 0xE07A4E
FOCUS_GOLD = 0xFFCB45
CELL_OFF = 0x0C0906

TICK_MS = 100  # fast enough to keep the T_BCN=350ms beacon cadence honest
LOG_REFRESH_MS = 500  # log labels repaint at most this often. A badge next to
# a beaconing neighbour logs ~3 frames/s; repainting 7 labels per frame keeps
# display DMA on the shared SPI bus almost continuously — the exact contention
# the _set() guards exist to avoid. The radio's log itself misses nothing;
# only the paint is throttled.


# ---- shared styles + fonts (fox-boss construction) -------------------------
def hexc(v):
    return lv.color_hex(v)


def _style(**props):
    s = lv.style_t()
    s.init()
    for k, v in props.items():
        getattr(s, "set_" + k)(v)
    return s


_RESET = _style(pad_all=0, border_width=0)
_PANEL = _style(border_width=2, border_color=hexc(EDGE))
_FOCUS = _style(
    border_color=hexc(GOLD),
    outline_color=hexc(FOCUS_GOLD),
    outline_width=4,
    outline_pad=0,
    outline_opa=lv.OPA.COVER,
)
_PRESSED = _style(translate_y=2)

_FONT_DIR = "M:apps/com.enigmeta.foxtrot/assets/fonts/"
_FONTS = {}


def _load(name, fallback_size):
    if name in _FONTS:
        return _FONTS[name]
    f = None
    try:
        f = lv.binfont_create(_FONT_DIR + name)
    except Exception as e:
        print("foxtrot: binfont", name, "failed:", e)
    if f is None:
        try:
            f = FontManager.getFont(size=fallback_size)
        except Exception:
            f = None
    _FONTS[name] = f
    return f


def font_small():
    return _load("pixelify_r11.bin", 11)


def font_title():
    return _load("pixelify_b22.bin", 20)


def make_screen():
    s = lv.obj()
    s.add_style(_RESET, 0)
    s.set_style_radius(0, 0)
    s.set_style_bg_color(hexc(BG), 0)
    s.remove_flag(lv.obj.FLAG.SCROLLABLE)
    return s


def box(parent, x, y, w, h, bg=None, radius=0):
    o = lv.obj(parent)
    o.set_pos(x, y)
    o.set_size(w, h)
    o.add_style(_RESET, 0)
    o.set_style_radius(radius, 0)
    o.remove_flag(lv.obj.FLAG.SCROLLABLE)
    o.remove_flag(lv.obj.FLAG.CLICKABLE)
    if bg is None:
        o.set_style_bg_opa(lv.OPA.TRANSP, 0)
    else:
        o.set_style_bg_color(hexc(bg), 0)
    return o


def label(parent, text, x, y, color=TEXT, font=None, w=None, center=False):
    l = lv.label(parent)
    l.set_text(text)
    l.set_pos(x, y)
    if w is not None:
        l.set_width(w)
        if center:
            l.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
    l.set_style_text_color(hexc(color), 0)
    f = font if font is not None else font_small()
    if f is not None:
        l.set_style_text_font(f, 0)
    return l


def panel(parent, x, y, w, h, bg=CARD, radius=2):
    o = box(parent, x, y, w, h, bg, radius=radius)
    o.add_style(_PANEL, 0)
    return o


def focusable(obj, on_click):
    obj.add_flag(lv.obj.FLAG.CLICKABLE)
    obj.add_flag(lv.obj.FLAG.SCROLL_ON_FOCUS)
    obj.add_style(_FOCUS, lv.PART.MAIN | lv.STATE.FOCUSED)
    obj.add_style(_PRESSED, lv.PART.MAIN | lv.STATE.PRESSED)
    g = lv.group_get_default()
    if g:
        g.add_obj(obj)
    obj.add_event_cb(lambda e: on_click(), lv.EVENT.CLICKED, None)
    return obj


# ═════════════════════════════ the one screen ═══════════════════════════════
class FoxtrotActivity(Activity):
    def onCreate(self):
        s = make_screen()

        # Banner
        box(s, 0, 0, 320, 26, CELL_OFF)
        label(s, "FOXTROT", 8, 4, GOLD, font_title())
        box(s, 0, 26, 320, 2, GOLD)
        rl = label(s, "LoRa baken", 8, 8, MUTED, w=304)
        rl.set_style_text_align(lv.TEXT_ALIGN.RIGHT, 0)

        # Identity panel: the creature a hunter sees, and the code they type.
        # Click cycles the creature — the whole roster is on one FID byte.
        # The FID byte is deliberately NOT shown: it is CHAR<<3|SEQ on the
        # wire (spec §2.1), so it changes with the creature — on screen it
        # read like an unstable device id. The log still shows real bytes.
        ip = panel(s, 6, 34, 196, 78)
        self.id_name = label(ip, "", 10, 2, GOLD, font_title(), w=176)
        self.id_fid = label(ip, "", 10, 30, MUTED, w=176)
        self.id_code = label(ip, "", 10, 44, TEXT, font_title())
        focusable(ip, self._cycle_char)

        # TX panel: is the chip really clocking beacons out?
        tp = panel(s, 208, 34, 106, 78)
        self.tx_state = label(tp, "", 8, 2, GREEN, font_title(), w=94)
        self.tx_count = label(tp, "", 8, 30, MUTED, w=94)
        self.tx_fail = label(tp, "", 8, 44, MUTED, w=94)
        label(tp, "klik: wissel", 8, 58, MUTED, w=94)
        focusable(tp, self._toggle_tx)

        # Packet log: newest first — spec packets described, everything else
        # as raw hexdump, CRC failures named. This is the listening half.
        lp = panel(s, 6, 118, 308, 104, bg=CELL_OFF)
        self.log_lines = [
            label(lp, "", 8, 4 + i * 14, TEXT if i == 0 else MUTED, w=292)
            for i in range(7)
        ]

        self.footer = label(s, "", 8, 226, GREEN, w=304)
        self.timer = None
        self._drawn = {}  # id(label) -> last text/colour pushed; see _set()
        self._log_painted = 0  # ticks_ms of the last log repaint
        self.setContentView(s)

    # ------------------------------------------------------------ lifecycle
    def onResume(self, screen):
        super().onResume(screen)
        trot_radio.RADIO.resume()
        self._tick()
        self.timer = lv.timer_create(lambda _t: self._tick(), TICK_MS, None)

    def onPause(self, screen):
        super().onPause(screen)
        if self.timer is not None:
            self.timer.delete()
            self.timer = None
        # Nothing polls the radio while this screen is away, so the bring-up
        # loop must stop too: its timers outlive the activity.
        trot_radio.RADIO.suspend()

    # ------------------------------------------------------------- actions
    def _toggle_tx(self):
        r = trot_radio.RADIO
        r.set_beaconing(not r.beaconing)

    def _cycle_char(self):
        r = trot_radio.RADIO
        r.set_char((r.char + 1) % len(trot_radio.CREATURE_NAMES))

    # ---------------------------------------------------------------- tick
    #
    # Every widget write below goes through _set()/_set_color(), which skip
    # the LVGL call when the value did not change. Not cosmetics: an
    # invalidated label becomes a display flush, and the flush is DMA on the
    # SAME SPI host the SX1262 sits on — with completion the radio's manual-CS
    # transaction cannot see (fox-hunt's sx1262_spi_patch story). Unconditional
    # set_text at 10 Hz keeps display DMA in flight almost permanently; with
    # the guards a steady-state tick invalidates nothing but the beacon
    # counter, and the bus is quiet when poll() next touches the radio.
    def _set(self, lbl, text):
        if self._drawn.get(id(lbl)) != text:
            self._drawn[id(lbl)] = text
            lbl.set_text(text)

    def _set_color(self, lbl, colour):
        key = -id(lbl)  # same dict, disjoint from the text keys
        if self._drawn.get(key) != colour:
            self._drawn[key] = colour
            lbl.set_style_text_color(hexc(colour), 0)

    def _tick(self):
        r = trot_radio.RADIO
        r.poll()  # radio first, widgets after — see module header

        self._set(self.id_name, r.name().upper())
        self._set(self.id_fid, "dier %d" % r.char)
        self._set(self.id_code, "%04d" % r.otc_code())

        if not r.ready:
            self._set(self.tx_state, "GEEN TX")
            self._set_color(self.tx_state, TERRA)
        elif r.beaconing:
            self._set(self.tx_state, "ZENDT")
            self._set_color(self.tx_state, GREEN)
        else:
            self._set(self.tx_state, "LUISTER")
            self._set_color(self.tx_state, GOLD)
        self._set(self.tx_count, "%d bakens" % r.beacons_sent)
        self._set(self.tx_fail, "%d mislukt" % r.tx_fails)
        self._set_color(self.tx_fail, TERRA if r.tx_fails else MUTED)

        now = time.ticks_ms()
        if time.ticks_diff(now, self._log_painted) >= LOG_REFRESH_MS:
            self._log_painted = now
            lines = r.log_lines()
            for i, l in enumerate(self.log_lines):
                self._set(l, lines[i] if i < len(lines) else "")

        # The reset count is the chip's health in one number: a beacon that
        # keeps needing the expander reset is not a beacon anyone can hunt.
        resets = ("  -  %d reset" % r.resets) if r.resets else ""
        note = ("  -  " + r.note) if r.note else ""
        self._set(
            self.footer, "%s  -  RX %d%s%s" % (r.status, r.rx_count, resets, note)
        )
        if not r.ready:
            colour = TERRA  # the one state that needs the user to act
        elif r.note:
            colour = GOLD
        else:
            colour = GREEN
        self._set_color(self.footer, colour)
