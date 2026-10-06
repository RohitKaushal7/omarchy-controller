// Bindings as the popup and the layer hint show them (spec §8): rows built
// from `padctl list --json`, button labels and glyph styles, and search.

var BUTTONS = ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "BACK", "START", "GUIDE",
               "L3", "R3", "UP", "DOWN", "LEFT", "RIGHT"]

// What a button cap shows, and the words a search may use for it.
var CAPS = {
  A: { text: "A", kind: "face", words: "a" },
  B: { text: "B", kind: "face", words: "b" },
  X: { text: "X", kind: "face", words: "x" },
  Y: { text: "Y", kind: "face", words: "y" },
  LB: { text: "LB", kind: "shoulder", words: "lb left bumper l1" },
  RB: { text: "RB", kind: "shoulder", words: "rb right bumper r1" },
  LT: { text: "LT", kind: "shoulder", words: "lt left trigger l2" },
  RT: { text: "RT", kind: "shoulder", words: "rt right trigger r2" },
  // As printed on an Xbox 360 pad, so every cap is one square.
  BACK: { text: "\u25C0", kind: "system", words: "back select view" },
  START: { text: "\u25B6", kind: "system", words: "start menu" },
  GUIDE: { text: String.fromCodePoint(0xF05B9), kind: "logo", words: "guide xbox home mode" },
  L3: { text: "L3", kind: "stick", words: "l3 left stick click" },
  R3: { text: "R3", kind: "stick", words: "r3 right stick click" },
  UP: { text: "↑", kind: "dpad", words: "up dpad d-pad" },
  DOWN: { text: "↓", kind: "dpad", words: "down dpad d-pad" },
  LEFT: { text: "←", kind: "dpad", words: "left dpad d-pad" },
  RIGHT: { text: "→", kind: "dpad", words: "right dpad d-pad" },
  LS: { text: "LS", kind: "stick", words: "ls left stick" },
  RS: { text: "RS", kind: "stick", words: "rs right stick" }
}

// The Xbox face colours, so A B X Y are found by colour as on the pad.
var FACE_COLORS = { A: "#5cb85c", B: "#e0533d", X: "#3d8fe0", Y: "#e8b830" }

var KINDS = ["keys", "exec", "click", "mouse"]

function cap(button) {
  return CAPS[button] || { text: String(button), kind: "system", words: String(button).toLowerCase() }
}

function actionOf(raw) {
  if (!raw || typeof raw !== "object") return null
  for (var i = 0; i < KINDS.length; i++) {
    var kind = KINDS[i]
    if (typeof raw[kind] === "string") {
      return { kind: kind, value: raw[kind], label: raw.label || "", repeat: raw.repeat === true }
    }
  }
  return null
}

function summary(action) {
  if (!action) return ""
  if (action.kind === "keys") return action.value.split("+").join(" + ")
  if (action.kind === "exec") return "$ " + action.value
  if (action.kind === "click") return action.value + " click"
  return action.value === "toggle" ? "mouse on / off" : "precision mode"
}

// One row per action: a button with a tap and a hold gives two rows.
function rows(config) {
  var out = []
  if (!config || !Array.isArray(config.layers)) return out
  var mouse = config.mouse || {}
  config.layers.forEach(function(layer, index) {
    var bindings = layer.bindings || {}
    BUTTONS.forEach(function(button) {
      var raw = bindings[button]
      if (!raw) return
      var split = raw.tap !== undefined || raw.hold !== undefined
      var slots = split ? [["tap", raw.tap], ["hold", raw.hold]] : [["tap", raw]]
      slots.forEach(function(pair) {
        var action = actionOf(pair[1])
        if (!action) return
        out.push({
          id: layer.id + ":" + button + ":" + pair[0],
          layer: layer.id, layerName: layer.name || layer.id, layerKey: layer.key || "",
          layerIndex: index, button: button, slot: pair[0],
          kind: action.kind, value: action.value, label: action.label, repeat: action.repeat,
          summary: summary(action), editable: true
        })
      })
    })
    if (index === 0) {
      // The sticks are the mouse's, set under "mouse" rather than bound.
      if (mouse.move) out.push(stickRow(layer, mouse.move, "Move pointer"))
      if (mouse.scroll) out.push(stickRow(layer, mouse.scroll, "Scroll"))
    }
  })
  return out
}

