"""From button presses to actions: layers, taps, holds, repeats and clicks.

Pure logic with time passed in, so it is tested without a pad or a clock.
The rules are the spec's section 5.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Action, Binding, Config


@dataclass(frozen=True)
class Effect:
    """Something the daemon should do.

    phase is "fire" (run the action once), "down" or "up" (a click starting
    or ending). reason says why it fired: "press", "release", "hold" or
    "repeat".
    """

    phase: str
    action: Action
    button: str
    layer: str
    slot: str
    reason: str


@dataclass
class _Press:
    button: str
    layer: str
    binding: Binding
    hold_at: float | None = None
    repeat_at: float | None = None
    clicking: bool = False


class Resolver:
    def __init__(self, config: Config):
        self._layer_stack: list[str] = []  # layer keys held, oldest first
        self._presses: dict[str, _Press] = {}
        self.configure(config)

    def configure(self, config: Config) -> None:
        self.config = config
        self.hold_time = config.timing["holdMs"] / 1000
        self.repeat_delay = config.timing["repeatDelayMs"] / 1000
        self.repeat_interval = config.timing["repeatMs"] / 1000
        keys = config.layer_keys
        self._layer_stack = [k for k in self._layer_stack if k in keys]

    # -- state --------------------------------------------------------------

    @property
    def layer(self) -> str:
        """The id of the layer presses resolve in right now."""
        keys = self.config.layer_keys
        for key in reversed(self._layer_stack):
            if key in keys:
                return keys[key].id
        return self.config.base.id

    def next_deadline(self) -> float | None:
        times = [
            t
            for press in self._presses.values()
            for t in (press.hold_at, press.repeat_at)
            if t is not None
        ]
        return min(times) if times else None

    # -- input --------------------------------------------------------------

    def _lookup(self, button: str) -> tuple[str, Binding] | None:
        layer = self.config.layer(self.layer)
        binding = layer.bindings.get(button)
        if binding is not None:
            return layer.id, binding
        base = self.config.base
        if layer is not base and button in base.bindings:
            return base.id, base.bindings[button]
        return None

    def press(self, button: str, now: float) -> list[Effect]:
        if button in self.config.layer_keys:
            if button in self._layer_stack:
                self._layer_stack.remove(button)
            self._layer_stack.append(button)
            return []
        if button in self._presses:
            return []
        found = self._lookup(button)
        if found is None:
            return []
        layer, binding = found
        press = _Press(button, layer, binding)
        self._presses[button] = press
        tap, hold = binding.tap, binding.hold
        if tap is not None and tap.kind == "click":
            press.clicking = True
            return [Effect("down", tap, button, layer, "tap", "press")]
        if hold is not None:
            # Wait: a release before hold_time is the tap, reaching it the hold.
            press.hold_at = now + self.hold_time
            return []
        if tap.repeat:
            press.repeat_at = now + self.repeat_delay
        return [Effect("fire", tap, button, layer, "tap", "press")]

    def release(self, button: str, now: float) -> list[Effect]:
        if button in self.config.layer_keys:
            if button in self._layer_stack:
                self._layer_stack.remove(button)
            return []
        press = self._presses.pop(button, None)
        if press is None:
            return []
        tap = press.binding.tap
        if press.clicking:
            return [Effect("up", tap, button, press.layer, "tap", "release")]
        if press.hold_at is not None and tap is not None:
            return [Effect("fire", tap, button, press.layer, "tap", "release")]
        return []

    def tick(self, now: float) -> list[Effect]:
        effects = []
        for press in list(self._presses.values()):
            if press.hold_at is not None and now >= press.hold_at:
                press.hold_at = None
                hold = press.binding.hold
                effects.append(Effect("fire", hold, press.button, press.layer, "hold", "hold"))
            if press.repeat_at is not None and now >= press.repeat_at:
                press.repeat_at = now + self.repeat_interval
                tap = press.binding.tap
                effects.append(Effect("fire", tap, press.button, press.layer, "tap", "repeat"))
        return effects

    def cancel(self, button: str) -> list[Effect]:
        """Forget one press without running its tap or hold.

        Used when the press turns out to be part of the toggle combo. A click
        already down is let go, so no mouse button stays stuck.
        """
        press = self._presses.pop(button, None)
        if press is None or not press.clicking:
            return []
        return [Effect("up", press.binding.tap, button, press.layer, "tap", "release")]

    def reset(self) -> list[Effect]:
        """Forget everything held, ending any click so no button sticks."""
        effects = [
            Effect("up", p.binding.tap, p.button, p.layer, "tap", "release")
            for p in self._presses.values()
            if p.clicking
        ]
        self._presses.clear()
        self._layer_stack.clear()
        return effects
