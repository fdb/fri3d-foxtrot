import ast
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_SOURCE = ROOT / "com.enigmeta.foxtrot" / "assets" / "foxtrot.py"


def load_sync_controls(radio):
    tree = ast.parse(UI_SOURCE.read_text())
    settings = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "SettingsActivity"
    )
    method = next(
        node
        for node in settings.body
        if isinstance(node, ast.FunctionDef) and node.name == "_sync_controls"
    )
    module = ast.Module(body=[method], type_ignores=[])
    namespace = {
        "lv": types.SimpleNamespace(STATE=types.SimpleNamespace(CHECKED="checked")),
        "trot_radio": types.SimpleNamespace(RADIO=radio),
    }
    exec(compile(ast.fix_missing_locations(module), str(UI_SOURCE), "exec"), namespace)
    return namespace["_sync_controls"]


def load_keypad_control(group, focusable):
    tree = ast.parse(UI_SOURCE.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "keypad_control"
    )
    module = ast.Module(body=[function], type_ignores=[])
    namespace = {
        "lv": types.SimpleNamespace(group_remove_obj=group.remove_obj),
        "focusable": focusable,
    }
    exec(compile(ast.fix_missing_locations(module), str(UI_SOURCE), "exec"), namespace)
    return namespace["keypad_control"]


class FakeSwitch:
    def __init__(self):
        self.states = {"checked"}

    def add_state(self, state):
        self.states.add(state)

    def remove_state(self, state):
        self.states.discard(state)


class FakeGroup:
    def __init__(self, *objects):
        self.objects = list(objects)

    def remove_obj(self, obj):
        self.objects.remove(obj)


class SettingsControlTests(unittest.TestCase):
    def test_keypad_focus_uses_panel_instead_of_native_switch(self):
        switch = FakeSwitch()
        panel = object()
        group = FakeGroup(switch)
        focused = []
        toggles = []

        helper = load_keypad_control(
            group, lambda obj, click: focused.append((obj, click))
        )
        helper(panel, switch, lambda: toggles.append(True))

        self.assertNotIn(switch, group.objects)
        self.assertEqual(focused[0][0], panel)
        focused[0][1]()
        self.assertEqual(toggles, [True])

    def test_syncing_disabled_transmit_uses_supported_switch_api(self):
        radio = types.SimpleNamespace(
            beaconing=False,
            name=lambda: "Vos",
        )
        activity = types.SimpleNamespace(
            creature_name=types.SimpleNamespace(set_text=lambda _text: None),
            switch=FakeSwitch(),
            _paint_tx=lambda _on: None,
        )

        load_sync_controls(radio)(activity)

        self.assertNotIn("checked", activity.switch.states)


if __name__ == "__main__":
    unittest.main()
