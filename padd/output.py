"""What the daemon drives: a virtual keyboard, a virtual mouse, commands."""

from __future__ import annotations

import math
import os
import subprocess
import time

from . import linux
from .codes import code, with_prefix
from .keymap import combo, key_codes

BUTTON_CODES = {
    "left": code("BTN_LEFT"),
    "right": code("BTN_RIGHT"),
    "middle": code("BTN_MIDDLE"),
    "back": code("BTN_SIDE"),
    "forward": code("BTN_EXTRA"),
}
REL_X, REL_Y = code("REL_X"), code("REL_Y")
REL_WHEEL, REL_HWHEEL = code("REL_WHEEL"), code("REL_HWHEEL")
REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES = code("REL_WHEEL_HI_RES"), code("REL_HWHEEL_HI_RES")
NOTCH = 120  # REL_WHEEL_HI_RES units in one wheel notch


MODIFIERS = {code(f"KEY_{name}") for name in (
    "LEFTMETA", "RIGHTMETA", "LEFTCTRL", "RIGHTCTRL", "LEFTALT", "RIGHTALT", "LEFTSHIFT", "RIGHTSHIFT",
)}


class Keyboard:
    def __init__(self, dry_run: bool = False, record: bool = False):
        self.dry_run = dry_run
        # Every report that would have been sent, for the tests.
        self.sent: list[list[tuple[int, int, int]]] | None = [] if record else None
        # Keys only: a keyboard that also claims mouse or pad buttons confuses
        # libinput's device classification.
        buttons = set(with_prefix("BTN_").values())
        keys = [c for c in key_codes().values() if c > 0 and c not in buttons]
        self.device = linux.UInput("padd virtual keyboard", product=0xCD01, keys=keys)

    def _send(self, events) -> None:
        if self.sent is not None:
            self.sent.append(list(events))
        if not self.dry_run:
            self.device.open()
            self.device.send(events)

    def tap(self, spec: str) -> None:
        """Press a combo the way fingers do: modifiers first, then the key.

        In one report, Hyprland can take the key before the modifier and
        let it through as typing (SUPER+SPACE arriving as a space). So the
        modifiers go down in a report of their own, and come up last.
        """
        codes = combo(spec)
        mods = [c for c in codes if c in MODIFIERS]
        keys = [c for c in codes if c not in MODIFIERS] or mods
        if keys is mods:
            mods = []
        pause = 0 if self.dry_run else 0.012
        if mods:
            self._send([(linux.EV_KEY, c, 1) for c in mods])
            time.sleep(pause)
        self._send([(linux.EV_KEY, c, 1) for c in keys])
        time.sleep(pause)
        self._send([(linux.EV_KEY, c, 0) for c in reversed(keys)])
        if mods:
            time.sleep(pause)
            self._send([(linux.EV_KEY, c, 0) for c in reversed(mods)])

    def close(self) -> None:
        self.device.close()


