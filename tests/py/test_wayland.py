"""The Wayland sinks against a fake compositor on a private socket.

The fake answers the registry and sync, and records every request, so the
wire format is checked without touching the real session.
"""

from __future__ import annotations

import array
import os
import socket
import struct
import tempfile
import threading
import unittest

from helpers import ROOT  # noqa: F401
from padd import linux, wayland
from padd.codes import code

GLOBALS = [("wl_seat", 9), (wayland.KEYBOARD_MANAGER, 1), (wayland.POINTER_MANAGER, 2)]


def _string(text: str) -> bytes:
    raw = text.encode() + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


class FakeCompositor:
    def __init__(self, path: str):
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(path)
        self.server.listen(1)
        self.requests: list[tuple[int, int, bytes]] = []
        self.fds: list[int] = []  # every descriptor received, in order
        self.registry = 0
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _send(self, conn, obj, opcode, payload=b""):
        conn.sendall(struct.pack("<II", obj, ((8 + len(payload)) << 16) | opcode) + payload)

    def _serve(self):
        try:
            conn, _ = self.server.accept()
        except OSError:
            return
        with conn:
            self._read(conn)

    def _read(self, conn):
        buffer = b""
        while True:
            fdbuf = array.array("i")
            try:
                data, ancdata, _flags, _ = conn.recvmsg(65536, socket.CMSG_SPACE(4 * 4))
            except OSError:
                return
            if not data:
                return
            for level, kind, cmsg in ancdata:
                if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                    fdbuf.frombytes(cmsg[: len(cmsg) - len(cmsg) % 4])
                    self.fds.extend(fdbuf)
            buffer += data
            while len(buffer) >= 8:
                obj, header = struct.unpack_from("<II", buffer)
                size, opcode = header >> 16, header & 0xFFFF
                if len(buffer) < size:
                    break
                body, buffer = buffer[8:size], buffer[size:]
                self.requests.append((obj, opcode, body))
                if obj == 1 and opcode == 1:  # get_registry
                    self.registry = struct.unpack("<I", body)[0]
                    for name, (interface, version) in enumerate(GLOBALS, start=1):
                        self._send(conn, self.registry, 0, struct.pack("<I", name) + _string(interface)
                                   + struct.pack("<I", version))
                elif obj == 1 and opcode == 0:  # sync
                    self._send(conn, struct.unpack("<I", body)[0], 0, struct.pack("<I", 0))

    def close(self):
        self.server.close()


class WaylandTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.env = {k: os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "WAYLAND_DISPLAY")}
        os.environ["XDG_RUNTIME_DIR"] = self.dir.name
        os.environ["WAYLAND_DISPLAY"] = "fake-0"
        self.fake = FakeCompositor(os.path.join(self.dir.name, "fake-0"))

    def tearDown(self):
        self.fake.close()
        for key, value in self.env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.dir.cleanup()

    def requests_on(self, obj):
        return [(op, body) for o, op, body in self.fake.requests if o == obj]

    def test_registry_lists_both_protocols(self):
        self.assertEqual(wayland.available(), [wayland.KEYBOARD_MANAGER, wayland.POINTER_MANAGER])

    def test_keyboard_sends_keymap_keys_and_modifiers(self):
        kb = wayland.VirtualKeyboard()
        meta, space = code("KEY_LEFTMETA"), code("KEY_SPACE")
        kb.send([(linux.EV_KEY, meta, 1)])
        kb.send([(linux.EV_KEY, space, 1)])
        kb.send([(linux.EV_KEY, space, 0)])
        kb.send([(linux.EV_KEY, meta, 0)])
        kb.conn.roundtrip()
        ops = self.requests_on(kb.keyboard)
        keymap_op, keymap_body = ops[0]
        self.assertEqual(keymap_op, 0)
        fmt, size = struct.unpack("<II", keymap_body)
        self.assertEqual((fmt, size), (1, len(wayland.KEYMAP)))
        self.assertEqual(len(self.fake.fds), 1, "the keymap travels as one descriptor")
        self.assertEqual(os.pread(self.fake.fds[0], size, 0), wayland.KEYMAP)
        os.close(self.fake.fds[0])
        sent = [(op, struct.unpack("<III", body)[1:] if op == 1 else struct.unpack("<IIII", body)[0])
                for op, body in ops[1:]]
        self.assertEqual(sent, [(1, (meta, 1)), (2, 64), (1, (space, 1)), (1, (space, 0)),
                                (1, (meta, 0)), (2, 0)])
        kb.close()

    def test_pointer_motion_buttons_and_wheel(self):
        mouse = wayland.VirtualPointer()
        mouse.send([(linux.EV_REL, code("REL_X"), 3), (linux.EV_REL, code("REL_Y"), -2)])
        mouse.send([(linux.EV_KEY, code("BTN_LEFT"), 1)])
        mouse.send([(linux.EV_REL, code("REL_WHEEL_HI_RES"), 120), (linux.EV_REL, code("REL_WHEEL"), 1)])
        mouse.send([(linux.EV_REL, code("REL_HWHEEL_HI_RES"), 60)])
        mouse.conn.roundtrip()
        ops = self.requests_on(mouse.pointer)
        names = [op for op, _ in ops]
        self.assertEqual(names, [0, 4, 2, 4, 5, 7, 4, 5, 3, 4])
        _t, dx, dy = struct.unpack("<Iii", ops[0][1])
        self.assertEqual((dx, dy), (3 * 256, -2 * 256))
        self.assertEqual(struct.unpack("<III", ops[2][1])[1:], (code("BTN_LEFT"), 1))
        _t, axis, value, steps = struct.unpack("<IIii", ops[5][1])
        self.assertEqual((axis, value, steps), (0, -15 * 256, -1))  # wheel up scrolls up
        _t, axis, value = struct.unpack("<IIi", ops[8][1])
        self.assertEqual((axis, value), (1, int(7.5 * 256)))  # right is positive
        mouse.close()

    def test_missing_protocol_is_reported(self):
        GLOBALS.remove((wayland.POINTER_MANAGER, 2))
        try:
            with self.assertRaises(wayland.WaylandError):
                wayland.VirtualPointer().open()
        finally:
            GLOBALS.append((wayland.POINTER_MANAGER, 2))


if __name__ == "__main__":
    unittest.main()
