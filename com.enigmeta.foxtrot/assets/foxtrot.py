# foxtrot.py - readable beacon UI with protected settings.
#
# The main screen is deliberately display-only. It gives the hunter one large,
# centred code to read and keeps the radio health visible, but puts the two
# controls that can change the fox behind INST. In particular, silencing the
# beacon can no longer happen through an accidental tap on the home screen.
#
# Every active screen polls RADIO before drawing. The SX1262 shares its SPI host
# with the display, so all radio work stays on the LVGL thread and happens before
# any widget invalidation (see trot_radio.py's module header).

import time

import lvgl as lv
from mpos import Activity, Intent
from mpos import FontManager

import trot_radio

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

TICK_MS = 100
LOG_REFRESH_MS = 500

RARITY_COLOR = {"norm": GREEN, "rare": GOLD, "leg": TERRA}

# Same book order, ids and rarity classes as foxboss's creature chooser.
ROSTER = [
    (0, "Vos", "norm"),
    (1, "Egel", "norm"),
    (2, "Kat", "norm"),
    (3, "Axolotl", "norm"),
    (4, "Capybara", "norm"),
    (5, "Koe", "norm"),
    (6, "Hond", "norm"),
    (7, "Eend", "norm"),
    (8, "Kip", "norm"),
    (9, "Koala", "norm"),
    (10, "Konijn", "norm"),
    (11, "Varken", "norm"),
    (22, "Aap", "norm"),
    (23, "Giraf", "norm"),
    (24, "Papegaai", "norm"),
    (16, "Everzwaan", "rare"),
    (17, "Kameleeuw", "rare"),
    (18, "Koekoekoek", "rare"),
    (19, "Konijlpaard", "rare"),
    (20, "Slakamander", "rare"),
    (21, "Tijghert", "rare"),
    (12, "Knoricorn", "leg"),
    (13, "Glitch Vos", "leg"),
    (14, "Party Vos", "leg"),
    (15, "Zwarte Vos", "leg"),
    (25, "Dolfenix", "leg"),
    (26, "Kraaiken", "leg"),
]


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
_FOCUS_TIGHT = _style(
    border_width=2,
    border_color=hexc(GOLD),
    outline_color=hexc(FOCUS_GOLD),
    outline_width=2,
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


def font_code():
    # A true 2x bitmap bake of pixelify_b22: crisp on the badge, with no
    # draw-time transform or interpolation.
    return _load("pixelify_b44.bin", 44)


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


def banner(screen, title, right=None):
    box(screen, 0, 0, 320, 26, CELL_OFF)
    label(screen, title, 8, 4, GOLD, font_title())
    box(screen, 0, 26, 320, 2, GOLD)
    if right is not None:
        r = label(screen, right, 8, 8, MUTED, w=304)
        r.set_style_text_align(lv.TEXT_ALIGN.RIGHT, 0)


def panel(parent, x, y, w, h, bg=CARD, radius=2):
    o = box(parent, x, y, w, h, bg, radius=radius)
    o.add_style(_PANEL, 0)
    return o


def focusable(obj, on_click, tight=False):
    obj.add_flag(lv.obj.FLAG.CLICKABLE)
    obj.add_flag(lv.obj.FLAG.SCROLL_ON_FOCUS)
    obj.add_style(
        _FOCUS_TIGHT if tight else _FOCUS,
        lv.PART.MAIN | lv.STATE.FOCUSED,
    )
    obj.add_style(_PRESSED, lv.PART.MAIN | lv.STATE.PRESSED)
    g = lv.group_get_default()
    if g:
        g.add_obj(obj)
    obj.add_event_cb(lambda e: on_click(), lv.EVENT.CLICKED, None)
    return obj


def keypad_control(panel, native_widget, on_click):
    """Focus a panel, not a native widget that edits on arrow keys."""
    lv.group_remove_obj(native_widget)
    return focusable(panel, on_click)


def focus_anchor(parent, x, y):
    """Invisible initial focus point; directional input can leave it."""
    anchor = box(parent, x, y, 1, 1)
    g = lv.group_get_default()
    if g:
        g.add_obj(anchor)
        lv.group_focus_obj(anchor)
    return anchor


# Every child screen keeps the radio alive. Navigating into settings must not
# temporarily silence the fox or pause recovery of a radio that is starting.
class RadioActivity(Activity):
    def onResume(self, screen):
        super().onResume(screen)
        trot_radio.RADIO.resume()
        self._tick_screen()
        self.timer = lv.timer_create(lambda _t: self._tick_screen(), TICK_MS, None)

    def onPause(self, screen):
        super().onPause(screen)
        if self.timer is not None:
            self.timer.delete()
            self.timer = None
        trot_radio.RADIO.suspend()

    def _tick_screen(self):
        trot_radio.RADIO.poll()


class FoxtrotActivity(RadioActivity):
    def onCreate(self):
        s = make_screen()
        banner(s, "FOXTROT")

        # The only home-screen action: settings are one deliberate navigation
        # away, while the code and status panels are fully non-interactive.
        focus_anchor(s, 238, 13)
        settings = panel(s, 246, 3, 68, 20, bg=CARD, radius=2)
        sl = label(settings, "INST.", 0, 2, GOLD, w=64, center=True)
        sl.align(lv.ALIGN.CENTER, 0, 0)
        focusable(settings, self._open_settings, tight=True)

        # One visual target for a hunter: only the centred, double-size claim
        # code. The creature identity is secret and stays behind settings.
        cp = panel(s, 6, 34, 308, 94)
        self.id_code = label(cp, "", 6, 0, TEXT, font_code(), w=292, center=True)
        self.id_code.align(lv.ALIGN.CENTER, 0, 0)

        # Radio truth remains visible but cannot be changed from here.
        sp = panel(s, 6, 134, 308, 32)
        self.tx_state = label(sp, "", 8, 6, GREEN, font_title(), w=94)
        self.tx_count = label(sp, "", 106, 4, MUTED, w=96)
        self.tx_fail = label(sp, "", 204, 4, MUTED, w=94)

        # Keep a compact air log for debugging without competing with the code.
        lp = panel(s, 6, 172, 308, 50, bg=CELL_OFF)
        self.log_lines = [
            label(lp, "", 8, 4 + i * 14, TEXT if i == 0 else MUTED, w=292)
            for i in range(3)
        ]

        self.footer = label(s, "", 8, 226, GREEN, w=304)
        self.timer = None
        self._drawn = {}
        self._log_painted = 0
        self.setContentView(s)

    def _open_settings(self):
        self.startActivity(Intent(activity_class=SettingsActivity))

    def _set(self, lbl, text):
        if self._drawn.get(id(lbl)) != text:
            self._drawn[id(lbl)] = text
            lbl.set_text(text)

    def _set_color(self, lbl, colour):
        key = -id(lbl)
        if self._drawn.get(key) != colour:
            self._drawn[key] = colour
            lbl.set_style_text_color(hexc(colour), 0)

    def _tick_screen(self):
        r = trot_radio.RADIO
        r.poll()

        self._set(self.id_code, "%04d" % r.otc_code())

        if not r.ready:
            self._set(self.tx_state, "GEEN TX")
            self._set_color(self.tx_state, TERRA)
        elif r.beaconing:
            self._set(self.tx_state, "ZENDT")
            self._set_color(self.tx_state, GREEN)
        else:
            self._set(self.tx_state, "UIT")
            self._set_color(self.tx_state, TERRA)
        self._set(self.tx_count, "%d bakens" % r.beacons_sent)
        self._set(self.tx_fail, "%d mislukt" % r.tx_fails)
        self._set_color(self.tx_fail, TERRA if r.tx_fails else MUTED)

        now = time.ticks_ms()
        if time.ticks_diff(now, self._log_painted) >= LOG_REFRESH_MS:
            self._log_painted = now
            lines = r.log_lines()
            for i, item in enumerate(self.log_lines):
                self._set(item, lines[i] if i < len(lines) else "")

        resets = ("  -  %d reset" % r.resets) if r.resets else ""
        note = ("  -  " + r.note) if r.note else ""
        self._set(
            self.footer, "%s  -  RX %d%s%s" % (r.status, r.rx_count, resets, note)
        )
        if not r.ready:
            colour = TERRA
        elif r.note:
            colour = GOLD
        else:
            colour = GREEN
        self._set_color(self.footer, colour)


class SettingsActivity(RadioActivity):
    def onCreate(self):
        s = make_screen()
        banner(s, "INSTELLINGEN")

        creature_panel = panel(s, 6, 38, 308, 58)
        label(creature_panel, "DIER", 10, 5, GOLD, font_title())
        self.creature_name = label(creature_panel, "", 106, 7, TEXT, w=126)
        change = label(creature_panel, "WIJZIG >", 222, 7, GOLD, w=74)
        change.set_style_text_align(lv.TEXT_ALIGN.RIGHT, 0)
        label(creature_panel, "kies welk dier jagers zien", 10, 35, MUTED, w=286)
        focusable(creature_panel, self._open_creatures)

        transmit_panel = panel(s, 6, 104, 308, 78, bg=CELL_OFF)
        label(transmit_panel, "ZENDEN", 10, 7, GOLD, font_title())
        label(transmit_panel, "uit = badge is niet vindbaar", 10, 37, TERRA, w=214)
        self.switch = lv.switch(transmit_panel)
        self.switch.set_pos(238, 12)
        self.switch.set_size(60, 28)
        self.switch.set_style_bg_color(hexc(TERRA), lv.PART.MAIN)
        self.switch.set_style_bg_color(
            hexc(GREEN), lv.PART.INDICATOR | lv.STATE.CHECKED
        )
        self.switch.set_style_bg_color(hexc(GOLD), lv.PART.KNOB)
        self.switch.add_event_cb(self._on_toggle, lv.EVENT.VALUE_CHANGED, None)
        keypad_control(transmit_panel, self.switch, self._toggle_transmit)
        self.tx_label = label(transmit_panel, "", 240, 45, GREEN, w=56, center=True)

        label(
            s,
            "Zenden staat bij elke start standaard AAN.",
            8,
            194,
            MUTED,
            w=304,
            center=True,
        )
        label(s, "terug: veeg vanaf de linkerrand", 8, 218, MUTED, w=304, center=True)
        self.timer = None
        self._sync_controls()
        self.setContentView(s)

    def _open_creatures(self):
        self.startActivity(Intent(activity_class=CreatureActivity))

    def _on_toggle(self, _evt):
        on = self.switch.has_state(lv.STATE.CHECKED)
        trot_radio.RADIO.set_beaconing(on)
        self._paint_tx(on)

    def _toggle_transmit(self):
        on = not self.switch.has_state(lv.STATE.CHECKED)
        if on:
            self.switch.add_state(lv.STATE.CHECKED)
        else:
            self.switch.remove_state(lv.STATE.CHECKED)
        trot_radio.RADIO.set_beaconing(on)
        self._paint_tx(on)

    def _sync_controls(self):
        r = trot_radio.RADIO
        self.creature_name.set_text(r.name())
        if r.beaconing:
            self.switch.add_state(lv.STATE.CHECKED)
        else:
            self.switch.remove_state(lv.STATE.CHECKED)
        self._paint_tx(r.beaconing)

    def _paint_tx(self, on):
        self.tx_label.set_text("AAN" if on else "UIT")
        self.tx_label.set_style_text_color(hexc(GREEN if on else TERRA), 0)

    def _tick_screen(self):
        trot_radio.RADIO.poll()
        name = trot_radio.RADIO.name()
        if self.creature_name.get_text() != name:
            self.creature_name.set_text(name)


class CreatureActivity(RadioActivity):
    def onCreate(self):
        s = make_screen()
        banner(s, "KIES HET DIER")

        # Same 3-wide, rarity-coloured, scrolling grid as foxboss.
        cw, ch, gap = 100, 25, 3
        grid = box(s, 4, 34, 312, 202)
        grid.add_flag(lv.obj.FLAG.SCROLLABLE)
        grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
        grid.set_style_pad_all(2, 0)
        grid.set_style_pad_column(gap, 0)
        grid.set_style_pad_row(gap, 0)
        for cid, name, rarity in ROSTER:
            cell = box(grid, 0, 0, cw, ch, CARD, radius=2)
            cell.set_style_border_width(1, 0)
            cell.set_style_border_color(hexc(EDGE), 0)
            name_label = label(
                cell, name, 0, 0, RARITY_COLOR[rarity], w=cw - 4, center=True
            )
            name_label.align(lv.ALIGN.CENTER, 0, 0)
            focusable(cell, lambda i=cid: self._pick(i), tight=True)
        self.timer = None
        self.setContentView(s)

    def _pick(self, cid):
        trot_radio.RADIO.set_char(cid)
        self.finish()
