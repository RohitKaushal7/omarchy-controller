"""Linux input event codes, read from the kernel's own header.

`/usr/include/linux/input-event-codes.h` ships with linux-api-headers, which
glibc depends on, so it is present on every Arch system. Reading it at start-up
keeps several hundred constants out of the source and can never drift from the
running kernel's ABI.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

HEADER = Path("/usr/include/linux/input-event-codes.h")

_DEFINE = re.compile(r"^#define\s+([A-Z][A-Z0-9_]*)\s+(\S+)")


class CodesUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def table() -> dict[str, int]:
    """Every #define in the header, resolved to an int (aliases followed)."""
    try:
        lines = HEADER.read_text().splitlines()
    except OSError as exc:
        raise CodesUnavailable(
            f"cannot read {HEADER} (install linux-api-headers): {exc}"
        ) from exc
    raw: dict[str, str] = {}
    for line in lines:
        match = _DEFINE.match(line)
        if match:
            raw[match.group(1)] = match.group(2)

    resolved: dict[str, int] = {}

    def resolve(name: str, depth: int = 0) -> int | None:
        if name in resolved:
            return resolved[name]
        value = raw.get(name)
        if value is None or depth > 8:
            return None
        try:
            number = int(value, 0)
        except ValueError:
            # An alias such as `#define BTN_A BTN_SOUTH`, or an expression
            # like `(KEY_MAX+1)`, which only the *_CNT helpers use.
            expr = re.fullmatch(r"\(([A-Z0-9_]+)\+1\)", value)
            if expr:
                base = resolve(expr.group(1), depth + 1)
                number = None if base is None else base + 1
            else:
                number = resolve(value, depth + 1)
        if number is not None:
            resolved[name] = number
        return number

    for name in raw:
        resolve(name)
    return resolved


def code(name: str) -> int:
    value = table().get(name)
    if value is None:
        raise KeyError(name)
    return value


def with_prefix(prefix: str) -> dict[str, int]:
    """{suffix: code} for every name starting with `prefix` (e.g. "KEY_")."""
    return {
        name[len(prefix):]: value
        for name, value in table().items()
        if name.startswith(prefix)
    }
