// The controller service (spec §2, §8): runs and supervises the daemon,
// keeps the state every bar widget shows, applies edits through padctl,
// owns the dev.reuk.controller IPC target, and hosts the layer hint.
// Loaded once per shell and kept across hot-reloads (keepLoaded), so the
// pad keeps working while the widget's code changes.
//
// No qs.* imports here; the hint, which needs the theme, is loaded from
// ui/Hint.qml.

import QtQuick
import Quickshell
import Quickshell.Io
import "lib/protocol.js" as Protocol
import "lib/editor.js" as Editor

Item {
  id: root
  visible: false

  // Injected by omarchy-shell.
  property var shell: null
  property var manifest: null

  readonly property string pluginDir: decodeURIComponent(String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "")).replace(/\/$/, "")
  readonly property string padctl: pluginDir + "/bin/padctl"

  // ------------------------------------------------------------------ state

  property bool running: false
  property bool connected: false
  property string device: ""
  property bool switchedOn: true
  property bool paused: false
  property bool mouseOn: true
  property bool precise: false
  property string activeLayer: "base"
  property var held: []
  property string lastError: ""
  onLastErrorChanged: if (lastError !== "") errorTimer.restart()
  // A shown error is about something just done; it goes after a while.
  Timer {
    id: errorTimer
    interval: 8000
    onTriggered: root.lastError = ""
  }
  // The last action that ran: {button, layer, label, action, ok, at}.
  property var lastFire: null
  // `padctl list --json`: the config with every default filled in.
  property var config: null
  property string configPath: ""

  // Recording a button for the editor: the daemon is paused meanwhile, so
  // the press does not also run what the button does now.
  property bool capturing: false
  signal captured(string layer, string button)
  // A pad button went down (layer keys included), whatever it is bound to.
  signal buttonDown(string button)
  signal fired(string button, string layer, string slot, string label)

  readonly property var layerKeys: Editor.layerKeys(config)
  readonly property int hintDelay: config && config.timing ? config.timing.hintDelayMs : 350

  function buttonName(button) {
    return ({ GUIDE: "Guide", BACK: "Back", START: "Start" })[button] || button
  }

  function send(cmd, extra) {
    if (daemon.running) daemon.write(Protocol.command(cmd, extra))
  }

  // ---------------------------------------------------------------- daemon

  Process {
    id: daemon
    command: [root.padctl, "daemon"]
    stdinEnabled: true
    running: false
    stdout: SplitParser {
      onRead: function(line) { root.handle(line) }
    }
    stderr: SplitParser {
      onRead: function(line) { if (line.trim() !== "") root.lastError = line.trim() }
    }
    onRunningChanged: {
      root.running = running
      if (!running) {
        root.connected = false
        root.held = []
        root.activeLayer = "base"
        restartTimer.restart()
      }
    }
  }

  // A crashed daemon means a dead controller, so it comes back by itself.
  Timer {
    id: restartTimer
    interval: 3000
    onTriggered: if (!daemon.running) daemon.running = true
  }

  function handle(line) {
    var e = Protocol.parse(line)
    if (!e) return
    switch (e.event) {
    case "state":
      root.connected = e.connected === true
      root.device = String(e.device || "")
      if (root.config && root.switchedOn !== (e.enabled !== false)) {
        var combo = root.config.toggleCombo || []
        hint.notice(e.enabled !== false ? "Controller on"
          : "Controller off" + (combo.length ? "  ·  " + combo.map(root.buttonName).join(" + ") + " to turn on" : ""))
      }
      root.switchedOn = e.enabled !== false
      root.paused = e.paused === true
      if (root.switchedOn && root.mouseOn !== (e.mouse !== false) && root.config) hint.notice(e.mouse !== false ? "Mouse on" : "Mouse off")
      else if (root.precise !== (e.precise === true) && e.mouse !== false) hint.notice(e.precise === true ? "Precision on" : "Precision off")
      root.mouseOn = e.mouse !== false
      root.precise = e.precise === true
      root.activeLayer = String(e.layer || "base")
      root.configPath = String(e.config || root.configPath)
      break
    case "connected":
      root.connected = true
      root.device = String(e.name || "")
      break
    case "disconnected":
      root.held = []
      break
    case "pad":
      root.held = Array.isArray(e.held) ? e.held : []
      if (e.pressed === true) root.buttonDown(String(e.button))
      if (root.capturing && e.pressed === true && root.layerKeys.indexOf(e.button) < 0) {
        var keys = root.held.filter(function(b) { return root.layerKeys.indexOf(b) >= 0 })
        var got = Editor.captured(root.config, keys, String(e.button))
        root.stopCapture()
        root.captured(got.layer, got.button)
      }
      break
    case "layer":
      root.activeLayer = String(e.layer || "base")
      break
    case "fire":
      root.lastFire = { button: e.button, layer: e.layer, slot: e.slot, label: e.label,
                        action: e.action, ok: e.ok === true, at: Date.now() }
      root.fired(String(e.button), String(e.layer), String(e.slot), String(e.label || e.action))
      if (e.ok === false && e.detail && e.detail !== "mouse is off") root.lastError = String(e.detail)
      break
    case "error":
      root.lastError = String(e.message || "")
      break
    }
  }

  // ---------------------------------------------------------------- config

  Process {
    id: lister
    command: [root.padctl, "list", "--json"]
    stdout: StdioCollector {
      onStreamFinished: {
        try {
          var parsed = JSON.parse(text)
          root.configPath = String(parsed.path || "")
          root.config = parsed
        } catch (e) {}
      }
    }
    stderr: StdioCollector {
      onStreamFinished: if (text.trim() !== "") root.lastError = text.trim()
    }
  }

  function refresh() {
    if (!lister.running) lister.running = true
  }

  // Edits made anywhere (the panel, padctl, a text editor) land in the file;
  // re-read it, and have the daemon re-read it too.
  FileView {
    path: root.configPath
    watchChanges: root.configPath !== ""
    printErrors: false
    onFileChanged: {
      root.refresh()
      root.send("reload")
    }
  }

  // padctl edits, one at a time. onDone(ok, message) after each.
  property var queue: []
  property var current: null

  function edit(args, onDone) {
    queue = queue.concat([{ args: args, done: onDone || null }])
    pump()
  }

  function pump() {
    if (current || queue.length === 0) return
    current = queue[0]
    queue = queue.slice(1)
    editor.command = [root.padctl].concat(current.args)
    editor.running = true
  }

  Process {
    id: editor
    stderr: StdioCollector {
      id: editorErr
      waitForEnd: true
    }
    onExited: function(code) {
      var job = root.current
      root.current = null
      // The file watch reloads too; asking here makes the change immediate
      // even when the watch is not set up yet (first save of a new config).
      root.refresh()
      root.send("reload")
      if (job && job.done) job.done(code === 0, code === 0 ? "" : (editorErr.text.trim().replace(/^padctl: /, "") || "padctl failed"))
      root.pump()
    }
  }

  function setEnabled(on) { edit(["set", "enabled", on ? "true" : "false"]) }
  function setMouseDefault(on) {
    edit(["set", "mouse.enabled", on ? "true" : "false"])
    send("mouse", { on: on })
  }
  function toggleMouse() { send("mouse", { on: "toggle" }) }

  function startCapture() {
    capturing = true
    send("pause")
  }
  function stopCapture() {
    if (!capturing) return
    capturing = false
    send("resume")
  }

  // ------------------------------------------------------------------- hint

  Loader {
    id: hintLoader
    source: "ui/Hint.qml"
    onLoaded: {
      item.service = root
    }
  }
  readonly property var hint: hintLoader.item || ({ notice: function() {}, request: function() {} })

  // ------------------------------------------------------------------- IPC

  IpcHandler {
    target: "dev.reuk.controller"

    function status(): string {
      return JSON.stringify({ running: root.running, connected: root.connected, device: root.device,
                              enabled: root.switchedOn, paused: root.paused, mouse: root.mouseOn,
                              precise: root.precise, layer: root.activeLayer, held: root.held,
                              config: root.configPath, error: root.lastError })
    }
    function reload(): string { root.refresh(); root.send("reload"); return "ok" }
    function on(): string { root.setEnabled(true); return "ok" }
    function off(): string { root.setEnabled(false); return "ok" }
    function toggle(): string { root.setEnabled(!root.switchedOn); return root.switchedOn ? "off" : "on" }
    function mouse(): string { root.toggleMouse(); return root.mouseOn ? "off" : "on" }
    function hint(): string { root.hint.request(); return "ok" }
  }

  Component.onCompleted: {
    refresh()
    daemon.running = true
  }
  Component.onDestruction: {
    send("quit")
  }
}