def run(command: str, dry_run: bool = False) -> None:
    if dry_run:
        return
    subprocess.Popen(
        ["/bin/sh", "-c", command],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def shape(x: float, y: float, rate: float, deadzone: float, curve: float) -> tuple[float, float]:
    """Stick deflection to a velocity: `rate` at full tilt, nothing in the deadzone.

    The response is radial, so diagonals are as fast as the axes, and follows
    `travel ** curve` past the deadzone, so small tilts stay fine.
    """
    magnitude = math.hypot(x, y)
    if magnitude <= deadzone:
        return (0.0, 0.0)
    travel = min(1.0, (magnitude - deadzone) / (1.0 - deadzone))
    speed = rate * travel**curve
    return (x / magnitude * speed, y / magnitude * speed)


class Mouse:
    """Stick motion and clicks on a virtual mouse."""

    def __init__(self, settings: dict, dry_run: bool = False, record: bool = False):
        self.dry_run = dry_run
        # Every report that would have been sent, for the tests.
        self.sent: list[list[tuple[int, int, int]]] | None = [] if record else None
        self.device = linux.UInput(
            "padd virtual mouse",
            product=0xCD02,
            keys=BUTTON_CODES.values(),
            rels=(REL_X, REL_Y, REL_WHEEL, REL_HWHEEL, REL_WHEEL_HI_RES, REL_HWHEEL_HI_RES),
            props=(code("INPUT_PROP_POINTER"),),
        )
        self.on = bool(settings["enabled"])
        self.precise = False
        self._down: set[int] = set()
        self._carry = [0.0, 0.0, 0.0, 0.0]
        self._notches = [0.0, 0.0]
        self._last: float | None = None
        self.configure(settings)

    def configure(self, settings: dict) -> None:
        self.settings = dict(settings)
        self.interval = 1.0 / settings["rateHz"]

    # -- buttons ------------------------------------------------------------

    def _send(self, events) -> None:
        if self.sent is not None and events:
            self.sent.append(list(events))
        if not self.dry_run and events:
            self.device.open()
            self.device.send(events)

    def button(self, name: str, down: bool) -> None:
        button = BUTTON_CODES[name]
        if down == (button in self._down):
            return
        (self._down.add if down else self._down.discard)(button)
        self._send([(linux.EV_KEY, button, 1 if down else 0)])

    def release_all(self) -> None:
        if self._down:
            self._send([(linux.EV_KEY, b, 0) for b in self._down])
            self._down.clear()

    # -- motion -------------------------------------------------------------

    def _vector(self, pads, stick: str | None) -> tuple[float, float]:
        if stick is None:
            return (0.0, 0.0)
        x = sum(pad.stick(stick)[0] for pad in pads)
        y = sum(pad.stick(stick)[1] for pad in pads)
        return (max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y)))

    def _moving(self, pads) -> bool:
        s = self.settings
        return any(
            math.hypot(*self._vector(pads, stick)) > s["deadzone"]
            for stick in (s["move"], s["scroll"])
            if stick
        )

    def next_deadline(self, now: float, pads) -> float | None:
        if not self.on or not self._moving(pads):
            self._last = None
            return None
        return now if self._last is None else self._last + self.interval

    def tick(self, now: float, pads) -> None:
        # The first step of a gesture is one interval long, not the time
        # since the stick last moved; later steps are capped the same way
        # so a stalled loop cannot fling the pointer.
        dt = self.interval if self._last is None else min(now - self._last, 4 * self.interval)
        if dt < self.interval / 2:
            return
        self._last = now
        s = self.settings
        scale = s["precision"] if self.precise else 1.0
        vx, vy = shape(*self._vector(pads, s["move"]), s["speed"] * scale, s["deadzone"], s["curve"])
        sx, sy = shape(
            *self._vector(pads, s["scroll"]),
            s["scrollSpeed"] * NOTCH * scale, s["deadzone"], s["scrollCurve"],
        )
        # Stick up is negative y; wheel up is positive.
        wheel, hwheel = (sy, -sx) if s["naturalScroll"] else (-sy, sx)

        steps = []
        for index, velocity in enumerate((vx, vy, wheel, hwheel)):
            total = self._carry[index] + velocity * dt
            whole = int(total)
            self._carry[index] = total - whole
            steps.append(whole)
        dx, dy, dwheel, dhwheel = steps

        events = []
        if dx:
            events.append((linux.EV_REL, REL_X, dx))
        if dy:
            events.append((linux.EV_REL, REL_Y, dy))
        for axis, amount, hi_res, legacy in (
            (0, dwheel, REL_WHEEL_HI_RES, REL_WHEEL),
            (1, dhwheel, REL_HWHEEL_HI_RES, REL_HWHEEL),
        ):
            if not amount:
                continue
            events.append((linux.EV_REL, hi_res, amount))
            # Clients without hi-res support count whole notches; keep
            # their total in step with the hi-res stream.
            self._notches[axis] += amount
            notches = int(self._notches[axis] / NOTCH)
            if notches:
                self._notches[axis] -= notches * NOTCH
                events.append((linux.EV_REL, legacy, notches))
        self._send(events)

    def close(self) -> None:
        self.release_all()
        self.device.close()


def can_write_uinput() -> bool:
    return os.access("/dev/uinput", os.W_OK)
