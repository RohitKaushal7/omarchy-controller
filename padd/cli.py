"""padctl: run the daemon, inspect pads, list and edit bindings."""

from __future__ import annotations

import argparse
import json
import os
import select
import sys
import time
from pathlib import Path

from . import linux
from . import pad as padmod
from .codes import HEADER, CodesUnavailable, table
from .config import CLICKS, DEFAULT_PATH, MOUSE_ACTIONS, Action, Config, ConfigError, load
from .output import backend


def _config(args) -> Config:
    return load(args.config)


def _fail(message: str) -> int:
    print(f"padctl: {message}", file=sys.stderr)
    return 1


# -- commands ----------------------------------------------------------------


def cmd_daemon(args) -> int:
    from .daemon import Daemon

    try:
        config = _config(args)
    except ConfigError as exc:
        print(json.dumps({"event": "error", "message": str(exc), "fatal": True}), flush=True)
        return 1
    # So an exec binding can call `padctl` by name.
    bin_dir = str(Path(__file__).resolve().parents[1] / "bin")
    path = os.environ.get("PATH", "")
    if bin_dir not in path.split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join(p for p in (bin_dir, path) if p)
    return Daemon(config, dry_run=args.dry_run).run()


def cmd_devices(args) -> int:
    config = _config(args)
    pads = padmod.Scanner().pads(config.device["match"])
    if args.json:
        print(json.dumps([p.describe() for p in pads], indent=2))
        return 0
    if not pads:
        print("no controller found")
        return 1
    for info in pads:
        fix = "  (Bluetooth layout fix)" if info.describe()["layoutFix"] else ""
        print(f"{info.name}  {info.path}  {info.vendor:04x}:{info.product:04x}{fix}")
        print(f"    {' '.join(info.buttons)}")
    return 0