function stickRow(layer, stick, label) {
  return {
    id: layer.id + ":" + stick + ":stick", layer: layer.id, layerName: layer.name || layer.id,
    layerKey: "", layerIndex: 0, button: stick, slot: "stick", kind: "stick", value: "",
    label: label, repeat: false, summary: "set under \"mouse\" in the config", editable: false
  }
}

function haystack(row) {
  return [row.button, cap(row.button).words, row.label, row.value, row.summary, row.layerName,
          row.layer, row.layerKey ? "hold " + row.layerKey : "", row.slot === "hold" ? "hold" : "",
          row.repeat ? "repeat" : "", row.kind].join(" ").toLowerCase()
}

// Every whitespace-separated term must appear somewhere in the row.
function matches(row, query) {
  var terms = String(query || "").toLowerCase().split(/\s+/).filter(function(t) { return t !== "" })
  if (terms.length === 0) return true
  var text = haystack(row)
  return terms.every(function(term) { return text.indexOf(term) >= 0 })
}

// Rows grouped by layer, in config order, filtered by query and layer.
function groups(config, query, layerId) {
  var all = rows(config)
  var out = []
  if (!config || !Array.isArray(config.layers)) return out
  config.layers.forEach(function(layer) {
    if (layerId && layerId !== "all" && layerId !== layer.id) return
    var shown = all.filter(function(row) { return row.layer === layer.id && matches(row, query) })
    if (shown.length === 0) return
    out.push({ id: layer.id, name: layer.name || layer.id, key: layer.key || "", rows: shown })
  })
  return out
}

// The layer a press of `button` resolves in while `layerId` is active: the
// layer itself if it binds the button, else the base it falls through to.
function resolves(config, layerId, button) {
  var layers = config && Array.isArray(config.layers) ? config.layers : []
  if (layers.length === 0) return ""
  var layer = layers.filter(function(l) { return l.id === layerId })[0] || layers[0]
  if (layer.bindings && layer.bindings[button]) return layer.id
  return layers[0].bindings && layers[0].bindings[button] ? layers[0].id : ""
}

function count(config) {
  return rows(config).filter(function(row) { return row.editable }).length
}

// The hint card: what each button does in one layer, laid out the way the
// pad is held. Buttons the layer leaves alone show what they fall through to.
var HINT_LEFT = ["LB", "UP", "DOWN", "LEFT", "RIGHT", "BACK", "L3"]
var HINT_RIGHT = ["RB", "Y", "X", "B", "A", "START", "R3", "GUIDE"]

function hint(config, layerId) {
  var all = rows(config)
  var layers = config && Array.isArray(config.layers) ? config.layers : []
  var layer = layers.filter(function(l) { return l.id === layerId })[0] || layers[0]
  if (!layer) return { name: "", key: "", left: [], right: [] }
  var base = layers[0]
  function side(buttons) {
    return buttons.map(function(button) {
      function find(id, slot) {
        return all.filter(function(r) { return r.layer === id && r.button === button && r.slot === slot })[0]
      }
      var own = find(layer.id, "tap") || find(layer.id, "hold")
      var hold = find(layer.id, "hold")
      var through = !own && layer !== base ? (find(base.id, "tap") || find(base.id, "hold")) : null
      var row = own || through
      return {
        button: button,
        label: row ? row.label || row.summary : "",
        hold: hold && hold !== own ? hold.label || hold.summary : (own && own.slot === "hold" ? "hold" : ""),
        inherited: !!through
      }
    }).filter(function(item) { return item.label !== "" })
  }
  return { name: layer.name || layer.id, key: layer.key || "", left: side(HINT_LEFT), right: side(HINT_RIGHT) }
}

if (typeof module !== "undefined") {
  module.exports = { BUTTONS: BUTTONS, CAPS: CAPS, FACE_COLORS: FACE_COLORS, cap: cap, actionOf: actionOf,
                     summary: summary, rows: rows, matches: matches, groups: groups, count: count, resolves: resolves,
                     hint: hint }
}
