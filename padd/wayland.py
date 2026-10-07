"""Virtual keyboard and pointer through the compositor, with no permissions.

Writing to /dev/uinput needs a udev rule that a stock install does not have.
Wayland compositors (Hyprland among them) offer the same thing to any client
of the user's session, through two protocols:

    zwp_virtual_keyboard_manager_v1   (virtual-keyboard-unstable-v1)
    zwlr_virtual_pointer_manager_v1   (wlr-virtual-pointer-unstable-v1)

This is just enough of the Wayland wire protocol to use them: a unix socket,
little-endian 32-bit words, and one file descriptor (the keymap) sent with
SCM_RIGHTS. The sinks here take the same evdev-style (type, code, value)
batches the uinput devices take, so nothing above them changes.
"""

from __future__ import annotations

import array
import errno
import os
import socket
import struct
import time

from . import linux
from .codes import code

KEYBOARD_MANAGER = "zwp_virtual_keyboard_manager_v1"
POINTER_MANAGER = "zwlr_virtual_pointer_manager_v1"

# An ordinary US layout by name; the compositor's xkbcommon resolves the
# includes from the system's XKB data. The keycodes are evdev codes + 8, so a
# KEY_* code goes out as it is, and media keys come from inet(evdev).
KEYMAP = b"""xkb_keymap {
  xkb_keycodes { include "evdev+aliases(qwerty)" };
  xkb_types { include "complete" };
  xkb_compat { include "complete" };
  xkb_symbols { include "pc+us+inet(evdev)" };
};
\0"""

# Modifier keys and the masks XKB's "complete" types give them.
_MOD_MASKS = {
    code("KEY_LEFTSHIFT"): 1, code("KEY_RIGHTSHIFT"): 1,
    code("KEY_LEFTCTRL"): 4, code("KEY_RIGHTCTRL"): 4,
    code("KEY_LEFTALT"): 8, code("KEY_RIGHTALT"): 8,
    code("KEY_LEFTMETA"): 64, code("KEY_RIGHTMETA"): 64,
}


class WaylandError(OSError):
    pass


