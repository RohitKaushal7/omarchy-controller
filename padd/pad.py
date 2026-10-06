"""Finding controllers and turning their events into named buttons.

Buttons are named after the Xbox layout (see the spec, section 3). Linux
gamepad drivers report that layout's codes whatever the plastic says, so the
names hold for Xbox, PlayStation and most generic pads.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass, field

from . import linux
from .codes import code

BUTTONS = (
    "A", "B", "X", "Y",
    "LB", "RB", "LT", "RT",
    "BACK", "START", "GUIDE",
    "L3", "R3",
    "UP", "DOWN", "LEFT", "RIGHT",
)
STICKS = ("LS", "RS")

# Matched in the test suite's synthetic pads; see `wanted()`.
TEST_MARK = "[padd-test]"

KEY_BUTTONS: dict[int, str] = {
    code("BTN_SOUTH"): "A",
    code("BTN_EAST"): "B",
    # BTN_NORTH and BTN_WEST are the kernel's names for BTN_X and BTN_Y,
    # which xpad, hid-playstation and hid-generic send for the buttons
    # printed X and Y.
    code("BTN_NORTH"): "X",
    code("BTN_WEST"): "Y",
    code("BTN_TL"): "LB",
    code("BTN_TR"): "RB",
    code("BTN_TL2"): "LT",
    code("BTN_TR2"): "RT",
    code("BTN_SELECT"): "BACK",
    code("BTN_START"): "START",
    code("BTN_MODE"): "GUIDE",
    code("BTN_THUMBL"): "L3",
    code("BTN_THUMBR"): "R3",
    code("BTN_DPAD_UP"): "UP",
    code("BTN_DPAD_DOWN"): "DOWN",
    code("BTN_DPAD_LEFT"): "LEFT",
    code("BTN_DPAD_RIGHT"): "RIGHT",
}

HAT = {code("ABS_HAT0X"): ("LEFT", "RIGHT"), code("ABS_HAT0Y"): ("UP", "DOWN")}
TRIGGERS = {code("ABS_Z"): "LT", code("ABS_RZ"): "RT"}
STICK_AXES = {
    code("ABS_X"): ("LS", 0),
    code("ABS_Y"): ("LS", 1),
    code("ABS_RX"): ("RS", 0),
    code("ABS_RY"): ("RS", 1),
}


class Gone(Exception):
    """The device went away (unplugged, out of range, powered off)."""


@dataclass
class Info:
    path: str
    name: str
    phys: str
    vendor: int
    product: int
    buttons: list[str]
    ranges: dict[int, tuple[int, int]] = field(default_factory=dict)

    @property
    def is_pad(self) -> bool:
        return bool(self.buttons)

    def wanted(self, match: list[str]) -> bool:
        name = self.name.lower()
        if TEST_MARK in name:
            # Synthetic pads only ever reach a daemon that asks for them.
            return any(TEST_MARK in m for m in match) and any(m in name for m in match)
        return not match or any(m in name for m in match)

    def describe(self) -> dict:
        return {
            "path": self.path,
            "name": self.name,
            "phys": self.phys,
            "id": f"{self.vendor:04x}:{self.product:04x}",
            "buttons": self.buttons,
        }


def inspect(path: str) -> Info | None:
    try:
        fd = linux.open_node(path)
    except OSError:
        return None
    try:
        keys = linux.capabilities(fd, linux.EV_KEY, linux.KEY_MAX + 1)
        axes = linux.capabilities(fd, linux.EV_ABS, linux.ABS_MAX + 1)
        buttons = sorted(
            {KEY_BUTTONS[k] for k in keys if k in KEY_BUTTONS}
            | {b for a in axes if a in HAT for b in HAT[a]}
            | {TRIGGERS[a] for a in axes if a in TRIGGERS},
            key=BUTTONS.index,
        )
        # A keyboard or mouse can have stray axes; a pad has face buttons.
        if not any(k in KEY_BUTTONS for k in keys):
            buttons = []
        ranges = {}
        for axis in axes:
            if axis in TRIGGERS or axis in STICK_AXES:
                found = linux.abs_range(fd, axis)
                if found:
                    ranges[axis] = found
        _bus, vendor, product = linux.device_ids(fd)
        return Info(
            path, linux.device_name(fd), linux.device_phys(fd), vendor, product,
            buttons, ranges,
        )
    except OSError:
        return None
    finally:
        os.close(fd)


class Scanner:
    """Finds pads, re-inspecting only nodes it has not seen.

    Opening an event node can block for tens of milliseconds, so a rescan
    every couple of seconds must not reopen every node on the system.
    """

    def __init__(self) -> None:
        self._seen: dict[str, tuple[tuple, Info | None]] = {}

    def pads(self, match: list[str]) -> list[Info]:
        found = []
        live = set()
        for path in linux.event_nodes():
            live.add(path)
            try:
                st = os.stat(path)
            except OSError:
                continue
            stamp = (st.st_ino, st.st_rdev, st.st_mtime_ns)
            cached = self._seen.get(path)
            if cached is None or cached[0] != stamp:
                cached = (stamp, inspect(path))
                self._seen[path] = cached
            info = cached[1]
            if info is not None and info.is_pad and info.wanted(match):
                found.append(info)
        for path in list(self._seen):
            if path not in live:
                del self._seen[path]
        return found


class Pad:
    """An open controller."""

    def __init__(self, info: Info, *, trigger_threshold: float, grab: bool):
        self.info = info
        self.path = info.path
        self.name = info.name
        self.threshold = trigger_threshold
        self.fd = linux.open_node(info.path)
        self.grabbed = linux.grab(self.fd, True) if grab else False
        self.sticks = {"LS": [0.0, 0.0], "RS": [0.0, 0.0]}
        self._down: set[str] = set()
        # What was held right after each press in the last read, since one
        # read can carry several events and held() only knows the end.
        self._held_after: dict[str, set[str]] = {}

    def fileno(self) -> int:
        return self.fd

    def close(self) -> None:
        if self.fd < 0:
            return
        if self.grabbed:
            linux.grab(self.fd, False)
        os.close(self.fd)
        self.fd = -1

    def stick(self, name: str) -> tuple[float, float]:
        x, y = self.sticks[name]
        return (x, y)

    def _fraction(self, axis: int, value: int, centred: bool) -> float:
        low, high = self.info.ranges.get(axis, (-32768, 32767) if centred else (0, 255))
        if high <= low:
            return 0.0
        if centred:
            mid = (low + high) / 2
            return max(-1.0, min(1.0, (value - mid) / ((high - low) / 2)))
        return max(0.0, min(1.0, (value - low) / (high - low)))

    def _set(self, button: str, down: bool, out: list) -> None:
        if down and button not in self._down:
            self._down.add(button)
            self._held_after[button] = set(self._down)
            out.append((button, True))
        elif not down and button in self._down:
            self._down.discard(button)
            out.append((button, False))

    def read(self) -> list[tuple[str, bool]]:
        """Drain the node. Returns (button, pressed) in arrival order."""
        out: list[tuple[str, bool]] = []
        while True:
            try:
                data = os.read(self.fd, linux.EVENT_SIZE * 64)
            except BlockingIOError:
                break
            except OSError as exc:
                if exc.errno in (errno.ENODEV, errno.EBADF, errno.EIO):
                    raise Gone(self.path) from exc
                raise
            if not data:
                raise Gone(self.path)
            for ev_type, ev_code, value in linux.unpack_events(data):
                if ev_type == linux.EV_KEY and ev_code in KEY_BUTTONS:
                    if value != 2:  # 2 is the kernel's autorepeat
                        self._set(KEY_BUTTONS[ev_code], value == 1, out)
                elif ev_type == linux.EV_ABS:
                    if ev_code in HAT:
                        negative, positive = HAT[ev_code]
                        self._set(negative, value < 0, out)
                        self._set(positive, value > 0, out)
                    elif ev_code in TRIGGERS:
                        pull = self._fraction(ev_code, value, centred=False)
                        button = TRIGGERS[ev_code]
                        # Let go a little below the press point, so a finger
                        # resting on the threshold cannot chatter.
                        limit = self.threshold * (0.7 if button in self._down else 1.0)
                        self._set(button, pull >= limit, out)
                    elif ev_code in STICK_AXES:
                        stick, index = STICK_AXES[ev_code]
                        self.sticks[stick][index] = self._fraction(ev_code, value, True)
            if len(data) < linux.EVENT_SIZE * 64:
                break
        return out

    def held(self) -> set[str]:
        return set(self._down)

    def held_at(self, button: str) -> set[str]:
        """What was held just after `button` last went down."""
        return set(self._held_after.get(button, self._down))
