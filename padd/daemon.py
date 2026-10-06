"""The long-running reader: pads in, actions out, JSON events on stdout.

Protocol in the spec, section 7.
"""

from __future__ import annotations

import json
import os
import select
import signal
import sys
import time

from .config import Config, ConfigError, load
from .output import Keyboard, Mouse, run
from .pad import Gone, Pad, Scanner
from .resolver import Effect, Resolver

RESCAN_S = 2.0


class Daemon:
    def __init__(self, config: Config, *, dry_run: bool = False, out=None):
        self.config = config
        self.dry_run = dry_run
        self.out = out or sys.stdout
        self.scanner = Scanner()
        self.pads: dict[str, Pad] = {}
        self.resolver = Resolver(config)
        self.keyboard = Keyboard(dry_run)
        self.mouse = Mouse(config.mouse, dry_run)
        self.paused = False
        self.running = False
        self._layer = self.resolver.layer
        self._stdin = b""
        self._stdin_open = True
        self._last_scan = 0.0
        self._reload = False
        self._wake_r = self._wake_w = -1
        # Set once the toggle combo fires, until all its buttons are up, so
        # holding it does not flip the controller back and forth.
        self._combo_latched = False

    # -- output -------------------------------------------------------------

    def emit(self, event: str, **fields) -> None:
        try:
            self.out.write(json.dumps({"event": event, **fields}) + "\n")
            self.out.flush()
        except (BrokenPipeError, ValueError):
            self.running = False

    def state(self) -> None:
        pad = next(iter(self.pads.values()), None)
        self.emit(
            "state",
            connected=pad is not None,
            device=pad.name if pad else "",
            grabbed=bool(pad and pad.grabbed),
            enabled=self.config.enabled,
            paused=self.paused,
            mouse=self.mouse.on,
            precise=self.mouse.precise,
            layer=self.resolver.layer,
            config=str(self.config.path),
        )

    @property
    def live(self) -> bool:
        return self.config.enabled and not self.paused

    # -- pads ---------------------------------------------------------------

    def rescan(self) -> None:
        self._last_scan = time.monotonic()
        found = {info.path: info for info in self.scanner.pads(self.config.device["match"])}
        changed = False
        for path in [p for p in self.pads if p not in found]:
            self._drop(path, "disconnected")
            changed = True
        for path, info in found.items():
            if path in self.pads:
                continue
            try:
                pad = Pad(
                    info,
                    trigger_threshold=self.config.timing["triggerThreshold"],
                    grab=self.config.device["grab"],
                )
            except OSError as exc:
                self.emit("error", message=f"cannot open {path}: {exc}")
                continue
            self.pads[path] = pad
            changed = True
            self.emit("connected", **info.describe(), grabbed=pad.grabbed)
        if changed:
            self.state()

    def _drop(self, path: str, reason: str) -> None:
        pad = self.pads.pop(path, None)
        if pad is None:
            return
        name = pad.name
        pad.close()
        self._settle()
        self.emit("disconnected", path=path, name=name, reason=reason)

    def _settle(self) -> None:
        """Let go of everything: no click, repeat or layer may outlive its button."""
        self._apply(self.resolver.reset())
        self.mouse.release_all()
        self._note_layer()

    # -- actions ------------------------------------------------------------

    def _note_layer(self) -> None:
        layer = self.resolver.layer
        if layer != self._layer:
            self._layer = layer
            self.emit("layer", layer=layer)

    def _apply(self, effects: list[Effect]) -> None:
        for effect in effects:
            action = effect.action
            ok, detail = True, ""
            try:
                if effect.phase in ("down", "up"):
                    down = effect.phase == "down"
                    if down and not self.mouse.on:
                        ok, detail = False, "mouse is off"
                    else:
                        self.mouse.button(action.value, down)
                    if not down:
                        continue  # the press already reported this click
                elif action.kind == "keys":
                    self.keyboard.tap(action.value)
                elif action.kind == "exec":
                    run(action.value, self.dry_run)
                elif action.value == "precision":
                    self.mouse.precise = not self.mouse.precise
                    self.state()
                elif action.value == "toggle":
                    self.set_mouse(not self.mouse.on)
            except OSError as exc:
                ok, detail = False, str(exc)
            self.emit(
                "fire",
                button=effect.button,
                layer=effect.layer,
                slot=effect.slot,
                reason=effect.reason,
                label=action.label,
                action=action.summary(),
                ok=ok,
                detail=detail,
            )

    def set_enabled(self, on: bool) -> None:
        """Switch everything, and keep it that way across restarts."""
        self._settle()
        self.config.enabled = on
        try:
            self.config.save()
        except OSError as exc:
            self.emit("error", message=f"could not save the switch: {exc}")
        self.state()

    def set_mouse(self, on: bool) -> None:
        self.mouse.on = on
        if not on:
            self.mouse.release_all()
            self.mouse.precise = False
        self.state()

    def _pump(self, pad: Pad) -> None:
        try:
            events = pad.read()
        except Gone:
            self._drop(pad.path, "gone")
            self.state()
            return
        except OSError as exc:
            self.emit("error", message=f"reading {pad.path}: {exc}")
            self._drop(pad.path, "error")
            self.state()
            return
        now = time.monotonic()
        combo = self.config.toggle_combo
        for button, pressed in events:
            if combo and button in combo:
                if pressed and not self._combo_latched and set(combo) <= pad.held_at(button):
                    self._combo_latched = True
                    # The combo's other buttons were already passed on; take
                    # their presses back so their taps and holds never run.
                    for other in combo:
                        self._apply(self.resolver.cancel(other))
                    self.emit("pad", button=button, pressed=True, held=sorted(pad.held()),
                              layer=self.resolver.layer)
                    self.set_enabled(not self.config.enabled)
                    continue
                if self._combo_latched:
                    if not pressed and not (set(combo) & pad.held()):
                        self._combo_latched = False
                    self._apply(self.resolver.cancel(button) if not pressed else [])
                    self.emit("pad", button=button, pressed=pressed, held=sorted(pad.held()),
                              layer=self.resolver.layer)
                    continue
            if self.live:
                effects = (self.resolver.press if pressed else self.resolver.release)(button, now)
            else:
                effects = []
            self.emit("pad", button=button, pressed=pressed, held=sorted(pad.held()),
                      layer=self.resolver.layer)
            self._note_layer()
            self._apply(effects)

    # -- control ------------------------------------------------------------

    def _reload_config(self) -> None:
        self._reload = False
        try:
            config = load(self.config.path)
        except ConfigError as exc:
            self.emit("error", message=f"reload failed, keeping the old config: {exc}")
            return
        regrab = config.device != self.config.device
        default_changed = config.mouse["enabled"] != self.config.mouse["enabled"]
        self._settle()
        self.config = config
        self.resolver.configure(config)
        self.mouse.configure(config.mouse)
        # A runtime toggle (GUIDE held, for a game) survives reloads that
        # leave the saved default alone, such as editing a binding.
        if default_changed:
            self.set_mouse(config.mouse["enabled"])
        if regrab:
            for path in list(self.pads):
                self._drop(path, "reconfigured")
        self.emit("reloaded")
        self.rescan()
        self.state()

    def command(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            self.emit("error", message=f"not JSON: {line[:80]}")
            return
        cmd = message.get("cmd") if isinstance(message, dict) else None
        if cmd == "reload":
            self._reload_config()
        elif cmd in ("pause", "resume"):
            paused = cmd == "pause"
            if paused != self.paused:
                self.paused = paused
                self._settle()
            self.state()
        elif cmd == "mouse":
            value = message.get("on")
            self.set_mouse(not self.mouse.on if value == "toggle" else bool(value))
        elif cmd == "state":
            self.state()
        elif cmd == "quit":
            self.running = False
        else:
            self.emit("error", message=f"unknown command {cmd!r}")

    def _read_stdin(self) -> None:
        try:
            data = os.read(sys.stdin.fileno(), 4096)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:
            self._stdin_open = False
            return
        self._stdin += data
        while b"\n" in self._stdin:
            line, self._stdin = self._stdin.split(b"\n", 1)
            if line.strip():
                self.command(line.decode("utf-8", "replace"))

    # -- loop ---------------------------------------------------------------

    def _signals(self) -> None:
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        signal.set_wakeup_fd(self._wake_w)

        def stop(_signum, _frame):
            self.running = False

        def reload(_signum, _frame):
            self._reload = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGHUP, reload)
        # Commands we start are not waited for; never leave zombies.
        signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    def run(self) -> int:
        self.running = True
        self._signals()
        try:
            os.set_blocking(sys.stdin.fileno(), False)
        except (OSError, ValueError):
            self._stdin_open = False
        self.emit("started", pid=os.getpid(), dryRun=self.dry_run)
        if not self.dry_run and self.mouse.on:
            try:
                self.mouse.device.open()
            except OSError as exc:
                self.emit("error", message=f"cannot create the virtual mouse: {exc}")
        self.rescan()
        self.state()
        while self.running:
            if self._reload:
                self._reload_config()
            now = time.monotonic()
            deadlines = [self._last_scan + RESCAN_S, self.resolver.next_deadline()]
            if self.live:
                deadlines.append(self.mouse.next_deadline(now, list(self.pads.values())))
            timeout = max(0.0, min(d for d in deadlines if d is not None) - now)

            fds = [pad.fileno() for pad in self.pads.values()] + [self._wake_r]
            if self._stdin_open:
                fds.append(sys.stdin.fileno())
            try:
                ready, _, _ = select.select(fds, [], [], timeout)
            except InterruptedError:
                continue
            by_fd = {pad.fileno(): pad for pad in self.pads.values()}
            for fd in ready:
                if fd == self._wake_r:
                    try:
                        while os.read(self._wake_r, 64):
                            pass
                    except BlockingIOError:
                        pass
                elif self._stdin_open and fd == sys.stdin.fileno():
                    self._read_stdin()
                elif fd in by_fd:
                    self._pump(by_fd[fd])

            now = time.monotonic()
            if self.live:
                self._apply(self.resolver.tick(now))
                pads = list(self.pads.values())
                due = self.mouse.next_deadline(now, pads)
                if due is not None and now >= due:
                    try:
                        self.mouse.tick(now, pads)
                    except OSError as exc:
                        self.emit("error", message=f"virtual mouse: {exc}")
            if now - self._last_scan >= RESCAN_S:
                self.rescan()
        self.shutdown()
        return 0

    def shutdown(self) -> None:
        self._settle()
        for path in list(self.pads):
            self.pads.pop(path).close()
        self.mouse.close()
        self.keyboard.close()
        self.emit("stopped")