def _string(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _fixed(value: float) -> int:
    return int(round(value * 256))


class Connection:
    """One client connection: registry, a seat, and object ids."""

    def __init__(self) -> None:
        runtime = os.environ.get("XDG_RUNTIME_DIR")
        display = os.environ.get("WAYLAND_DISPLAY", "wayland-0")
        if not runtime:
            raise WaylandError(errno.ENOENT, "XDG_RUNTIME_DIR is not set")
        path = display if display.startswith("/") else os.path.join(runtime, display)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(path)
        except OSError as exc:
            self.sock.close()
            raise WaylandError(exc.errno, f"no Wayland compositor at {path}") from exc
        self._next_id = 2  # 1 is wl_display
        self._buffer = b""
        self.globals: dict[str, tuple[int, int]] = {}  # interface -> (name, version)
        self.registry = self._new_id()
        self._request(1, 1, struct.pack("<I", self.registry))  # wl_display.get_registry
        self.roundtrip()

    # -- wire ---------------------------------------------------------------

    def _new_id(self) -> int:
        new = self._next_id
        self._next_id += 1
        return new

    def _request(self, obj: int, opcode: int, payload: bytes = b"", fd: int | None = None) -> None:
        message = struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload
        try:
            if fd is None:
                self.sock.sendall(message)
            else:
                self.sock.sendmsg([message], [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))])
        except OSError as exc:
            raise WaylandError(exc.errno, f"compositor connection lost: {exc}") from exc

    def _read(self, block: bool) -> None:
        self.sock.setblocking(block)
        try:
            data = self.sock.recv(65536)
        except BlockingIOError:
            return
        except OSError as exc:
            raise WaylandError(exc.errno, f"compositor connection lost: {exc}") from exc
        finally:
            self.sock.setblocking(True)
        if not data:
            raise WaylandError(errno.ECONNRESET, "the compositor closed the connection")
        self._buffer += data

    def _events(self):
        while len(self._buffer) >= 8:
            obj, header = struct.unpack_from("<II", self._buffer)
            size, opcode = header >> 16, header & 0xFFFF
            if size < 8 or len(self._buffer) < size:
                return
            body = self._buffer[8:size]
            self._buffer = self._buffer[size:]
            yield obj, opcode, body

    def _dispatch(self, done_id: int | None = None) -> bool:
        """Handle buffered events. Returns True once `done_id`'s callback fired."""
        finished = False
        for obj, opcode, body in self._events():
            if obj == 1 and opcode == 0:  # wl_display.error
                failed, err = struct.unpack_from("<II", body)
                length = struct.unpack_from("<I", body, 8)[0]
                message = body[12:12 + length - 1].decode("utf-8", "replace")
                raise WaylandError(errno.EPROTO, f"compositor error {err} on object {failed}: {message}")
            if obj == self.registry and opcode == 0:  # wl_registry.global
                name = struct.unpack_from("<I", body)[0]
                length = struct.unpack_from("<I", body, 4)[0]
                interface = body[8:8 + length - 1].decode()
                version = struct.unpack_from("<I", body, 8 + length + (-length % 4))[0]
                self.globals[interface] = (name, version)
            elif done_id is not None and obj == done_id and opcode == 0:  # wl_callback.done
                finished = True
        return finished

    def roundtrip(self) -> None:
        callback = self._new_id()
        self._request(1, 0, struct.pack("<I", callback))  # wl_display.sync
        deadline = time.monotonic() + 2.0
        while not self._dispatch(callback):
            if time.monotonic() > deadline:
                raise WaylandError(errno.ETIMEDOUT, "the compositor did not answer")
            self._read(block=True)

    def drain(self) -> None:
        """Read whatever the compositor sent, so its queue never backs up."""
        self._read(block=False)
        self._dispatch()

    def bind(self, interface: str, version: int) -> int:
        if interface not in self.globals:
            raise WaylandError(errno.ENOTSUP, f"the compositor does not offer {interface}")
        name, offered = self.globals[interface]
        new = self._new_id()
        payload = struct.pack("<I", name) + _string(interface) + struct.pack("<II", min(version, offered), new)
        self._request(self.registry, 0, payload)  # wl_registry.bind
        return new

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _now_ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


class _Sink:
    """Shared plumbing: one lazily made connection, rebuilt if it drops."""

    def __init__(self, name: str):
        self.name = name
        self.conn: Connection | None = None

    @property
    def is_open(self) -> bool:
        return self.conn is not None

    def open(self) -> None:
        if self.conn is None:
            conn = Connection()
            try:
                self._create(conn)
                conn.roundtrip()  # surfaces a refusal now, not on the first key
            except BaseException:
                conn.close()
                raise
            self.conn = conn

    def _create(self, conn: Connection) -> None:
        raise NotImplementedError

    def send(self, events) -> None:
        if not events:
            return
        self.open()
        try:
            self._send(events)
            self.conn.drain()
        except WaylandError:
            # The compositor restarted or dropped us: start over next time.
            self.close()
            raise

    def _send(self, events) -> None:
        raise NotImplementedError

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None


