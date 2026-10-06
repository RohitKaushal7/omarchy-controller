"""Layers, taps, holds, repeats and clicks (spec section 5)."""

import unittest

from helpers import ROOT  # noqa: F401  (puts padd on sys.path)
from padd.config import Config
from padd.resolver import Resolver

LAYERS = [
    {
        "id": "base",
        "name": "Browse",
        "bindings": {
            "A": {"click": "left", "label": "Click"},
            "X": {"keys": "PLAYPAUSE", "label": "Play"},
            "UP": {"keys": "UP", "repeat": True, "label": "Up"},
            "Y": {"hold": {"keys": "SUPER+W", "label": "Close"}},
            "BACK": {
                "tap": {"keys": "ESC", "label": "Escape"},
                "hold": {"keys": "SUPER+CTRL+L", "label": "Lock"},
            },
        },
    },
    {
        "id": "text",
        "name": "Text",
        "key": "LT",
        "bindings": {"A": {"keys": "RETURN", "label": "Enter"}},
    },
    {
        "id": "system",
        "name": "System",
        "key": "RT",
        "bindings": {"A": {"keys": "F", "label": "Fullscreen"}},
    },
]
TIMING = {"holdMs": 400, "repeatDelayMs": 300, "repeatMs": 100}


def resolver():
    return Resolver(Config({"layers": LAYERS, "timing": TIMING}))


def seen(effects):
    return [(e.phase, e.action.label, e.layer, e.reason) for e in effects]


class TestTaps(unittest.TestCase):
    def test_tap_fires_on_press_with_no_delay(self):
        r = resolver()
        self.assertEqual(seen(r.press("X", 0.0)), [("fire", "Play", "base", "press")])
        self.assertEqual(r.release("X", 0.05), [])

    def test_unbound_button_does_nothing(self):
        r = resolver()
        self.assertEqual(r.press("B", 0.0), [])
        self.assertEqual(r.release("B", 0.1), [])

    def test_repeat_after_delay_then_at_interval(self):
        r = resolver()
        self.assertEqual(len(r.press("UP", 0.0)), 1)
        self.assertEqual(r.tick(0.29), [])
        self.assertEqual(seen(r.tick(0.30)), [("fire", "Up", "base", "repeat")])
        self.assertEqual(r.tick(0.35), [])
        self.assertEqual(len(r.tick(0.40)), 1)
        r.release("UP", 0.45)
        self.assertEqual(r.tick(1.0), [])
        self.assertIsNone(r.next_deadline())


class TestHolds(unittest.TestCase):
    def test_hold_only_needs_the_full_hold(self):
        r = resolver()
        r.press("Y", 0.0)
        self.assertEqual(r.release("Y", 0.2), [])
        self.assertEqual(r.tick(1.0), [])
        r.press("Y", 2.0)
        self.assertEqual(seen(r.tick(2.4)), [("fire", "Close", "base", "hold")])
        self.assertEqual(r.release("Y", 3.0), [])

    def test_tap_and_hold_share_a_button(self):
        r = resolver()
        self.assertEqual(r.press("BACK", 0.0), [])
        self.assertEqual(seen(r.release("BACK", 0.1)), [("fire", "Escape", "base", "release")])
        r.press("BACK", 1.0)
        self.assertEqual(seen(r.tick(1.4)), [("fire", "Lock", "base", "hold")])
        self.assertEqual(r.release("BACK", 1.6), [], "the tap fired after the hold")

    def test_deadline_reports_the_pending_hold(self):
        r = resolver()
        r.press("BACK", 1.0)
        self.assertAlmostEqual(r.next_deadline(), 1.4)


class TestClicks(unittest.TestCase):
    def test_click_follows_the_button(self):
        r = resolver()
        self.assertEqual(seen(r.press("A", 0.0)), [("down", "Click", "base", "press")])
        self.assertEqual(r.tick(5.0), [])
        self.assertEqual(seen(r.release("A", 5.0)), [("up", "Click", "base", "release")])

    def test_reset_releases_a_held_click(self):
        r = resolver()
        r.press("A", 0.0)
        self.assertEqual(seen(r.reset()), [("up", "Click", "base", "release")])
        self.assertEqual(r.release("A", 0.1), [])


class TestCancel(unittest.TestCase):
    def test_cancel_drops_a_pending_hold(self):
        r = resolver()
        r.press("BACK", 0.0)
        self.assertEqual(r.cancel("BACK"), [])
        self.assertEqual(r.tick(1.0), [])
        self.assertEqual(r.release("BACK", 1.1), [])

    def test_cancel_lets_go_of_a_click(self):
        r = resolver()
        r.press("A", 0.0)
        self.assertEqual(seen(r.cancel("A")), [("up", "Click", "base", "release")])


class TestLayers(unittest.TestCase):
    def test_layer_key_switches_and_fires_nothing(self):
        r = resolver()
        self.assertEqual(r.press("LT", 0.0), [])
        self.assertEqual(r.layer, "text")
        self.assertEqual(seen(r.press("A", 0.1)), [("fire", "Enter", "text", "press")])
        r.release("A", 0.2)
        self.assertEqual(r.release("LT", 0.3), [])
        self.assertEqual(r.layer, "base")

    def test_unbound_in_layer_falls_through_to_base(self):
        r = resolver()
        r.press("LT", 0.0)
        self.assertEqual(seen(r.press("X", 0.1)), [("fire", "Play", "base", "press")])

    def test_press_keeps_its_layer_until_release(self):
        r = resolver()
        r.press("A", 0.0)  # a click in the base layer
        r.press("LT", 0.1)
        self.assertEqual(seen(r.release("A", 0.2)), [("up", "Click", "base", "release")])

    def test_latest_layer_key_wins(self):
        r = resolver()
        r.press("LT", 0.0)
        r.press("RT", 0.1)
        self.assertEqual(r.layer, "system")
        r.release("RT", 0.2)
        self.assertEqual(r.layer, "text")

    def test_layer_released_before_the_button(self):
        r = resolver()
        r.press("RT", 0.0)
        r.press("A", 0.1)
        r.release("RT", 0.2)
        self.assertEqual(r.layer, "base")
        self.assertEqual(r.release("A", 0.3), [])
        self.assertEqual(seen(r.press("A", 0.4)), [("down", "Click", "base", "press")])


if __name__ == "__main__":
    unittest.main()
