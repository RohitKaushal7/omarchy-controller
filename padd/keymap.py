"""Key combo strings, such as "SUPER+SHIFT+RETURN", to key codes."""

from __future__ import annotations

from functools import lru_cache

from .codes import with_prefix

# Spellings people use, and the ones Hyprland config uses, on top of the
# kernel's own KEY_* names.
ALIASES = {
    "SUPER": "LEFTMETA", "META": "LEFTMETA", "WIN": "LEFTMETA", "MOD": "LEFTMETA",
    "CTRL": "LEFTCTRL", "CONTROL": "LEFTCTRL",
    "ALT": "LEFTALT",
    "SHIFT": "LEFTSHIFT",
    "ALTGR": "RIGHTALT",
    "RETURN": "ENTER",
    "ESCAPE": "ESC",
    "DEL": "DELETE",
    "INS": "INSERT",
    "PGUP": "PAGEUP", "PGDN": "PAGEDOWN",
    "PRINT": "SYSRQ", "PRINTSCREEN": "SYSRQ",
    "PERIOD": "DOT",
    "XF86AUDIORAISEVOLUME": "VOLUMEUP",
    "XF86AUDIOLOWERVOLUME": "VOLUMEDOWN",
    "XF86AUDIOMUTE": "MUTE",
    "XF86AUDIOMICMUTE": "MICMUTE",
    "XF86AUDIOPLAY": "PLAYPAUSE",
    "XF86AUDIONEXT": "NEXTSONG",
    "XF86AUDIOPREV": "PREVIOUSSONG",
    "XF86MONBRIGHTNESSUP": "BRIGHTNESSUP",
    "XF86MONBRIGHTNESSDOWN": "BRIGHTNESSDOWN",
}


class BadKeys(ValueError):
    """A key combo names a key that does not exist."""


@lru_cache(maxsize=1)
def key_codes() -> dict[str, int]:
    keys = with_prefix("KEY_")
    # KEY_MAX, KEY_CNT and friends are bounds, not keys.
    for bound in ("MAX", "CNT", "RESERVED", "MIN_INTERESTING"):
        keys.pop(bound, None)
    return keys


def key(name: str) -> int:
    token = name.strip().upper().replace(" ", "")
    if token.startswith("KEY_"):
        token = token[4:]
    token = ALIASES.get(token, token)
    found = key_codes().get(token)
    if found is None:
        raise BadKeys(f"unknown key {name.strip()!r}")
    return found


@lru_cache(maxsize=256)
def combo(spec: str) -> tuple[int, ...]:
    """Codes in press order. Release is the reverse."""
    parts = [part for part in spec.split("+") if part.strip()]
    if not parts:
        raise BadKeys("no keys given")
    return tuple(key(part) for part in parts)