class VirtualKeyboard(_Sink):
    def __init__(self):
        super().__init__("padd virtual keyboard")
        self.keyboard = 0
        self._mods = 0
        self._down: set[int] = set()

    def _create(self, conn: Connection) -> None:
        manager = conn.bind(KEYBOARD_MANAGER, 1)
        seat = conn.bind("wl_seat", 1)
        self.keyboard = conn._new_id()
        conn._request(manager, 0, struct.pack("<II", seat, self.keyboard))  # create_virtual_keyboard
        fd = os.memfd_create("padd-keymap", os.MFD_CLOEXEC)
        try:
            os.write(fd, KEYMAP)
            # zwp_virtual_keyboard_v1.keymap(format XKB_V1, fd, size)
            conn._request(self.keyboard, 0, struct.pack("<II", 1, len(KEYMAP)), fd=fd)
        finally:
            os.close(fd)
        self._mods = 0
        self._down.clear()

    def _send(self, events) -> None:
        for ev_type, key, value in events:
            if ev_type != linux.EV_KEY:
                continue
            (self._down.add if value else self._down.discard)(key)
            # zwp_virtual_keyboard_v1.key(time, key, state)
            self.conn._request(self.keyboard, 1, struct.pack("<III", _now_ms(), key, 1 if value else 0))
            if key in _MOD_MASKS:
                mods = 0
                for held in self._down:
                    mods |= _MOD_MASKS.get(held, 0)
                if mods != self._mods:
                    self._mods = mods
                    # zwp_virtual_keyboard_v1.modifiers(depressed, latched, locked, group)
                    self.conn._request(self.keyboard, 2, struct.pack("<IIII", mods, 0, 0, 0))


# wl_pointer axis numbers and the scroll distance of one wheel notch.
_VERTICAL, _HORIZONTAL = 0, 1
_NOTCH_DISTANCE = 15.0
_HI_RES_NOTCH = 120


class VirtualPointer(_Sink):
    def __init__(self):
        super().__init__("padd virtual mouse")
        self.pointer = 0

    def _create(self, conn: Connection) -> None:
        manager = conn.bind(POINTER_MANAGER, 2)
        seat = conn.bind("wl_seat", 1)
        self.pointer = conn._new_id()
        conn._request(manager, 0, struct.pack("<II", seat, self.pointer))  # create_virtual_pointer

    def _send(self, events) -> None:
        now = _now_ms()
        dx = dy = 0
        scroll = {_VERTICAL: 0, _HORIZONTAL: 0}
        notches = {_VERTICAL: 0, _HORIZONTAL: 0}
        req = self.conn._request
        for ev_type, ev_code, value in events:
            if ev_type == linux.EV_KEY:
                req(self.pointer, 2, struct.pack("<III", now, ev_code, 1 if value else 0))  # button
            elif ev_code == _REL["X"]:
                dx += value
            elif ev_code == _REL["Y"]:
                dy += value
            elif ev_code == _REL["WHEEL_HI_RES"]:
                scroll[_VERTICAL] += value
            elif ev_code == _REL["HWHEEL_HI_RES"]:
                scroll[_HORIZONTAL] += value
            elif ev_code == _REL["WHEEL"]:
                notches[_VERTICAL] += value
            elif ev_code == _REL["HWHEEL"]:
                notches[_HORIZONTAL] += value
        if dx or dy:
            req(self.pointer, 0, struct.pack("<Iii", now, _fixed(dx), _fixed(dy)))  # motion
        for axis in (_VERTICAL, _HORIZONTAL):
            if not scroll[axis]:
                continue
            # evdev counts wheel-up as positive; Wayland, scrolling down.
            distance = -scroll[axis] / _HI_RES_NOTCH * _NOTCH_DISTANCE
            if axis == _HORIZONTAL:
                distance = -distance
            req(self.pointer, 5, struct.pack("<I", 0))  # axis_source(wheel)
            if notches[axis]:
                steps = -notches[axis] if axis == _VERTICAL else notches[axis]
                req(self.pointer, 7, struct.pack("<IIii", now, axis, _fixed(distance), steps))  # axis_discrete
            else:
                req(self.pointer, 3, struct.pack("<IIi", now, axis, _fixed(distance)))  # axis
        req(self.pointer, 4)  # frame


_REL = {name: code(f"REL_{name}") for name in ("X", "Y", "WHEEL", "HWHEEL", "WHEEL_HI_RES", "HWHEEL_HI_RES")}


def available() -> list[str]:
    """The protocols this session's compositor offers, of the two needed."""
    conn = Connection()
    try:
        return [name for name in (KEYBOARD_MANAGER, POINTER_MANAGER) if name in conn.globals]
    finally:
        conn.close()
