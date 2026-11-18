"""The real daemon, in dry-run mode, against a synthetic uinput pad.

Needs write access to /dev/uinput. The pad's name carries TEST_MARK, so a
daemon already running on this machine never picks it up.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import NAME, ROOT, SyntheticPad
from padd.pad import TEST_MARK

TIMING = {"holdMs": 300, "repeatDelayMs": 200, "repeatMs": 80, "hintDelayMs": 100}


class Daemon:
    def __init__(self, config: dict | str):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "config.json"
        if isinstance(config, str):
            self.path.write_text(config)
        else:
            self.write(config)
        self.events: queue.Queue = queue.Queue()
        self.seen: list[dict] = []
        self.proc = subprocess.Popen(
            [str(ROOT / "bin" / "padctl"), "--config", str(self.path), "daemon", "--dry-run"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        threading.Thread(target=self._read, daemon=True).start()

    def write(self, config: dict) -> None:
        self.path.write_text(json.dumps(config))

    def _read(self):
        for line in self.proc.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            self.seen.append(event)
            self.events.put(event)

    def send(self, **command):
        self.proc.stdin.write(json.dumps(command) + "\n")
        self.proc.stdin.flush()

    def wait(self, kind: str, timeout: float = 5.0, **match) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = self.events.get(timeout=max(0.01, deadline - time.monotonic()))
            except queue.Empty:
                break
            if event["event"] == kind and all(event.get(k) == v for k, v in match.items()):
                return event
        raise AssertionError(f"no {kind} {match}; saw {[e['event'] for e in self.seen[-15:]]}")

    def drain(self):
        time.sleep(0.15)
        while not self.events.empty():
            self.events.get_nowait()
        self.seen.clear()

    def fired(self, settle: float = 0.4) -> list[str]:
        time.sleep(settle)
        return [f"{e['layer']}:{e['label']}" for e in self.seen if e["event"] == "fire"]

    def stop(self):
        self.send(cmd="quit")
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()
        self.proc.stdin.close()
        self.dir.cleanup()


# The synthetic pad is a uinput device; without write access there is no pad.
needs_uinput = unittest.skipUnless(os.access("/dev/uinput", os.W_OK), "/dev/uinput is not writable")


@needs_uinput
class TestDaemon(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daemon = Daemon({"device": {"match": [TEST_MARK]}, "timing": TIMING})
        cls.daemon.wait("started")
        cls.pad = SyntheticPad()
        cls.connected = cls.daemon.wait("connected", timeout=6, name=NAME)

    @classmethod
    def tearDownClass(cls):
        cls.pad.close()
        cls.daemon.stop()

    def setUp(self):
        self.daemon.drain()

    def test_a_pad_plugged_in_while_running_is_news(self):
        self.assertTrue(self.connected["new"])

    def test_no_battery_reported_is_none(self):
        self.daemon.send(cmd="state")
        state = self.daemon.wait("state")
        self.assertIsNone(state["battery"])  # uinput pads have no power supply
        self.assertEqual(state["batteryStatus"], "")

    def test_tap(self):
        self.pad.tap("X")
        self.assertEqual(self.daemon.fired(), ["base:Play / pause"])

    def test_click_reports_once(self):
        self.pad.tap("A", hold=0.1)
        self.assertEqual(self.daemon.fired(), ["base:Click"])

    def test_layer_by_trigger(self):
        self.pad.set("LT", True)
        self.daemon.wait("layer", layer="text")
        self.pad.tap("B")
        self.pad.tap("X")  # Delete word
        self.pad.set("LT", False)
        self.daemon.wait("layer", layer="base")
        self.assertEqual(self.daemon.fired(), ["text:Backspace", "text:Delete word"])

    def test_dpad_repeat(self):
        self.pad.set("UP", True)
        time.sleep(0.45)
        self.pad.set("UP", False)
        fired = self.daemon.fired()
        self.assertGreaterEqual(len(fired), 3)
        self.assertEqual(set(fired), {"base:Up"})

    def test_tap_and_hold(self):
        self.pad.set("RT", True)
        self.pad.tap("BACK", hold=0.05)
        self.pad.tap("BACK", hold=0.45)
        self.pad.set("RT", False)
        self.assertEqual(self.daemon.fired(), ["system:Former workspace", "system:Lock"])

    def test_bumper_tap_switches_and_hold_takes_the_window(self):
        self.pad.tap("RB", hold=0.05)
        self.pad.tap("RB", hold=0.45)
        self.pad.tap("LB", hold=0.45)
        self.assertEqual(self.daemon.fired(), [
            "base:Next workspace", "base:Window to a new workspace", "base:Window to previous workspace",
        ])

    def test_pause_reports_buttons_but_runs_nothing(self):
        self.daemon.send(cmd="pause")
        self.daemon.wait("state", paused=True)
        self.pad.tap("X")
        self.daemon.wait("pad", button="X", pressed=True)
        self.assertEqual(self.daemon.fired(), [])
        self.daemon.send(cmd="resume")
        self.daemon.wait("state", paused=False)

    def test_mouse_off_stops_clicks(self):
        self.daemon.send(cmd="mouse", on=False)
        self.daemon.wait("state", mouse=False)
        self.pad.tap("A")
        fire = self.daemon.wait("fire", button="A")
        self.assertFalse(fire["ok"])
        self.daemon.send(cmd="mouse", on=True)
        self.daemon.wait("state", mouse=True)

    def test_guide_hold_toggles_the_mouse(self):
        self.pad.tap("GUIDE", hold=0.45)
        self.daemon.wait("state", mouse=False)
        self.pad.tap("GUIDE", hold=0.45)
        self.daemon.wait("state", mouse=True)

    def test_guide_and_back_switch_everything_even_when_off(self):
        self.pad.set("GUIDE", True)
        time.sleep(0.05)
        self.pad.set("BACK", True)
        self.daemon.wait("state", enabled=False)
        self.pad.set("BACK", False)
        self.pad.set("GUIDE", False)
        time.sleep(0.6)  # past holdMs: Guide's hold must not fire either
        self.pad.tap("X")
        self.assertEqual(self.daemon.fired(), [], "fired while switched off")
        self.assertFalse(json.loads(self.daemon.path.read_text())["enabled"], "not saved")
        # Back first this time; the switch works from either end.
        self.pad.set("BACK", True)
        self.pad.set("GUIDE", True)
        self.daemon.wait("state", enabled=True)
        self.pad.set("GUIDE", False)
        self.pad.set("BACK", False)
        self.daemon.drain()
        self.pad.tap("X")
        self.assertEqual(self.daemon.fired(), ["base:Play / pause"])

    def test_back_before_guide_sends_no_escape(self):
        self.pad.set("BACK", True)
        time.sleep(0.04)
        self.pad.set("GUIDE", True)
        self.daemon.wait("state", enabled=False)
        self.pad.set("GUIDE", False)
        self.pad.set("BACK", False)
        try:
            self.assertEqual(self.daemon.fired(), [], "Back ran its action as part of the combo")
        finally:
            self.combo()
            self.daemon.wait("state", enabled=True)
        # On its own, Back still sends Escape: on release, or held, soon after.
        self.daemon.drain()
        self.pad.tap("BACK", hold=0.05)
        self.pad.tap("BACK", hold=0.3)
        self.assertEqual(self.daemon.fired(), ["base:Escape", "base:Escape"])

    def combo(self):
        self.pad.set("GUIDE", True)
        time.sleep(0.05)
        self.pad.set("BACK", True)
        time.sleep(0.05)
        self.pad.set("BACK", False)
        self.pad.set("GUIDE", False)

    def test_combo_never_saves_over_a_broken_edit(self):
        good = self.daemon.path.read_text()
        broken = good.rstrip()[:-1] + ',, "typo"'  # a hand edit gone wrong
        try:
            self.daemon.path.write_text(broken)
            self.daemon.send(cmd="reload")
            self.daemon.wait("error")
            self.combo()
            error = self.daemon.wait("error")
            self.assertIn("not saved", error["message"])
            self.daemon.wait("state", enabled=False)
            self.assertEqual(self.daemon.path.read_text(), broken, "the edit was overwritten")
            # Fixed, and maybe not reloaded yet (the daemon looks again every
            # couple of seconds): the switch lands in the new file either way.
            edited = json.loads(good)
            edited["enabled"] = False  # as switched; that was not saved
            edited["timing"] = {**TIMING, "holdMs": 310}
            self.daemon.write(edited)
            self.combo()
            self.daemon.wait("state", enabled=True)
            self.assertEqual(json.loads(self.daemon.path.read_text()), {**edited, "enabled": True})
        finally:
            self.daemon.path.write_text(good)
            self.daemon.send(cmd="reload")
            self.daemon.wait("reloaded")

    def test_a_failed_reload_is_reported_until_fixed(self):
        good = self.daemon.path.read_text()
        try:
            self.daemon.path.write_text("{")
            self.daemon.send(cmd="reload")
            self.assertIn("keeping the last config", self.daemon.wait("state")["configError"])
            self.daemon.drain()
            self.pad.tap("X")  # the last good config still runs
            self.assertEqual(self.daemon.fired(), ["base:Play / pause"])
        finally:
            self.daemon.path.write_text(good)
            self.daemon.send(cmd="reload")
            self.daemon.wait("reloaded")
        self.assertEqual(self.daemon.wait("state")["configError"], "")

    def test_reload_picks_up_an_edit(self):
        config = json.loads(self.daemon.path.read_text())
        config["layers"] = [{"id": "base", "bindings": {"X": {"keys": "A", "label": "Edited"}}}]
        self.daemon.write(config)
        try:
            self.daemon.send(cmd="reload")
            self.daemon.wait("reloaded")
            self.daemon.drain()
            self.pad.tap("X")
            self.assertEqual(self.daemon.fired(), ["base:Edited"])
        finally:
            self.daemon.write({"device": {"match": [TEST_MARK]}, "timing": TIMING})
            self.daemon.send(cmd="reload")
            self.daemon.wait("reloaded")


class TestBrokenAtStart(unittest.TestCase):
    """A file that will not load at start-up leaves the pad working."""

    def test_runs_on_defaults_and_picks_up_the_fix(self):
        broken = '{"enabled": true, "device": {"match": ["%s"]},, }' % TEST_MARK
        daemon = Daemon(broken)
        try:
            daemon.wait("started")
            self.assertIn("not valid JSON", daemon.wait("error")["message"])
            state = daemon.wait("state")
            self.assertIn("running on the defaults", state["configError"])
            self.assertTrue(state["enabled"])
            time.sleep(0.3)
            self.assertIsNone(daemon.proc.poll(), "the daemon exited")
            self.assertEqual(daemon.path.read_text(), broken, "the file was rewritten")
            # Fixed by hand, with no reload asked for: picked up by itself.
            daemon.write({"device": {"match": [TEST_MARK]}, "timing": TIMING})
            daemon.wait("reloaded", timeout=6)
            self.assertEqual(daemon.wait("state")["configError"], "")
        finally:
            daemon.stop()

    def test_keeps_a_readable_switch(self):
        daemon = Daemon('{"enabled": false, "mouse": {"speed": "fast"}}')
        try:
            state = daemon.wait("state")
            self.assertIn("mouse.speed", state["configError"])
            self.assertFalse(state["enabled"])
        finally:
            daemon.stop()


@needs_uinput
class TestIsolation(unittest.TestCase):
    def test_a_pad_there_at_start_or_regrabbed_is_not_news(self):
        pad = SyntheticPad()
        try:
            daemon = Daemon({"device": {"match": [TEST_MARK]}, "timing": TIMING})
            try:
                self.assertFalse(daemon.wait("connected", timeout=6, name=NAME)["new"])
                # A reload that changes the device settings lets go and finds it again.
                daemon.write({"device": {"match": [TEST_MARK], "grab": True}, "timing": TIMING})
                daemon.send(cmd="reload")
                daemon.wait("disconnected", reason="reconfigured")
                self.assertFalse(daemon.wait("connected", timeout=6, name=NAME)["new"])
            finally:
                daemon.stop()
        finally:
            pad.close()

    def test_default_match_ignores_synthetic_pads(self):
        daemon = Daemon({"timing": TIMING})
        try:
            daemon.wait("started")
            pad = SyntheticPad()
            try:
                time.sleep(2.6)  # past one rescan
                names = [e.get("name") for e in daemon.seen if e["event"] == "connected"]
                self.assertNotIn(NAME, names)
            finally:
                pad.close()
        finally:
            daemon.stop()


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    unittest.main()
