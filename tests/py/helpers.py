"""Shared by the Python tests: the import path, and a synthetic uinput pad."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from padd import linux  # noqa: E402
from padd.codes import code  # noqa: E402
from padd.pad import TEST_MARK  # noqa: E402

NAME = f"Synthetic pad {TEST_MARK}"

KEYS = {
    "A": "BTN_SOUTH", "B": "BTN_EAST", "X": "BTN_NORTH", "Y": "BTN_WEST",
    "LB": "BTN_TL", "RB": "BTN_TR", "BACK": "BTN_SELECT", "START": "BTN_START",
    "GUIDE": "BTN_MODE", "L3": "BTN_THUMBL", "R3": "BTN_THUMBR",
}
TRIGGERS = {"LT": "ABS_Z", "RT": "ABS_RZ"}
HAT = {"UP": ("ABS_HAT0Y", -1), "DOWN": ("ABS_HAT0Y", 1),
       "LEFT": ("ABS_HAT0X", -1), "RIGHT": ("ABS_HAT0X", 1)}
STICKS = {"LS": ("ABS_X", "ABS_Y"), "RS": ("ABS_RX", "ABS_RY")}


class SyntheticPad:
    """An Xbox-shaped pad on uinput, for driving the real daemon."""

    def __init__(self, name: str = NAME):
        absolutes = {code(a): (0, 255) for a in TRIGGERS.values()}
        absolutes.update({code(a): (-1, 1) for a in ("ABS_HAT0X", "ABS_HAT0Y")})
        for x, y in STICKS.values():
            absolutes[code(x)] = absolutes[code(y)] = (-32768, 32767)
        self.device = linux.UInput(
            name, product=0xCDF0, keys=[code(k) for k in KEYS.values()],
            absolutes=absolutes, settle=0.4,
        )
        self.device.open()

    def set(self, button: str, down: bool) -> None:
        if button in KEYS:
            event = (linux.EV_KEY, code(KEYS[button]), int(down))
        elif button in TRIGGERS:
            event = (linux.EV_ABS, code(TRIGGERS[button]), 255 if down else 0)
        else:
            axis, value = HAT[button]
            event = (linux.EV_ABS, code(axis), value if down else 0)
        self.device.send([event])

    def tap(self, button: str, hold: float = 0.05) -> None:
        self.set(button, True)
        time.sleep(hold)
        self.set(button, False)

    def stick(self, name: str, x: float, y: float) -> None:
        ax, ay = STICKS[name]
        self.device.send([
            (linux.EV_ABS, code(ax), int(x * 32767)),
            (linux.EV_ABS, code(ay), int(y * 32767)),
        ])

    def close(self) -> None:
        self.device.close()
