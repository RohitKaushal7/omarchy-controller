"""The real daemon, in dry-run mode, against a synthetic uinput pad.

Needs write access to /dev/uinput. The pad's name carries TEST_MARK, so a
daemon already running on this machine never picks it up.
"""

from __future__ import annotations

import io
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
from padd import daemon as padd_daemon
from padd.config import load_or_off
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
            # Fixed, and reloaded as the shell does on save. Waiting matters:
            # a reload lets go of held buttons, and the daemon's own recheck
            # could otherwise land mid-combo and swallow it.
            edited = json.loads(good)
            edited["enabled"] = False  # as switched; that was not saved
            edited["timing"] = {**TIMING, "holdMs": 310}
            self.daemon.write(edited)
            self.daemon.send(cmd="reload")
            self.daemon.wait("reloaded")
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
    """A file that will not load at start-up leaves the controller off."""

    def test_starts_off_and_picks_up_the_fix(self):
        broken = '{"enabled": true, "device": {"match": ["%s"]},, }' % TEST_MARK
        daemon = Daemon(broken)
        try:
            daemon.wait("started")
            self.assertIn("not valid JSON", daemon.wait("error")["message"])
            state = daemon.wait("state")
            self.assertIn("switched off until it is fixed", state["configError"])
            self.assertFalse(state["enabled"], "a broken file switched the controller on")
            self.assertFalse(state["mouse"], "a broken file switched the stick mouse on")
            time.sleep(0.3)
            self.assertIsNone(daemon.proc.poll(), "the daemon exited")
            self.assertEqual(daemon.path.read_text(), broken, "the file was rewritten")
            # Fixed by hand, with no reload asked for: picked up by itself,
            # switch and all.
            daemon.write({"device": {"match": [TEST_MARK]}, "timing": TIMING})
            daemon.wait("reloaded", timeout=6)
            state = daemon.wait("state")
            self.assertEqual(state["configError"], "")
            self.assertTrue(state["enabled"])
        finally:
            daemon.stop()

    def test_off_even_when_the_file_says_on(self):
        daemon = Daemon('{"enabled": true, "mouse": {"speed": "fast"}}')
        try:
            state = daemon.wait("state")
            self.assertIn("mouse.speed", state["configError"])
            self.assertFalse(state["enabled"])
        finally:
            daemon.stop()

    def test_cannot_be_switched_on_until_it_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            # Valid JSON, so the switch could be saved into it, but no config.
            broken = '{"enabled": false, "mouse": {"speed": "fast"}}'
            path.write_text(broken)
            config, error = load_or_off(path)
            out = io.StringIO()
            daemon = padd_daemon.Daemon(config, dry_run=True, out=out, config_error=error)
            daemon.set_enabled(True)  # the toggle combo, were there one
            events = [json.loads(line) for line in out.getvalue().splitlines()]
            self.assertIn("cannot switch on", events[0]["message"])
            self.assertFalse(events[-1]["enabled"])
            self.assertFalse(daemon.live)
            self.assertEqual(path.read_text(), broken, "the switch was saved")
            # Nothing is bound, should anything get past the switch.
            self.assertEqual(daemon.resolver.press("Y", 0.0) + daemon.resolver.release("Y", 0.1), [])
            # Fixed: the file's own config, switch included, takes over.
            # (Matching the test marker, so the rescan opens no real pad.)
            path.write_text(json.dumps({"enabled": False, "device": {"match": [TEST_MARK]},
                                        "mouse": {"speed": 900}}))
            daemon._reload_config()
            self.assertEqual(daemon.config_error, "")
            self.assertFalse(daemon.config.enabled)
            daemon.set_enabled(True)
            self.assertTrue(daemon.live)
            self.assertTrue(json.loads(path.read_text())["enabled"])
            daemon.shutdown()


class TestFileGone(unittest.TestCase):
    """A file that loaded, then went, never brings back the defaults."""

    def daemon(self, path):
        config, error = load_or_off(path)
        self.out = io.StringIO()
        daemon = padd_daemon.Daemon(config, dry_run=True, out=self.out, config_error=error)
        daemon.scanner.pads = lambda match: []  # never open a real pad
        self.addCleanup(daemon.shutdown)
        return daemon

    def events(self):
        return [json.loads(line) for line in self.out.getvalue().splitlines()]

    def test_deleted_while_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            # Switched off, Y rebound; matching the test marker, so no
            # rescan opens a real pad.
            saved = {"version": 2, "enabled": False, "device": {"match": [TEST_MARK]},
                     "layers": [{"id": "base", "bindings": {"Y": {"keys": "A", "label": "Mine"}}}]}
            path.write_text(json.dumps(saved))
            daemon = self.daemon(path)
            path.unlink()  # deleted, moved away, or an editor between unlink and write
            daemon._reload_config()
            self.assertIn("not there any more", daemon.config_error)
            self.assertIn("keeping the last config", daemon.config_error)
            self.assertFalse(daemon.config.enabled, "the defaults switched it back on")
            self.assertEqual(daemon.config.base.bindings["Y"].tap.label, "Mine")
            # The combo's switch is not saved into a new file of its own,
            # which the next reload would fill out with the defaults.
            daemon.set_enabled(True)
            self.assertFalse(os.path.lexists(path), "a new file was made")
            self.assertIn("not saved", [e for e in self.events() if e["event"] == "error"][-1]["message"])
            daemon.set_enabled(False)
            # Back again: loaded as usual.
            path.write_text(json.dumps(saved))
            daemon._reload_config()
            self.assertEqual(daemon.config_error, "")
            self.assertFalse(daemon.config.enabled)

    def test_a_dotfiles_move_leaves_a_link_to_nowhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "dotfiles" / "config.json"
            real.parent.mkdir()
            real.write_text(json.dumps({"version": 2, "enabled": False, "device": {"match": [TEST_MARK]}}))
            link = Path(tmp) / "config.json"
            link.symlink_to(real)
            daemon = self.daemon(link)
            real.parent.rename(Path(tmp) / "elsewhere")
            daemon._reload_config()
            self.assertIn("a link to", daemon.config_error)
            self.assertFalse(daemon.config.enabled)
            daemon.set_enabled(True)
            self.assertFalse(real.parent.exists(), "the link's target was made")

    def test_deleted_while_switched_off_for_a_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{")
            daemon = self.daemon(path)
            path.unlink()
            daemon._reload_config()
            self.assertTrue(daemon.config.stand_in, "the defaults took over")
            self.assertIn("switched off until it is fixed", daemon.config_error)

    def test_no_file_from_the_start_is_a_first_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            daemon = self.daemon(path)
            self.assertTrue(daemon.config.enabled)
            daemon._reload_config()  # still none: still the defaults, no error
            self.assertEqual(daemon.config_error, "")
            daemon.set_enabled(False)  # the first save makes the file
            self.assertFalse(json.loads(path.read_text())["enabled"])


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
