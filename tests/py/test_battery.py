"""A pad's battery from sysfs, and the one "battery low" per discharge."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT  # noqa: F401
from padd.config import Config
from padd.pad import Battery, find_battery

HID = "devices/pci0000:00/usb1/1-2/1-2:1.0/0005:045E:0B13.0003"


class FakeSysfs:
    """Just enough of /sys: an event node under a HID device, and supplies."""

    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        hid = self.root / HID
        (hid / "input" / "input25" / "event7").mkdir(parents=True)
        (self.root / "class" / "input").mkdir(parents=True)
        (self.root / "class" / "power_supply").mkdir(parents=True)
        (self.root / "class" / "input" / "event7").symlink_to(hid / "input" / "input25" / "event7")
        (hid / "input" / "input25" / "event7" / "device").symlink_to(hid / "input" / "input25")
        # The laptop's own battery, which is nobody's pad.
        self.supply("BAT0", "devices/LNXSYSTM:00/PNP0C0A:03", capacity=59, status="Discharging", scope=None)

    def supply(self, name, owner, *, capacity=80, status="Discharging", kind="Battery", scope="Device"):
        path = self.root / owner / "power_supply" / name
        path.mkdir(parents=True)
        (path / "device").symlink_to(self.root / owner)
        (path / "type").write_text(kind + "\n")
        if scope:
            (path / "scope").write_text(scope + "\n")
        (self.root / "class" / "power_supply" / name).symlink_to(path)
        self.set(name, capacity, status)
        return path

    def set(self, name, capacity, status):
        path = self.root / "class" / "power_supply" / name
        (path / "capacity").write_text(f"{capacity}\n")
        (path / "status").write_text(status + "\n")

    def close(self):
        self.dir.cleanup()


class TestFind(unittest.TestCase):
    def setUp(self):
        self.sys = FakeSysfs()
        self.addCleanup(self.sys.close)

    def test_no_battery_reported(self):
        self.assertEqual(find_battery("/dev/input/event7", str(self.sys.root)), "")
        battery = Battery("/dev/input/event7", str(self.sys.root))
        self.assertFalse(battery.poll())
        self.assertIsNone(battery.level)

    def test_the_supply_of_the_pads_device(self):
        # Another pad's battery, then this one's, as xpadneo names them.
        self.sys.supply("xpadneo-other", "devices/pci0000:00/usb1/1-3/1-3:1.0/0005:045E:0B13.0004")
        mine = self.sys.supply("xpadneo-mine", HID, capacity=64)
        self.assertEqual(os.path.realpath(find_battery("/dev/input/event7", str(self.sys.root))),
                         os.path.realpath(mine))
        battery = Battery("/dev/input/event7", str(self.sys.root))
        battery.poll()
        self.assertEqual((battery.level, battery.status), (64, "Discharging"))

    def test_not_yet_reported(self):
        # hid-input, before the pad's first battery report.
        self.sys.supply("hid-pad-battery", HID, capacity=0, status="Unknown")
        battery = Battery("/dev/input/event7", str(self.sys.root))
        battery.poll()
        self.assertIsNone(battery.level)

    def test_a_battery_that_appears_later(self):
        battery = Battery("/dev/input/event7", str(self.sys.root))
        battery.poll()
        self.sys.supply("ps-controller-battery", HID, capacity=40)
        battery.poll()
        self.assertEqual(battery.level, 40)


class TestLow(unittest.TestCase):
    def test_once_per_discharge(self):
        sys_ = FakeSysfs()
        self.addCleanup(sys_.close)
        sys_.supply("pad", HID, capacity=30)
        battery = Battery("/dev/input/event7", str(sys_.root))

        def at(capacity, status="Discharging"):
            sys_.set("pad", capacity, status)
            return battery.poll()

        self.assertEqual([at(30), at(16), at(15), at(14), at(9), at(16), at(12)],
                         [False, False, True, False, False, False, False])
        # Charging re-arms it; so does reading well above the line again.
        self.assertEqual([at(13, "Charging"), at(12), at(25), at(15)], [False, True, False, True])
        # Plugged in low: said straight away, once.
        battery = Battery("/dev/input/event7", str(sys_.root))
        self.assertEqual([at(8), at(8)], [True, False])


class FakePad:
    def __init__(self, name, battery):
        self.name, self.battery, self.grabbed = name, battery, False


class TestDaemonEvents(unittest.TestCase):
    def test_state_carries_the_level_and_low_is_said_once(self):
        from padd.daemon import Daemon

        sys_ = FakeSysfs()
        self.addCleanup(sys_.close)
        sys_.supply("pad", HID, capacity=50)
        out = io.StringIO()
        daemon = Daemon(Config({}), dry_run=True, out=out)
        daemon.pads["/dev/input/event7"] = FakePad("Pad", Battery("/dev/input/event7", str(sys_.root)))

        def events():
            lines = [json.loads(line) for line in out.getvalue().splitlines()]
            out.seek(0)
            out.truncate()
            return lines

        daemon.poll_battery()
        state = events()[-1]
        self.assertEqual((state["event"], state["battery"], state["batteryStatus"]), ("state", 50, "Discharging"))
        daemon.poll_battery()
        self.assertEqual(events(), [], "nothing changed, nothing said")
        sys_.set("pad", 14, "Discharging")
        daemon.poll_battery()
        self.assertEqual([e["event"] for e in events()], ["batteryLow", "state"])
        sys_.set("pad", 13, "Discharging")
        daemon.poll_battery()
        self.assertEqual([e["event"] for e in events()], ["state"])


if __name__ == "__main__":
    unittest.main()
