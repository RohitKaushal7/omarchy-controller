# Controller — design

Drive an Omarchy desktop from a game controller, from bed or the sofa: the
sticks are a mouse, the triggers hold layers of shortcuts, and a hint on
screen shows what each button does while a layer is held.

## 1. Goals

- Point, click, scroll, type by voice and edit text without a keyboard.
- One model that explains every button: **layers**. A layer is a set of
  button actions, switched on by holding one button. No chords to learn.
- Each button keeps a related meaning across layers (B is back, then
  Backspace, then close window), so the layout can be learnt by feel.
- Learnable on the pad itself: holding a layer key shows its map.
- Pure Python standard library and QML. Nothing to build or install.

Out of scope: per-application profiles, gyro, rumble, macros with delays.

## 2. Pieces

```
Panel.qml          bar icon + popup (Ui/Panel), reads the service
Service.qml        one per shell: runs the daemon, parses its events, IPC,
                   hosts the layer hint
ui/                popup body, rows, editor, hint overlay
lib/*.js           pure logic for the QML side, tested with node
bin/padctl         CLI: the daemon, listing, binding, diagnostics
padd/              the daemon: evdev in, uinput keyboard + mouse out
tests/             python unittest, node --test, qmllint
```

The daemon is the only reader of the pad. It speaks newline-delimited JSON
on stdout and reads commands on stdin, so the service talks to it without
pid files or signals. Every config change goes through `padctl`, which
writes the file atomically; the service then tells the daemon to reload.

## 3. Buttons

Names follow the Xbox layout, which is what Linux reports for nearly every
pad: `A B X Y`, `LB RB` (bumpers), `LT RT` (triggers, analog or digital),
`BACK START GUIDE`, `L3 R3` (stick clicks), `UP DOWN LEFT RIGHT` (d-pad,
from the hat or from d-pad buttons). Sticks are `LS` and `RS` and only ever
drive the mouse. Analog triggers count as pressed past
`timing.triggerThreshold`.

Key codes come from `/usr/include/linux/input-event-codes.h`
(linux-api-headers, a dependency of glibc), parsed at start-up rather than
copied into the source.

## 4. Config

`$XDG_CONFIG_HOME/dev.reuk.controller/config.json`:

```json
{
  "version": 1,
  "enabled": true,
  "toggleCombo": ["GUIDE", "BACK"],
  "device": { "match": [], "grab": false },
  "timing": { "holdMs": 450, "repeatDelayMs": 350, "repeatMs": 90,
              "triggerThreshold": 0.3, "hintDelayMs": 350 },
  "mouse": { "enabled": true, "move": "LS", "scroll": "RS",
             "speed": 1800, "curve": 2.2, "deadzone": 0.15,
             "scrollSpeed": 22, "scrollCurve": 2.0, "naturalScroll": false,
             "precision": 0.3, "rateHz": 125 },
  "layers": [
    { "id": "base", "name": "Browse", "bindings": { "A": { "click": "left", "label": "Click" } } },
    { "id": "text", "name": "Text", "key": "LT", "bindings": { "B": { "keys": "BACKSPACE", "repeat": true, "label": "Backspace" } } }
  ]
}
```

- The first layer is the base and has no `key`. Every other layer has one.
- A binding is either an action (meaning: on tap) or
  `{"tap": action, "hold": action}`.
- An action is one of `keys` (typed on the virtual keyboard, `repeat`
  optional), `exec` (shell command), `click` (`left right middle back
  forward`, held while the button is held) or `mouse` (`precision`,
  `toggle`). Each carries an optional `label`.
- Layer keys are modifiers only: they may not be bound in any layer.
- `toggleCombo` (default `["GUIDE", "BACK"]`, `[]` for none): pressed
  together, switches `enabled` and saves it. It works while switched off,
  which is the point: out of the way for a game, back after it. The combo's
  buttons do not run their own actions when it fires.
- `device.match`: name substrings; empty means any gamepad. A device whose
  name contains `[padd-test]` is used only when `match` names it, so the
  test suite's synthetic pads can never drive a real desktop.

## 5. Resolving presses

- The active layer is the most recently pressed layer key still held, or
  the base.
- A button resolves in the active layer, falling through to the base when
  the layer does not bind it. The resolution is fixed at press time, so a
  release (a click, a repeat) always ends what the press began.
- Tap only: fires on press. With `repeat`, again after `repeatDelayMs`,
  then every `repeatMs`.
- Hold only: fires once held `holdMs`; a shorter press does nothing.
- Tap and hold: the tap fires on a release before `holdMs`, the hold at
  `holdMs`. Only these buttons wait.
- `click` actions press on press and release on release; they cannot have
  a hold or repeat.
- Releasing a layer key ends the layer for later presses only.

## 6. Mouse

One stick moves, the other scrolls (hi-res wheel). Speed is
`speed * t^curve`, with `t` the travel past a radial deadzone, so a light
tilt moves a pixel at a time and full tilt crosses the screen. `precision`
scales both while precision is on. Output goes to its own uinput mouse,
`padd virtual mouse`; README tells Hyprland users to give it a flat
acceleration profile. `mouse: toggle` switches the mouse off and on at run
time (for games); `mouse.enabled` is the persisted default.

## 7. Daemon protocol

Out (one JSON object per line, all with `"event"`): `started`, `state`
(connected, device, enabled, paused, mouse, precise, layer), `pad` (a
button went down or up, with the resolved layer), `layer` (active layer
changed), `fire` (an action ran: button, layer, slot, label, ok),
`error`, `stopped`.

In (one JSON object per line, `"cmd"`): `reload`, `pause`, `resume`,
`mouse` (`on`, `off`, `toggle`), `state`, `quit`.

## 8. Shell side

- **Bar icon**: controller glyph, dim without a pad, accent while the
  mouse is on, a pulse when an action fires.
- **Popup**: a hero (pad glyph, name, status line in caps, the buttons held
  right now); the master switch, labelled with the toggle combo; the mouse
  switch; a search box focused on open that matches button
  names, labels, keys, commands and layer names; layer chips (All plus each
  layer); rows grouped by layer showing the button, its label and what it
  sends, with hold and repeat tags; the active layer and held buttons
  highlighted live. Clicking a row edits it; a delete control shows on
  hover. An editor card adds or edits a binding: press **Record** and press
  the button on the pad (holding a layer key picks that layer), choose tap
  or hold, the action kind and its value, a label, save.
- **Layer hint**: while a layer key is held past `hintDelayMs` with no
  other button pressed, a card at the bottom of the screen lists that
  layer's buttons. Pressing a button first means the layer is known, so the
  card is skipped for that hold. A short notice shows
  when the mouse or precision mode is switched. Input passes through it.
- **IPC** target `dev.reuk.controller`: `status`, `reload`, `on`, `off`,
  `toggle`, `mouse`, `hint` (show the current layer's map).

## 9. Tests

- Python: config validation, key parsing, the resolver (layers,
  fall-through, tap/hold, repeat, clicks), mouse maths, and an end-to-end run
  of the real daemon against a synthetic uinput pad in dry-run mode.
- node: row building, search and the editor's argument building.
- qmllint over every QML file.
