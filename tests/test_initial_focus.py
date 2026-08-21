"""Regression coverage for the home screen's keypad focus entry point."""

import ast
from pathlib import Path
import types


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = ROOT / "com.enigmeta.foxtrot" / "assets" / "foxtrot.py"


class FakeGroup:
    def __init__(self):
        self.focused = None

    def add_obj(self, obj):
        if self.focused is None:
            self.focused = obj


class FakeObject:
    def __init__(self):
        self.flags = []
        self.styles = []
        self.callbacks = {}

    def add_flag(self, flag):
        self.flags.append(flag)

    def add_style(self, style, selector):
        self.styles.append((style, selector))

    def add_event_cb(self, callback, code, _user_data):
        self.callbacks.setdefault(code, []).append(callback)

    def send(self, code, key=None):
        event = types.SimpleNamespace(get_key=lambda: key)
        for callback in self.callbacks.get(code, []):
            callback(event)


def load_focus_helpers(group):
    tree = ast.parse(UI_SOURCE.read_text())
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name in ("focusable", "focus_anchor")
    ]
    module = ast.Module(body=functions, type_ignores=[])
    lv = types.SimpleNamespace(
        obj=types.SimpleNamespace(
            FLAG=types.SimpleNamespace(CLICKABLE="clickable", SCROLL_ON_FOCUS="scroll")
        ),
        PART=types.SimpleNamespace(MAIN=1),
        STATE=types.SimpleNamespace(FOCUSED=2, PRESSED=4),
        EVENT=types.SimpleNamespace(KEY="key", CLICKED="clicked"),
        group_get_default=lambda: group,
        group_focus_obj=lambda obj: setattr(group, "focused", obj),
    )
    namespace = {
        "lv": lv,
        "_FOCUS": "focus",
        "_FOCUS_TIGHT": "focus-tight",
        "_PRESSED": "pressed",
        "box": lambda _parent, _x, _y, _w, _h: FakeObject(),
    }
    exec(compile(module, str(UI_SOURCE), "exec"), namespace)
    return namespace["focusable"], namespace["focus_anchor"], lv


def test_invisible_anchor_holds_initial_focus_until_navigation_reaches_button():
    group = FakeGroup()
    focusable, focus_anchor, lv = load_focus_helpers(group)
    anchor = focus_anchor(object(), 238, 13)
    button = FakeObject()
    clicks = []

    focusable(button, lambda: clicks.append(True), tight=True)

    focused_selector = lv.PART.MAIN | lv.STATE.FOCUSED
    assert group.focused is anchor
    assert ("focus-tight", focused_selector) in button.styles

    button.send(lv.EVENT.CLICKED)
    assert clicks == [True]
