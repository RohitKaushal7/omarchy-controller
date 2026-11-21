"""The config file: layers of button actions, plus device, timing and mouse.

See the spec, section 4, for the format.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import stat
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from .keymap import BadKeys, combo
from .pad import BUTTONS, STICKS

# Version 1 lists every layer in full, and is taken as it is. Version 2,
# which save() writes, holds only what differs from the defaults, so a
# default added or changed in an update still reaches the user.
VERSION = 2
VERSIONS = (1, 2)

# How long a writer waits for another to finish with the file, at most.
LOCK_WAIT_S = 5.0

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
    # A toggle combo button waits this long, or until it is let go, before
    # its tap fires: time for the rest of the combo to join.
    "comboMs": 150,
}

DEFAULT_DEVICE = {"match": [], "grab": False}

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


# Omarchy's dictation command runs whichever backend the user picked in
# Setup > Defaults > Dictation (voxtype, superwhisper, ...).
DICTATION = "omarchy-dictation toggle"
# What 0.1 shipped instead. Version 1 files hold every default of their
# day, so this one is read as the default it was, not a choice.
OLD_DICTATION = "voxtype record toggle"


def _old_defaults_updated(layers):
    """Version 1 layers with 0.1's dictation command swapped for Omarchy's."""
    def walk(node):
        if isinstance(node, dict):
            if node.get("exec") == OLD_DICTATION:
                node = {**node, "exec": DICTATION}
            return {key: walk(value) for key, value in node.items()}
        if isinstance(node, list):
            return [walk(item) for item in node]
        return node
    return walk(layers)


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
            "Y": {"exec": DICTATION, "label": "Dictation"},
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


def _merge_layers(changes) -> list:
    """The default layers with a version 2 file's changes applied.

    An entry whose id names a default layer changes it: its name, its key,
    and each binding given (null removes that button's default). An entry
    with `"removed": true` drops that default layer. Any other entry is a
    new layer, given in full, after the defaults.
    """
    layers = copy.deepcopy(DEFAULT_LAYERS)
    if changes is None:
        return layers
    if not isinstance(changes, list):
        raise ConfigError("layers must be a list")
    by_id = {layer["id"]: layer for layer in layers}
    removed = set()
    for index, item in enumerate(changes):
        where = f"layers[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{where} must be an object")
        layer_id = str(item.get("id") or "").strip()
        if not layer_id:
            raise ConfigError(f"{where} needs an id")
        bindings = item.get("bindings") or {}
        if not isinstance(bindings, dict):
            raise ConfigError(f"{where}.bindings must be an object")
        target = by_id.get(layer_id)
        if target is None and item.get("removed") is True:
            continue  # not a default layer, so there is nothing to remove
        if target is None:
            fresh = {k: v for k, v in item.items() if k != "removed"}
            fresh["bindings"] = {b: v for b, v in bindings.items() if v is not None}
            layers.append(fresh)
            by_id[layer_id] = fresh
            continue
        if item.get("removed") is True:
            removed.add(layer_id)
            continue
        for key in ("name", "key"):
            if key in item:
                target[key] = item[key]
        for button, binding in bindings.items():
            name = _button(button, f"{where}.bindings")
            if binding is None:
                target["bindings"].pop(name, None)
            else:
                target["bindings"][name] = binding
    return [layer for layer in layers if layer["id"] not in removed]


class Config:
    def __init__(self, raw: dict | None = None, path: Path | None = None):
        raw = copy.deepcopy(raw or {})
        self.path = Path(path or DEFAULT_PATH)
        # A file with no version predates version 2, so it is a full one.
        version = raw.get("version", 1)
        if version not in VERSIONS:
            raise ConfigError(f"unsupported config version {version!r}")
        self.enabled = bool(raw.get("enabled", True))
        # Set on switched_off()'s config, which stands in for a file that
        # would not load: it can never be switched on.
        self.stand_in = False
        # Set by load() on a config read from its file (not the defaults of
        # a first run): once there was a file, its going away is an error.
        self.from_file = False

        combo_raw = raw.get("toggleCombo", DEFAULT_TOGGLE_COMBO)
        if not isinstance(combo_raw, list):
            raise ConfigError("toggleCombo must be a list of buttons, or [] for none")
        self.toggle_combo = [_button(b, "toggleCombo") for b in combo_raw]
        if len(self.toggle_combo) == 1 or len(set(self.toggle_combo)) != len(self.toggle_combo):
            raise ConfigError("toggleCombo needs two or more different buttons, or [] for none")

        device = raw.get("device") or {}
        if not isinstance(device, dict):
            raise ConfigError("device must be an object")
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
        _number(self.timing, "comboMs", "timing", 0, 1000)

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

        if version == 1:
            layers = raw.get("layers")
            if layers is None:
                layers = copy.deepcopy(DEFAULT_LAYERS)
            else:
                layers = _old_defaults_updated(layers)
        else:
            layers = _merge_layers(raw.get("layers"))
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
        """Everything, defaults filled in: a full (version 1) config."""
        return {
            "version": 1,
            "enabled": self.enabled,
            "toggleCombo": self.toggle_combo,
            "device": self.device,
            "timing": self.timing,
            "mouse": self.mouse,
            "layers": [layer.to_json() for layer in self.layers],
        }

    def changes(self) -> dict:
        """What differs from the defaults: the version 2 file save() writes."""
        out: dict = {"version": 2, "enabled": self.enabled}
        if self.toggle_combo != DEFAULT_TOGGLE_COMBO:
            out["toggleCombo"] = self.toggle_combo
        for name, current, default in (
            ("device", self.device, DEFAULT_DEVICE),
            ("timing", self.timing, DEFAULT_TIMING),
            ("mouse", self.mouse, DEFAULT_MOUSE),
        ):
            changed = {k: v for k, v in current.items() if default.get(k) != v}
            if changed:
                out[name] = changed
        defaults = {layer.id: layer for layer in Config({}).layers}
        layers = []
        ids = {layer.id for layer in self.layers}
        for layer in self.layers:
            default = defaults.get(layer.id)
            if default is None:
                layers.append(layer.to_json())
                continue
            entry: dict = {"id": layer.id}
            if layer.name != default.name:
                entry["name"] = layer.name
            if layer.key != default.key:
                entry["key"] = layer.key
            bindings = {}
            for button in BUTTONS:
                mine, theirs = layer.bindings.get(button), default.bindings.get(button)
                if mine != theirs:
                    bindings[button] = mine.to_json() if mine else None
            if bindings:
                entry["bindings"] = bindings
            if len(entry) > 1:
                layers.append(entry)
        layers += [{"id": i, "removed": True} for i in defaults if i not in ids]
        if layers:
            out["layers"] = layers
        return out

    def file_json(self) -> dict:
        """What save() writes: the changes alone when they say it all.

        Layers reordered, or a base layer of another name, cannot be said
        as changes to the defaults; such a config is written in full.
        """
        changes = self.changes()
        try:
            if Config(changes).to_json() == self.to_json():
                return changes
        except ConfigError:
            pass
        return self.to_json()

    def save(self, full: bool = False) -> Path:
        """Write this config out. Hold editing() from before the load that
        made it, or a write made meanwhile (the switch) is undone."""
        data = self.to_json() if full else self.file_json()
        return write(self.path, data)


def switched_off(path: Path | str | None = None) -> Config:
    """What the daemon runs while the file will not load at start-up: nothing.

    Off, with nothing bound, the stick mouse off, no grab and no toggle
    combo. The file could not say what its owner wants, so nothing is
    guessed: a pad they switched off stays off, and the shipped bindings
    never land on buttons they rebound. Only a fixed file switches it on.
    """
    config = Config(
        {
            "enabled": False,
            "toggleCombo": [],
            "mouse": {"enabled": False},
            "layers": [{"id": "base", "name": "Browse", "bindings": {}}],
        },
        path,
    )
    config.stand_in = True
    return config


def load_or_off(path: Path | str | None = None) -> tuple[Config, str]:
    """The config and "", or switched_off() and why the file would not load.

    For starting up: a missing file is the defaults (a first run), but one
    that is there and will not load leaves the controller off until it is
    fixed. The file is left for its owner to fix.
    """
    target = Path(path or DEFAULT_PATH)
    try:
        return load(target), ""
    except ConfigError as exc:
        return switched_off(target), str(exc)


_locks = threading.local()  # lock file -> depth, for the thread holding it


def absent(path: Path | str) -> bool:
    """Whether nothing at all is at `path`: a first run, for the defaults.

    False when something is there, even a link that points nowhere. Raises
    ConfigError when it cannot be told (no permission on a directory, a
    link loop) or when the way to it is broken (a directory on the way is a
    link to nowhere, or a file): that is a move or a mistake, and running,
    or writing, as on a first run would be a guess.
    """
    target = Path(os.path.abspath(path))
    try:
        os.lstat(target)
        return False
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ConfigError(f"{target}: cannot be read: {exc.strerror or exc}") from None
    for parent in target.parents:
        try:
            os.lstat(parent)
        except FileNotFoundError:
            continue  # missing too; made on the first save
        except OSError as exc:
            raise ConfigError(f"{target}: cannot be read: {exc.strerror or exc}") from None
        try:
            is_dir = stat.S_ISDIR(os.stat(parent).st_mode)
        except OSError as exc:
            raise ConfigError(
                f"{target}: {parent} is a link to {os.path.realpath(parent)}, "
                f"which cannot be reached: {exc.strerror or exc}"
            ) from None
        if not is_dir:
            raise ConfigError(f"{target}: {parent} is not a directory")
        return True
    return True


def _real(path: Path | str) -> Path:
    """The real file behind `path`, to write or to lock beside.

    A symlinked config (dotfiles) is written through to its target. Only
    for a first run (nothing at `path`, see absent()) are missing
    directories made; a link to nowhere is never followed into creating
    its target or the directories above it.
    """
    target = Path(path)
    if absent(target):
        real = Path(os.path.realpath(target))
        real.parent.mkdir(parents=True, exist_ok=True)
        return real
    try:
        os.stat(target)
    except OSError as exc:
        raise ConfigError(
            f"{target} is a link to {os.path.realpath(target)}, which cannot be "
            f"reached ({exc.strerror or exc}); fix or remove the link"
        ) from None
    return Path(os.path.realpath(target))


def lock_path(path: Path | str) -> Path:
    """The lock beside the file itself, for a config that is a symlink."""
    real = Path(os.path.realpath(path))
    return real.parent / f".{real.name}.lock"


@contextmanager
def editing(path: Path | str | None = None, wait: float | None = None):
    """Hold the config's lock across a read, a change and the write.

    Every writer (the daemon's switch, each padctl edit) reads the whole
    file and writes it back; two at once would have the later write undo
    the earlier. They take turns on an advisory flock of a lock file next
    to the real file. Held already by this thread, it is held again (a
    save inside an edit). Waits `wait` seconds (LOCK_WAIT_S) at most, then
    raises ConfigError, so a stuck writer cannot hang another for good.
    """
    target = Path(path or DEFAULT_PATH)
    real = _real(target)
    lock = real.parent / f".{real.name}.lock"
    held = _locks.__dict__.setdefault("depth", {})
    key = str(lock)
    if held.get(key):
        held[key] += 1
        try:
            yield
        finally:
            held[key] -= 1
        return
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        deadline = time.monotonic() + (LOCK_WAIT_S if wait is None else wait)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ConfigError(
                        f"{target} is being saved by something else (lock {lock}); try again"
                    ) from None
                time.sleep(0.02)
        held[key] = 1
        try:
            yield
        finally:
            del held[key]
    finally:
        os.close(fd)  # and with it the lock


def save_switch(path: Path | str, on: bool, create: bool = True) -> Path:
    """Set `enabled` in the file on disk, leaving the rest as it is there.

    For the toggle combo, which flips the switch from the daemon: its copy
    of the config can be older than the file (a hand edit not reloaded yet,
    or one that failed to load), and saving that copy would undo the edit.
    A file that is not JSON is left alone, and ConfigError says why. The
    lock keeps a padctl edit from reading before this and writing after.
    With no file, one holding just the switch is made, unless `create` is
    false (the daemon's config came from a file that has since gone: a new
    file would bring back the defaults), when ConfigError says so.
    """
    path = Path(path)
    with editing(path):
        if absent(path):
            if not create:
                raise ConfigError(f"{path} is not there any more")
            return write(path, {"version": VERSION, "enabled": bool(on)})
        try:
            raw = json.loads(path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc}") from None
        if not isinstance(raw, dict):
            raise ConfigError(f"{path}: the top level must be an object")
        raw["enabled"] = bool(on)
        return write(path, raw)


def write(path: Path, data: dict) -> Path:
    """Write the file atomically: a reader sees the old one or the new one.

    A symlinked config (dotfiles) is written through to its target, so the
    link survives; the temp file sits next to the target for the rename.
    A link to nowhere is refused (see _real()). It takes the lock itself,
    but whatever read the file to make `data` must hold it from before
    that read: see editing().
    """
    with editing(path):
        path = _real(path)
        text = json.dumps(data, indent=2) + "\n"
        handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".config.", suffix=".json")
        try:
            with os.fdopen(handle, "w") as out:
                out.write(text)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temp, path)
        except BaseException:
            try:
                os.unlink(temp)
            except OSError:
                pass
            raise
        return path


def load(path: Path | str | None = None, missing_ok: bool = True) -> Config:
    """The config in the file, or the defaults when nothing is there.

    Only nothing at all is a first run: a link to nowhere, a link loop, or
    a directory that cannot be searched raises ConfigError, as does a
    missing file when `missing_ok` is false (a reload, after a file).
    """
    target = Path(path or DEFAULT_PATH)
    if absent(target):
        if not missing_ok:
            raise ConfigError(f"{target} is not there any more")
        return Config({}, target)
    try:
        raw = json.loads(target.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{target}: not valid JSON: {exc}") from None
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{target}: not UTF-8 text: {exc}") from None
    except RecursionError:
        raise ConfigError(f"{target}: nested too deeply") from None
    except OSError as exc:  # a directory, no read permission, a link to nowhere
        where = f" (a link to {os.path.realpath(target)})" if target.is_symlink() else ""
        raise ConfigError(f"{target}{where}: cannot be read: {exc.strerror or exc}") from None
    if not isinstance(raw, dict):
        raise ConfigError(f"{target}: the top level must be an object")
    config = Config(raw, target)
    config.from_file = True
    return config
