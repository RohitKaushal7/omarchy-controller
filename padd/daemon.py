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

from .config import Config, ConfigError, load, save_switch
from .output import Keyboard, Mouse, backend, run
from .pad import Gone, Pad, Scanner
from .resolver import Effect, Resolver

RESCAN_S = 2.0
BATTERY_S = 60.0  # a pad's battery changes slowly; sysfs is read this often


class Daemon:
    def __init__(self, config: Config, *, dry_run: bool = False, out=None, config_error: str = ""):
        self.config = config
        # Why the file on disk is not what runs: it failed to load, and the
        # daemon is switched off (at start-up) or runs the last good config.
        self.config_error = ""
        self._config_stamp = None
        if config_error:
            self._config_broken(f"{config_error}; switched off until it is fixed")
        self.dry_run = dry_run
        self.out = out or sys.stdout
        self.scanner = Scanner()
        self.pads: dict[str, Pad] = {}
        self.resolver = Resolver(config)
        self.output = "" if dry_run else backend()
        self.keyboard = Keyboard(dry_run, output=self.output or "uinput")
        self.mouse = Mouse(config.mouse, dry_run, output=self.output or "uinput")
        self.paused = False
        self.running = False
        self._layer = self.resolver.layer
        self._stdin = b""
        self._stdin_open = True
        self._last_scan = 0.0
        self._last_battery = 0.0
        # Pads found by the first scan, or found again after a reload let go
        # of them, were already there: their "connected" is not news.
        self._scanned = False
        self._quiet_scan = False
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
            battery=pad.battery.level if pad else None,
            batteryStatus=pad.battery.status if pad else "",
            enabled=self.config.enabled,
            paused=self.paused,
            mouse=self.mouse.on,
            precise=self.mouse.precise,
            layer=self.resolver.layer,
            config=str(self.config.path),
            configError=self.config_error,
            output=self.output,
        )

    @property
    def live(self) -> bool:
        return self.config.enabled and not self.paused

    # -- pads ---------------------------------------------------------------

    def rescan(self) -> None:
        self._last_scan = time.monotonic()
        new = self._scanned and not self._quiet_scan
        self._scanned, self._quiet_scan = True, False
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
            low = pad.battery.poll()
            self.emit("connected", **info.describe(), grabbed=pad.grabbed, new=new)
            if low:
                self._battery_low(pad)
        if changed:
            self.state()

    def poll_battery(self) -> None:
        self._last_battery = time.monotonic()
        changed = False
        for pad in list(self.pads.values()):
            before = (pad.battery.level, pad.battery.status)
            if pad.battery.poll():
                self._battery_low(pad)
            changed = changed or (pad.battery.level, pad.battery.status) != before
        if changed:
            self.state()

    def _battery_low(self, pad: Pad) -> None:
        self.emit("batteryLow", name=pad.name, level=pad.battery.level)

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
        if on and self.config.stand_in:
            # Nothing loaded yet: on would mean bindings nobody chose.
            self.emit("error", message=f"cannot switch on until the config loads: {self.config_error}")
            self.state()
            return
        self._settle()
        self.config.enabled = on
        # Only the switch is written, into the file as it is on disk: the
        # config in memory may be older than a hand edit.
        try:
            # Not into a file that has gone: a new one would hold the switch
            # and nothing else, and the reload would bring back the defaults.
            save_switch(self.config.path, on, create=not self.config.from_file)
        except ConfigError as exc:
            self.emit("error", message=f"switched {'on' if on else 'off'} until restart, not saved: {exc}")
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

    def _stamp(self):
        try:
            st = os.stat(self.config.path)
        except OSError:
            return None
        return (st.st_ino, st.st_size, st.st_mtime_ns)

    def _config_broken(self, message: str, stamp=None) -> None:
        self.config_error = message
        self._config_stamp = stamp if stamp is not None else self._stamp()

    def _reload_config(self) -> None:
        self._reload = False
        stamp = self._stamp()  # before the load: a fix saved during it is still a change
        try:
            # A file that loaded once (or would not load) and has now gone is
            # an error, not a first run: never swap the defaults in for it.
            had_file = self.config.from_file or self.config.stand_in
            config = load(self.config.path, missing_ok=not had_file)
        except ConfigError as exc:
            still = ("switched off until it is fixed" if self.config.stand_in
                     else "keeping the last config that loaded")
            self._config_broken(f"{exc}; {still}", stamp)
            self.emit("error", message=self.config_error)
            self.state()
            return
        self.config_error = ""
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
            self._quiet_scan = True
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

    def _recheck_config(self) -> None:
        """While the file is broken, reload it once it changes.

        The shell asks for a reload when the file changes; this covers a
        daemon run without it, and a watch that is not set up yet.
        """
        if self.config_error and self._stamp() != self._config_stamp:
            self._reload_config()

    def run(self) -> int:
        self.running = True
        self._signals()
        try:
            os.set_blocking(sys.stdin.fileno(), False)
        except (OSError, ValueError):
            self._stdin_open = False
        self.emit("started", pid=os.getpid(), dryRun=self.dry_run, output=self.output)
        if self.config_error:
            self.emit("error", message=self.config_error)
        if not self.dry_run and not self.output:
            self.emit("error", message="nothing can receive keys or pointer motion: no Wayland "
                      "compositor with virtual keyboard and pointer support, and /dev/uinput is not writable")
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
            deadlines = [self._last_scan + RESCAN_S, self.resolver.next_deadline(),
                         self._last_battery + BATTERY_S]
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
                self._recheck_config()
                self.rescan()
            if now - self._last_battery >= BATTERY_S:
                self.poll_battery()
        self.shutdown()
        return 0

    def shutdown(self) -> None:
        self._settle()
        for path in list(self.pads):
            self.pads.pop(path).close()
        self.mouse.close()
        self.keyboard.close()
        self.emit("stopped")
