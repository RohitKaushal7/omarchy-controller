"""Config validation, editing and round trips; key parsing; mouse maths."""

import tempfile
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401
from padd.config import Action, Config, ConfigError, load
from padd.keymap import BadKeys, combo
from padd.output import NOTCH, REL_WHEEL, REL_WHEEL_HI_RES, REL_X, REL_Y, Mouse, shape
from padd import linux


class TestDefaults(unittest.TestCase):
    def test_defaults_load_and_round_trip(self):
        config = Config({})
        self.assertEqual([layer.id for layer in config.layers], ["base", "text", "system"])
        self.assertEqual(set(config.layer_keys), {"LT", "RT"})
        again = Config(config.to_json())
        self.assertEqual(again.to_json(), config.to_json())

    def test_missing_file_gives_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load(Path(tmp) / "none.json")
            self.assertEqual(config.base.name, "Browse")


class TestValidation(unittest.TestCase):
    def bad(self, raw, fragment):
        with self.assertRaises(ConfigError) as caught:
            Config(raw)
        self.assertIn(fragment, str(caught.exception))

    def test_layer_key_cannot_have_an_action(self):
        self.bad(
            {"layers": [
                {"id": "base", "bindings": {"LT": {"keys": "A"}}},
                {"id": "text", "key": "LT", "bindings": {}},
            ]},
            "layer key",
        )

    def test_base_has_no_key_and_others_need_one(self):
        self.bad({"layers": [{"id": "base", "key": "LT"}]}, "base")
        self.bad({"layers": [{"id": "base"}, {"id": "text"}]}, "needs a key")

    def test_actions(self):
        layer = lambda b: {"layers": [{"id": "base", "bindings": {"A": b}}]}  # noqa: E731
        self.bad(layer({"keys": "SUPER+NOPE"}), "unknown key")
        self.bad(layer({"click": "sideways"}), "click must be")
        self.bad(layer({"keys": "A", "exec": "true"}), "exactly one")
        self.bad(layer({"exec": "true", "repeat": True}), "only keys")
        self.bad(layer({"tap": {"click": "left"}, "hold": {"keys": "A"}}), "cannot share")
        self.bad(layer({"hold": {"keys": "A", "repeat": True}}), "cannot repeat")
        self.bad({"layers": [{"id": "base", "bindings": {"Q": {"keys": "A"}}}]}, "unknown button")

    def test_toggle_combo(self):
        self.assertEqual(Config({}).toggle_combo, ["GUIDE", "BACK"])
        self.assertEqual(Config({"toggleCombo": []}).toggle_combo, [])
        self.bad({"toggleCombo": ["GUIDE"]}, "two or more")
        self.bad({"toggleCombo": ["GUIDE", "NOPE"]}, "unknown button")

    def test_mouse_and_timing(self):
        self.bad({"mouse": {"move": "RS"}}, "different sticks")
        self.bad({"mouse": {"deadzone": 2}}, "deadzone")
        self.bad({"timing": {"holdMs": "slow"}}, "holdMs")


class TestEditing(unittest.TestCase):
    def test_bind_tap_and_hold_then_unbind(self):
        config = Config({})
        config.bind("base", "X", "hold", Action("keys", "NEXTSONG", "Next"))
        binding = config.base.bindings["X"]
        self.assertEqual((binding.tap.label, binding.hold.label), ("Play / pause", "Next"))
        self.assertTrue(config.unbind("base", "X", "tap"))
        self.assertIsNone(config.base.bindings["X"].tap)
        self.assertTrue(config.unbind("base", "X", "hold"))
        self.assertNotIn("X", config.base.bindings)

    def test_bind_refuses_a_layer_key(self):
        with self.assertRaises(ConfigError):
            Config({}).bind("base", "RT", "tap", Action("keys", "A"))

    def test_save_is_loadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            config = Config({}, path)
            config.bind("text", "Y", "tap", Action("keys", "CTRL+SHIFT+Z", "Redo"))
            config.save()
            self.assertEqual(load(path).layer("text").bindings["Y"].tap.label, "Redo")


class TestKeys(unittest.TestCase):
    def test_names_and_aliases(self):
        self.assertEqual(combo("SUPER+RETURN"), combo("leftmeta+enter"))
        self.assertEqual(len(combo("CTRL+SHIFT+TAB")), 3)
        self.assertEqual(combo("XF86AudioPlay"), combo("PLAYPAUSE"))

    def test_unknown(self):
        with self.assertRaises(BadKeys):
            combo("SUPER+BANANA")


