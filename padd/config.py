"""The config file: layers of button actions, plus device, timing and mouse.

See the spec, section 4, for the format.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .keymap import BadKeys, combo
from .pad import BUTTONS, STICKS

VERSION = 1

CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
DEFAULT_PATH = CONFIG_HOME / "dev.reuk.controller" / "config.json"

KINDS = ("keys", "exec", "click", "mouse")
CLICKS = ("left", "right", "middle", "back", "forward")
MOUSE_ACTIONS = ("precision", "toggle")
SLOTS = ("tap", "hold")

# Pressed together, switches the whole controller off and on, even while off:
# out of the way for a game, back after it, without reaching for the mouse.
DEFAULT_TOGGLE_COMBO = ["GUIDE", "BACK"]

DEFAULT_TIMING = {
    "holdMs": 450,
    "repeatDelayMs": 350,
    "repeatMs": 90,
    "triggerThreshold": 0.3,
    "hintDelayMs": 350,
}

DEFAULT_MOUSE = {
    "enabled": True,
    "move": "LS",
    "scroll": "RS",
    "speed": 1800,
    "curve": 2.2,
    "deadzone": 0.15,
    "scrollSpeed": 22,
    "scrollCurve": 2.0,
    "naturalScroll": False,
    "precision": 0.3,
    "rateHz": 125,
}


def _k(keys, label, repeat=False):
    action = {"keys": keys, "label": label}
    if repeat:
        action["repeat"] = True
    return action


def _move(workspace, label):
    """Move the focused window to a workspace and follow it there."""
    lua = f'hl.dsp.window.move({{ workspace = "{workspace}" }})'
    return {"exec": f"hyprctl dispatch '{lua}'", "label": label}


# Browse with the sticks and face buttons, hold LT for text editing, hold
# RT for the system. A button keeps a related job in every layer: B goes
# back, deletes, and closes; the d-pad moves, jumps words, and moves focus.
DEFAULT_LAYERS = [
    {
        "id": "base",
        "name": "Browse",
        "bindings": {
            "A": {"click": "left", "label": "Click"},
            "B": {"click": "back", "label": "Back"},
            "X": _k("PLAYPAUSE", "Play / pause"),
            "Y": {"exec": "voxtype record toggle", "label": "Dictation"},
            "UP": _k("UP", "Up", True),
            "DOWN": _k("DOWN", "Down", True),
            "LEFT": _k("LEFT", "Left / seek back", True),
            "RIGHT": _k("RIGHT", "Right / seek forward", True),
            # Hold a bumper to take the focused window along: RB gives it a
            # workspace of its own, LB carries it back.
            "LB": {
                "tap": _k("SUPER+SHIFT+TAB", "Previous workspace"),
                "hold": _move("e-1", "Window to previous workspace"),
            },
            "RB": {
                "tap": _k("SUPER+TAB", "Next workspace"),
                "hold": _move("empty", "Window to a new workspace"),
            },
            # The command Omarchy's own SUPER + SPACE runs: typed, the shortcut
            # can reach a text box as a bare space before the menu opens.
            "START": {"exec": "omarchy-menu toggle", "label": "Omarchy menu"},
            "BACK": _k("ESC", "Escape"),
            "L3": {"mouse": "precision", "label": "Precision"},
            "R3": {"click": "right", "label": "Right click"},
            "GUIDE": {
                "tap": {"exec": "omarchy-shell dev.reuk.controller hint", "label": "Show buttons"},
                "hold": {"mouse": "toggle", "label": "Mouse on / off"},
            },
        },
    },
    {
        "id": "text",
        "name": "Text",
        "key": "LT",
        "bindings": {
            "A": _k("RETURN", "Enter"),
            "B": _k("BACKSPACE", "Backspace", True),
            "X": _k("CTRL+BACKSPACE", "Delete word", True),
            "Y": _k("CTRL+Z", "Undo"),
            "LEFT": _k("CTRL+LEFT", "Word left", True),
            "RIGHT": _k("CTRL+RIGHT", "Word right", True),
            "UP": _k("HOME", "Start of line"),
            "DOWN": _k("END", "End of line"),
            "LB": _k("SUPER+C", "Copy"),
            "RB": _k("SUPER+V", "Paste"),
            "START": _k("CTRL+A", "Select all"),
            "BACK": _k("TAB", "Tab"),
        },
    },
    {
        "id": "system",
        "name": "System",
        "key": "RT",
        "bindings": {
            "A": _k("F", "Video fullscreen"),
            "B": {"hold": _k("SUPER+W", "Close window")},
            "X": _k("NEXTSONG", "Next track"),
            "Y": {"exec": "omarchy capture screenshot fullscreen", "label": "Screenshot"},
            "UP": _k("VOLUMEUP", "Volume up", True),
            "DOWN": _k("VOLUMEDOWN", "Volume down", True),
            "LEFT": _k("SUPER+LEFT", "Focus left window"),
            "RIGHT": _k("SUPER+RIGHT", "Focus right window"),
            "LB": _k("CTRL+SHIFT+TAB", "Previous tab"),
            "RB": _k("CTRL+TAB", "Next tab"),
            "START": _k("SUPER+F", "Window fullscreen"),
            "BACK": {
                "tap": _k("SUPER+CTRL+TAB", "Former workspace"),
                "hold": _k("SUPER+CTRL+L", "Lock"),
            },
        },
    },
]


class ConfigError(ValueError):
    pass


@dataclass
class Action:
    kind: str
    value: str
    label: str = ""
    repeat: bool = False

    @classmethod
    def parse(cls, raw, where: str) -> "Action":
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: an action is an object")
        kinds = [k for k in KINDS if k in raw]
        if len(kinds) != 1:
            raise ConfigError(f"{where}: needs exactly one of {', '.join(KINDS)}")
        kind = kinds[0]
        value = raw[kind]
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{where}.{kind} must be a non-empty string")
        value = value.strip()
        if kind == "keys":
            try:
                combo(value)
            except BadKeys as exc:
                raise ConfigError(f"{where}.keys: {exc}") from None
        elif kind == "click" and value not in CLICKS:
            raise ConfigError(f"{where}.click must be one of {', '.join(CLICKS)}")
        elif kind == "mouse" and value not in MOUSE_ACTIONS:
            raise ConfigError(f"{where}.mouse must be one of {', '.join(MOUSE_ACTIONS)}")
        repeat = bool(raw.get("repeat", False))
        if repeat and kind != "keys":
            raise ConfigError(f"{where}: only keys actions repeat")
        label = raw.get("label", "")
        if not isinstance(label, str):
            raise ConfigError(f"{where}.label must be a string")
        return cls(kind, value, label.strip(), repeat)

    def to_json(self) -> dict:
        out: dict = {self.kind: self.value}
        if self.repeat:
            out["repeat"] = True
        if self.label:
            out["label"] = self.label
        return out

    def summary(self) -> str:
        if self.kind == "keys":
            return self.value
        if self.kind == "exec":
            return f"$ {self.value}"
        if self.kind == "click":
            return f"{self.value} click"
        return f"mouse {self.value}"


@dataclass
class Binding:
    tap: Action | None = None
    hold: Action | None = None

    @classmethod
    def parse(cls, raw, where: str) -> "Binding":
        if not isinstance(raw, dict):
            raise ConfigError(f"{where}: a binding is an object")
        if "tap" in raw or "hold" in raw:
            extra = set(raw) - set(SLOTS)
            if extra:
                raise ConfigError(f"{where}: unexpected {', '.join(sorted(extra))}")
            tap = Action.parse(raw["tap"], f"{where}.tap") if raw.get("tap") else None
            hold = Action.parse(raw["hold"], f"{where}.hold") if raw.get("hold") else None
        else:
            tap, hold = Action.parse(raw, where), None
        binding = cls(tap, hold)
        binding.check(where)
        return binding

    def check(self, where: str) -> None:
        if self.tap is None and self.hold is None:
            raise ConfigError(f"{where}: binds nothing")
        if self.hold is not None and self.hold.repeat:
            raise ConfigError(f"{where}.hold: a hold fires once and cannot repeat")
        if self.hold is not None and self.hold.kind == "click":
            raise ConfigError(f"{where}.hold: a click follows the button and cannot be a hold")
        if self.tap is not None and self.hold is not None:
            if self.tap.kind == "click" or self.tap.repeat:
                raise ConfigError(
                    f"{where}: a click or repeating tap starts on press, so it "
                    "cannot share a button with a hold"
                )

    def slot(self, name: str) -> Action | None:
        return self.tap if name == "tap" else self.hold

    def to_json(self) -> dict:
        if self.hold is None and self.tap is not None:
            return self.tap.to_json()
        out = {}
        if self.tap is not None:
            out["tap"] = self.tap.to_json()
        if self.hold is not None:
            out["hold"] = self.hold.to_json()
        return out


@dataclass
class Layer:
    id: str
    name: str
    key: str | None
    bindings: dict[str, Binding] = field(default_factory=dict)

    def to_json(self) -> dict:
        out: dict = {"id": self.id, "name": self.name}
        if self.key:
            out["key"] = self.key
        out["bindings"] = {
            button: self.bindings[button].to_json()
            for button in BUTTONS
            if button in self.bindings
        }
        return out


def _button(raw, where: str) -> str:
    name = str(raw).strip().upper()
    if name not in BUTTONS:
        raise ConfigError(f"{where}: unknown button {raw!r} (one of {' '.join(BUTTONS)})")
    return name


def _number(section: dict, key: str, where: str, low=None, high=None) -> float:
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}.{key} must be a number")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ConfigError(f"{where}.{key} must be between {low} and {high}")
    return value


class Config:
    def __init__(self, raw: dict | None = None, path: Path | None = None):
        raw = copy.deepcopy(raw or {})
        self.path = Path(path or DEFAULT_PATH)
        if raw.get("version", VERSION) != VERSION:
            raise ConfigError(f"unsupported config version {raw.get('version')!r}")
        self.enabled = bool(raw.get("enabled", True))

        combo_raw = raw.get("toggleCombo", DEFAULT_TOGGLE_COMBO)
        if not isinstance(combo_raw, list):
            raise ConfigError("toggleCombo must be a list of buttons, or [] for none")
        self.toggle_combo = [_button(b, "toggleCombo") for b in combo_raw]
        if len(self.toggle_combo) == 1 or len(set(self.toggle_combo)) != len(self.toggle_combo):
            raise ConfigError("toggleCombo needs two or more different buttons, or [] for none")

        device = raw.get("device") or {}
        match = device.get("match", [])
        if isinstance(match, str):
            match = [match]
        if not isinstance(match, list) or not all(isinstance(m, str) for m in match):
            raise ConfigError("device.match must be a list of strings")
        self.device = {"match": [m.lower() for m in match], "grab": bool(device.get("grab", False))}

        self.timing = {**DEFAULT_TIMING, **(raw.get("timing") or {})}
        for key in ("holdMs", "repeatDelayMs", "repeatMs", "hintDelayMs"):
            _number(self.timing, key, "timing", 10, 10000)
        _number(self.timing, "triggerThreshold", "timing", 0.05, 0.95)

        self.mouse = {**DEFAULT_MOUSE, **(raw.get("mouse") or {})}
        self.mouse["enabled"] = bool(self.mouse["enabled"])
        self.mouse["naturalScroll"] = bool(self.mouse["naturalScroll"])
        for key in ("move", "scroll"):
            stick = self.mouse[key]
            if stick in (None, ""):
                self.mouse[key] = None
            elif str(stick).upper() in STICKS:
                self.mouse[key] = str(stick).upper()
            else:
                raise ConfigError(f"mouse.{key} must be LS, RS or null")
        if self.mouse["move"] and self.mouse["move"] == self.mouse["scroll"]:
            raise ConfigError("mouse.move and mouse.scroll must be different sticks")
        for key in ("speed", "curve", "scrollSpeed", "scrollCurve", "rateHz"):
            _number(self.mouse, key, "mouse", 0.01, 100000)
        _number(self.mouse, "deadzone", "mouse", 0, 0.9)
        _number(self.mouse, "precision", "mouse", 0.01, 1)

        layers = raw.get("layers")
        if layers is None:
            layers = copy.deepcopy(DEFAULT_LAYERS)
        if not isinstance(layers, list) or not layers:
            raise ConfigError("layers must be a non-empty list")
        self.layers: list[Layer] = []
        ids, keys = set(), {}
        for index, item in enumerate(layers):
            where = f"layers[{index}]"
            if not isinstance(item, dict):
                raise ConfigError(f"{where} must be an object")
            layer_id = str(item.get("id") or "").strip()
            if not layer_id:
                raise ConfigError(f"{where} needs an id")
            if layer_id in ids:
                raise ConfigError(f"{where}: layer id {layer_id!r} is used twice")
            ids.add(layer_id)
            key = item.get("key")
            if index == 0 and key:
                raise ConfigError(f"{where}: the first layer is the base and has no key")
            if index > 0:
                if not key:
                    raise ConfigError(f"{where}: needs a key, the button that holds it")
                key = _button(key, f"{where}.key")
                if key in keys:
                    raise ConfigError(f"{where}: {key} already holds layer {keys[key]!r}")
                keys[key] = layer_id
            raw_bindings = item.get("bindings") or {}
            if not isinstance(raw_bindings, dict):
                raise ConfigError(f"{where}.bindings must be an object")
            bindings = {}
            for button, raw_binding in raw_bindings.items():
                name = _button(button, f"{where}.bindings")
                bindings[name] = Binding.parse(raw_binding, f"{where}.bindings.{name}")
            self.layers.append(
                Layer(layer_id, str(item.get("name") or layer_id), key or None, bindings)
            )
        for layer in self.layers:
            for key in keys:
                if key in layer.bindings:
                    raise ConfigError(
                        f"layer {layer.id!r} binds {key}, which holds layer "
                        f"{keys[key]!r}; a layer key cannot also have an action"
                    )

    # -- lookups ------------------------------------------------------------

    @property
    def base(self) -> Layer:
        return self.layers[0]

    @property
    def layer_keys(self) -> dict[str, Layer]:
        return {layer.key: layer for layer in self.layers if layer.key}

    def layer(self, layer_id: str) -> Layer:
        for layer in self.layers:
            if layer.id == layer_id:
                return layer
        raise ConfigError(f"no layer {layer_id!r}")

    # -- editing ------------------------------------------------------------

    def bind(self, layer_id: str, button: str, slot: str, action: Action) -> None:
        layer = self.layer(layer_id)
        button = _button(button, "button")
        if button in self.layer_keys:
            raise ConfigError(f"{button} holds a layer and cannot have an action")
        if slot not in SLOTS:
            raise ConfigError(f"slot must be tap or hold, not {slot!r}")
        old = layer.bindings.get(button) or Binding()
        new = Binding(old.tap, old.hold)
        setattr(new, slot, action)
        new.check(f"{layer.id}.{button}")
        layer.bindings[button] = new

    def unbind(self, layer_id: str, button: str, slot: str | None = None) -> bool:
        layer = self.layer(layer_id)
        button = _button(button, "button")
        binding = layer.bindings.get(button)
        if binding is None:
            return False
        if slot is None:
            del layer.bindings[button]
            return True
        if binding.slot(slot) is None:
            return False
        setattr(binding, slot, None)
        if binding.tap is None and binding.hold is None:
            del layer.bindings[button]
        return True

    # -- persistence --------------------------------------------------------

    def to_json(self) -> dict:
        return {
            "version": VERSION,
            "enabled": self.enabled,
            "toggleCombo": self.toggle_combo,
            "device": self.device,
            "timing": self.timing,
            "mouse": self.mouse,
            "layers": [layer.to_json() for layer in self.layers],
        }

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(self.to_json(), indent=2) + "\n"
        handle, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".config.", suffix=".json")
        try:
            with os.fdopen(handle, "w") as out:
                out.write(text)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, self.path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        return self.path


def load(path: Path | str | None = None) -> Config:
    target = Path(path or DEFAULT_PATH)
    if not target.exists():
        return Config({}, target)
    try:
        raw = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{target}: not valid JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{target}: the top level must be an object")
    return Config(raw, target)