def cmd_monitor(args) -> int:
    config = _config(args)
    found = padmod.Scanner().pads(config.device["match"])
    if not found:
        return _fail("no controller found")
    pad = padmod.Pad(found[0], trigger_threshold=config.timing["triggerThreshold"], grab=False)
    print(f"{pad.name}: press buttons, Ctrl+C to stop", flush=True)
    end = time.monotonic() + args.timeout
    try:
        while time.monotonic() < end:
            ready, _, _ = select.select([pad.fileno()], [], [], 0.25)
            if ready:
                for button, pressed in pad.read():
                    print(f"{'down' if pressed else 'up  '}  {button}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        pad.close()
    return 0


def cmd_list(args) -> int:
    config = _config(args)
    if args.json:
        print(json.dumps({"path": str(config.path), **config.to_json()}))
        return 0
    for layer in config.layers:
        held = f"  (hold {layer.key})" if layer.key else ""
        print(f"{layer.name}{held}")
        for button in padmod.BUTTONS:
            binding = layer.bindings.get(button)
            if binding is None:
                continue
            for slot in ("tap", "hold"):
                action = binding.slot(slot)
                if action is None:
                    continue
                tag = " (hold)" if slot == "hold" else (" (repeat)" if action.repeat else "")
                print(f"  {button:<6} {action.label or '-':<24} {action.summary()}{tag}")
        print()
    return 0


def _action(args) -> Action:
    kinds = [k for k in ("keys", "exec", "click", "mouse") if getattr(args, k)]
    if len(kinds) != 1:
        raise ConfigError("give exactly one of --keys, --exec, --click, --mouse")
    raw = {kinds[0]: getattr(args, kinds[0])}
    if args.repeat:
        raw["repeat"] = True
    if args.label:
        raw["label"] = args.label
    return Action.parse(raw, "binding")


def cmd_bind(args) -> int:
    config = _config(args)
    slot = "hold" if args.hold else "tap"
    config.bind(args.layer, args.button, slot, _action(args))
    # A move: the same action recorded on another button replaces the old one.
    if args.replace:
        old_layer, old_button, old_slot = args.replace.split(":")
        if (old_layer, old_button.upper(), old_slot) != (args.layer, args.button.upper(), slot):
            config.unbind(old_layer, old_button, old_slot)
    Config(config.to_json(), config.path)  # validate the result as a whole
    config.save()
    print(f"bound {args.layer} {args.button.upper()} ({slot})")
    return 0


def cmd_unbind(args) -> int:
    config = _config(args)
    slot = None if args.all else ("hold" if args.hold else "tap")
    if not config.unbind(args.layer, args.button, slot):
        return _fail(f"nothing bound to {args.button.upper()} in {args.layer}")
    config.save()
    print(f"unbound {args.layer} {args.button.upper()}")
    return 0


def cmd_set(args) -> int:
    """Set one setting by dotted path, e.g. `padctl set mouse.enabled false`."""
    config = _config(args)
    raw = config.to_json()
    try:
        value = json.loads(args.value)
    except json.JSONDecodeError:
        value = args.value
    node = raw
    parts = args.key.split(".")
    for part in parts[:-1]:
        if not isinstance(node.get(part), dict):
            return _fail(f"no setting {args.key}")
        node = node[part]
    if parts[-1] not in node or parts[-1] == "layers":
        return _fail(f"no setting {args.key}")
    node[parts[-1]] = value
    Config(raw, config.path).save()
    print(f"{args.key} = {json.dumps(value)}")
    return 0


def cmd_init(args) -> int:
    path = Path(args.config or DEFAULT_PATH)
    if path.exists() and not args.force:
        return _fail(f"{path} exists (--force to overwrite)")
    Config({}, path).save()
    print(f"wrote {path}")
    return 0


def cmd_check(args) -> int:
    config = _config(args)
    count = sum(len(layer.bindings) for layer in config.layers)
    print(f"{config.path}: ok, {len(config.layers)} layers, {count} buttons bound")
    return 0


def cmd_doctor(args) -> int:
    problems = 0

    def report(ok: bool, text: str, fix: str = "") -> None:
        nonlocal problems
        print(f"{'ok  ' if ok else 'FAIL'}  {text}")
        if not ok:
            problems += 1
            if fix:
                print(f"      {fix}")

    try:
        table()
        report(True, f"key codes from {HEADER}")
    except CodesUnavailable as exc:
        report(False, str(exc), "install the linux-api-headers package")
    # Access can come from the input group or from the seat's uaccess ACLs,
    # so test it rather than the group.
    nodes = linux.event_nodes()
    readable = [n for n in nodes if os.access(n, os.R_OK)]
    report(bool(readable), f"/dev/input is readable ({len(readable)} of {len(nodes)} nodes)",
           "add your user to the input group, then log in again")
    out = backend()
    if out == "wayland":
        report(True, "virtual keyboard and mouse through the compositor (no permissions needed)")
    elif out == "uinput":
        report(True, "virtual keyboard and mouse through /dev/uinput")
    else:
        report(False, "nothing can receive keys or pointer motion",
               "run inside a Wayland session with virtual keyboard and pointer support")
    try:
        config = _config(args)
        report(True, f"config {config.path}")
    except ConfigError as exc:
        report(False, f"config: {exc}")
        config = Config({})
    pads = padmod.Scanner().pads(config.device["match"])
    report(bool(pads), "a controller is connected" + (f": {pads[0].name}" if pads else ""),
           "pair or plug one in, then run `padctl devices`")
    return 1 if problems else 0


# -- parsing -----------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="padctl", description="Drive the desktop from a game controller.")
    top.add_argument("--config", help=f"config file (default {DEFAULT_PATH})")
    sub = top.add_subparsers(dest="command", required=True)

    p = sub.add_parser("daemon", help="run the reader (the shell plugin does this)")
    p.add_argument("--dry-run", action="store_true", help="report actions without performing them")
    p.set_defaults(func=cmd_daemon)

    p = sub.add_parser("devices", help="list connected controllers")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_devices)

    p = sub.add_parser("monitor", help="print buttons as they are pressed")
    p.add_argument("--timeout", type=float, default=60)
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("list", help="show the layers and their bindings")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("bind", help="bind a button in a layer")
    p.add_argument("layer", help="layer id, e.g. base, text, system")
    p.add_argument("button", help=" ".join(padmod.BUTTONS))
    p.add_argument("--hold", action="store_true", help="bind the hold action instead of the tap")
    p.add_argument("--keys", help="key combo, e.g. SUPER+RETURN")
    p.add_argument("--exec", help="shell command")
    p.add_argument("--click", choices=CLICKS)
    p.add_argument("--mouse", choices=MOUSE_ACTIONS)
    p.add_argument("--repeat", action="store_true", help="repeat keys while held")
    p.add_argument("--label", help="what it does, in a few words")
    p.add_argument("--replace", metavar="LAYER:BUTTON:SLOT", help="remove this binding in the same write")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("unbind", help="remove a button's binding from a layer")
    p.add_argument("layer")
    p.add_argument("button")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--hold", action="store_true", help="remove only the hold action")
    group.add_argument("--all", action="store_true", help="remove tap and hold")
    p.set_defaults(func=cmd_unbind)

    p = sub.add_parser("set", help="change a setting, e.g. mouse.speed 1500")
    p.add_argument("key")
    p.add_argument("value")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("init", help="write the default config")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="validate the config")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("doctor", help="check permissions, headers and the pad")
    p.set_defaults(func=cmd_doctor)
    return top


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, CodesUnavailable) as exc:
        return _fail(str(exc))
    except KeyboardInterrupt:
        return 130