class TestQuirks(unittest.TestCase):
    def test_bluetooth_xbox_pad_with_ordered_codes_is_remapped(self):
        from padd.codes import code
        from padd.pad import BUS_BLUETOOTH, KEY_BUTTONS, MICROSOFT, ORDERED_XBOX, keymap_for

        # What an Xbox Wireless Controller (045e:02e0) advertises over Bluetooth.
        ordered = {code(n) for n in ("BTN_SOUTH", "BTN_EAST", "BTN_C", "BTN_NORTH", "BTN_WEST",
                                     "BTN_Z", "BTN_TL", "BTN_TR", "BTN_TL2", "BTN_TR2", "KEY_MENU")}
        keymap = keymap_for(BUS_BLUETOOTH, MICROSOFT, ordered)
        self.assertIs(keymap, ORDERED_XBOX)
        for name, button in (("BTN_C", "X"), ("BTN_NORTH", "Y"), ("BTN_WEST", "LB"), ("BTN_Z", "RB"),
                             ("BTN_TL", "BACK"), ("BTN_TR", "START"), ("BTN_TL2", "L3"),
                             ("BTN_TR2", "R3"), ("KEY_MENU", "GUIDE")):
            self.assertEqual(keymap[code(name)], button)
        self.assertEqual(len(set(ORDERED_XBOX.values())), len(ORDERED_XBOX), "a button twice")

        # Wired (xpad) and adapter (xone) pads advertise BTN_SELECT/START/MODE:
        # standard map, as for any pad that is not Microsoft's.
        xpad = {code(n) for n in ("BTN_SOUTH", "BTN_EAST", "BTN_NORTH", "BTN_WEST", "BTN_TL",
                                  "BTN_TR", "BTN_SELECT", "BTN_START", "BTN_MODE",
                                  "BTN_THUMBL", "BTN_THUMBR")}
        self.assertIs(keymap_for(0x03, MICROSOFT, xpad), KEY_BUTTONS)
        self.assertIs(keymap_for(BUS_BLUETOOTH, MICROSOFT, xpad), KEY_BUTTONS)
        self.assertIs(keymap_for(0x03, MICROSOFT, ordered), KEY_BUTTONS)
        self.assertIs(keymap_for(BUS_BLUETOOTH, 0x2DC8, ordered), KEY_BUTTONS)


class Stick:
    def __init__(self, **sticks):
        self.sticks = sticks

    def stick(self, name):
        return self.sticks.get(name, (0.0, 0.0))


def drive(mouse, pads, seconds):
    now, end = 10.0, 10.0 + seconds
    while now < end:
        due = mouse.next_deadline(now, pads)
        if due is None:
            return
        now = max(now, due)
        mouse.tick(now, pads)
        now += 1e-4


def total(mouse, axis):
    return sum(v for report in mouse.sent for t, c, v in report if t == linux.EV_REL and c == axis)


class TestMouse(unittest.TestCase):
    def mouse(self, **settings):
        return Mouse({**Config({}).mouse, **settings}, dry_run=True, record=True)

    def test_shape(self):
        self.assertEqual(shape(0.1, 0.0, 1000, 0.15, 2), (0.0, 0.0))
        self.assertEqual(shape(1.0, 0.0, 1000, 0.15, 2), (1000.0, 0.0))
        x, y = shape(0.7071, 0.7071, 1000, 0.15, 2)
        self.assertAlmostEqual((x * x + y * y) ** 0.5, 1000, delta=1)

    def test_full_tilt_speed(self):
        mouse = self.mouse(speed=1000)
        drive(mouse, [Stick(LS=(1.0, 0.0))], 1.0)
        self.assertAlmostEqual(total(mouse, REL_X), 1000, delta=20)
        self.assertEqual(total(mouse, REL_Y), 0)

    def test_precision(self):
        mouse = self.mouse(speed=1000, precision=0.25)
        mouse.precise = True
        drive(mouse, [Stick(LS=(0.0, -1.0))], 1.0)
        self.assertAlmostEqual(total(mouse, REL_Y), -250, delta=10)

    def test_scroll_up_and_legacy_notches(self):
        mouse = self.mouse(scrollSpeed=10, scrollCurve=1.0)
        drive(mouse, [Stick(RS=(0.0, -1.0))], 1.0)
        hi_res = total(mouse, REL_WHEEL_HI_RES)
        self.assertAlmostEqual(hi_res, 10 * NOTCH, delta=30)
        self.assertEqual(total(mouse, REL_WHEEL), hi_res // NOTCH)

    def test_resting_and_off(self):
        mouse = self.mouse()
        self.assertIsNone(mouse.next_deadline(0, [Stick(LS=(0.1, 0.1))]))
        mouse.on = False
        self.assertIsNone(mouse.next_deadline(0, [Stick(LS=(1.0, 0.0))]))

    def test_buttons_do_not_double_up(self):
        mouse = self.mouse()
        mouse.button("left", True)
        mouse.button("left", True)
        mouse.release_all()
        self.assertEqual([v for r in mouse.sent for _, _, v in r], [1, 0])


if __name__ == "__main__":
    unittest.main()
