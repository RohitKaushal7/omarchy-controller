"""The evdev and uinput ABI, through ioctl and struct alone.

Just enough of <linux/input.h> and <linux/uinput.h> to read a controller and
to create a virtual keyboard and mouse.
"""

from __future__ import annotations

import ctypes
import fcntl
import glob
import os
import struct
import time

from .codes import code

EV_SYN = code("EV_SYN")
EV_KEY = code("EV_KEY")
EV_REL = code("EV_REL")
EV_ABS = code("EV_ABS")
SYN_REPORT = code("SYN_REPORT")
KEY_MAX = code("KEY_MAX")
ABS_MAX = code("ABS_MAX")

# struct input_event: struct timeval (two longs), __u16 type, __u16 code,
# __s32 value.
_EVENT = struct.Struct("@llHHi")
EVENT_SIZE = _EVENT.size

# struct input_absinfo: value, minimum, maximum, fuzz, flat, resolution.
_ABSINFO = struct.Struct("@6i")
# struct uinput_setup: struct input_id (4 x __u16), char name[80],
# __u32 ff_effects_max.
_SETUP = struct.Struct("@4H80sI")
# struct uinput_abs_setup: __u16 code, then an input_absinfo (4-byte aligned).
_ABS_SETUP = struct.Struct("@H2x6i")

_READ, _WRITE = 2, 1


def _ioc(direction: int, kind: str, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(kind) << 8) | number


def _io(kind: str, number: int) -> int:
    return _ioc(0, kind, number, 0)


EVIOCGID = _ioc(_READ, "E", 0x02, 8)
EVIOCGRAB = _ioc(_WRITE, "E", 0x90, 4)

UI_DEV_CREATE = _io("U", 1)
UI_DEV_DESTROY = _io("U", 2)
UI_DEV_SETUP = _ioc(_WRITE, "U", 3, _SETUP.size)
UI_ABS_SETUP = _ioc(_WRITE, "U", 4, _ABS_SETUP.size)
UI_SET_EVBIT = _ioc(_WRITE, "U", 100, 4)
UI_SET_KEYBIT = _ioc(_WRITE, "U", 101, 4)
UI_SET_RELBIT = _ioc(_WRITE, "U", 102, 4)
UI_SET_ABSBIT = _ioc(_WRITE, "U", 103, 4)
UI_SET_PROPBIT = _ioc(_WRITE, "U", 110, 4)

BUS_USB = 0x03
BUS_VIRTUAL = 0x06


def pack_event(ev_type: int, ev_code: int, value: int) -> bytes:
    # The kernel stamps uinput writes itself; the timeval is ignored.
    return _EVENT.pack(0, 0, ev_type, ev_code, value)


def unpack_events(data: bytes):
    """Yield (type, code, value) for every whole event in `data`."""
    for offset in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
        _sec, _usec, ev_type, ev_code, value = _EVENT.unpack_from(data, offset)
        yield ev_type, ev_code, value


# -- reading devices ---------------------------------------------------------


def event_nodes() -> list[str]:
    nodes = glob.glob("/dev/input/event*")
    nodes.sort(key=lambda node: int(node.rsplit("event", 1)[1] or 0))
    return nodes


def open_node(path: str) -> int:
    return os.open(path, os.O_RDONLY | os.O_NONBLOCK)


def _string(fd: int, number: int, size: int = 256) -> str:
    buf = ctypes.create_string_buffer(size)
    try:
        fcntl.ioctl(fd, _ioc(_READ, "E", number, size), buf)
    except OSError:
        return ""
    return buf.value.decode("utf-8", "replace")


def device_name(fd: int) -> str:
    return _string(fd, 0x06)


def device_phys(fd: int) -> str:
    return _string(fd, 0x07)


def device_ids(fd: int) -> tuple[int, int, int]:
    """(bustype, vendor, product)."""
    buf = bytearray(8)
    try:
        fcntl.ioctl(fd, EVIOCGID, buf)
    except OSError:
        return (0, 0, 0)
    bus, vendor, product, _version = struct.unpack("4H", buf)
    return (bus, vendor, product)


def capabilities(fd: int, ev_type: int, count: int) -> set[int]:
    """The codes a device advertises for one event type."""
    buf = bytearray((count + 7) // 8)
    try:
        fcntl.ioctl(fd, _ioc(_READ, "E", 0x20 + ev_type, len(buf)), buf)
    except OSError:
        return set()
    return {
        index * 8 + bit
        for index, byte in enumerate(buf)
        if byte
        for bit in range(8)
        if byte & (1 << bit)
    }


def abs_range(fd: int, axis: int) -> tuple[int, int] | None:
    buf = bytearray(_ABSINFO.size)
    try:
        fcntl.ioctl(fd, _ioc(_READ, "E", 0x40 + axis, _ABSINFO.size), buf)
    except OSError:
        return None
    _value, low, high, _fuzz, _flat, _res = _ABSINFO.unpack(buf)
    return (low, high)


def grab(fd: int, on: bool) -> bool:
    try:
        fcntl.ioctl(fd, EVIOCGRAB, 1 if on else 0)
        return True
    except OSError:
        return False


# -- virtual devices ---------------------------------------------------------


class UInput:
    """A virtual input device. Created on open(), destroyed on close()."""

    def __init__(
        self,
        name: str,
        *,
        product: int,
        keys=(),
        rels=(),
        absolutes: dict[int, tuple[int, int]] | None = None,
        props=(),
        settle: float = 0.35,
    ):
        self.name = name
        self.product = product
        self.keys = sorted(set(keys))
        self.rels = sorted(set(rels))
        self.absolutes = dict(absolutes or {})
        self.props = list(props)
        self.settle = settle
        self.fd: int | None = None

    @property
    def is_open(self) -> bool:
        return self.fd is not None

    def open(self) -> None:
        if self.fd is not None:
            return
        fd = os.open("/dev/uinput", os.O_WRONLY | os.O_NONBLOCK)
        try:
            fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
            if self.keys:
                fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
                for key in self.keys:
                    fcntl.ioctl(fd, UI_SET_KEYBIT, key)
            if self.rels:
                fcntl.ioctl(fd, UI_SET_EVBIT, EV_REL)
                for rel in self.rels:
                    fcntl.ioctl(fd, UI_SET_RELBIT, rel)
            if self.absolutes:
                fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
                for axis, (low, high) in self.absolutes.items():
                    fcntl.ioctl(fd, UI_SET_ABSBIT, axis)
                    fcntl.ioctl(
                        fd, UI_ABS_SETUP, _ABS_SETUP.pack(axis, 0, low, high, 0, 0, 0)
                    )
            for prop in self.props:
                fcntl.ioctl(fd, UI_SET_PROPBIT, prop)
            setup = _SETUP.pack(
                BUS_VIRTUAL, 0x1D6B, self.product, 1, self.name.encode()[:79], 0
            )
            fcntl.ioctl(fd, UI_DEV_SETUP, setup)
            fcntl.ioctl(fd, UI_DEV_CREATE)
        except OSError:
            os.close(fd)
            raise
        self.fd = fd
        # A brand-new device is invisible until the compositor has opened
        # it; anything written before then is lost.
        if self.settle:
            time.sleep(self.settle)

    def send(self, events) -> None:
        """Write (type, code, value) events followed by one SYN_REPORT."""
        if self.fd is None or not events:
            return
        payload = b"".join(pack_event(*event) for event in events)
        os.write(self.fd, payload + pack_event(EV_SYN, SYN_REPORT, 0))

    def close(self) -> None:
        if self.fd is None:
            return
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)
        self.fd = None
