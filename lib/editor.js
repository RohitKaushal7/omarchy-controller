// The binding editor's draft (spec §8): made from a row or from scratch,
// checked, and turned into `padctl bind` arguments.

var CLICKS = ["left", "right", "middle", "back", "forward"]
var MOUSE = ["precision", "toggle"]

function blank(layer) {
  return { layer: layer || "base", button: "", slot: "tap", kind: "keys", value: "", label: "",
           repeat: false, original: "" }
}

function fromRow(row) {
  return { layer: row.layer, button: row.button, slot: row.slot, kind: row.kind, value: row.value,
           label: row.label, repeat: row.repeat === true, original: row.id }
}

// What a press on the pad means while recording: the layer comes from the
// layer key held at the time (the latest, as the daemon resolves it).
function captured(config, heldLayerKeys, button) {
  var layers = config && Array.isArray(config.layers) ? config.layers : []
  var layer = layers.length > 0 ? layers[0].id : "base"
  for (var i = heldLayerKeys.length - 1; i >= 0; i--) {
    var found = layers.filter(function(l) { return l.key === heldLayerKeys[i] })[0]
    if (found) { layer = found.id; break }
  }
  return { layer: layer, button: button }
}

function layerKeys(config) {
  var layers = config && Array.isArray(config.layers) ? config.layers : []
  return layers.filter(function(l) { return !!l.key }).map(function(l) { return l.key })
}

// "" when the draft can be saved, otherwise what is missing, in words.
function problem(draft, config) {
  if (!draft.button) return "Record a button first."
  if (layerKeys(config).indexOf(draft.button) >= 0) return draft.button + " holds a layer, so it cannot have an action."
  var value = String(draft.value || "").trim()
  if (draft.kind === "keys" && value === "") return "Type the keys to send, e.g. SUPER+RETURN."
  if (draft.kind === "exec" && value === "") return "Type the command to run."
  if (draft.kind === "click" && CLICKS.indexOf(value) < 0) return "Choose which click."
  if (draft.kind === "mouse" && MOUSE.indexOf(value) < 0) return "Choose a mouse action."
  if (draft.slot === "hold" && draft.kind === "click") return "A click follows the button, so it cannot be a hold."
  if (draft.repeat && (draft.kind !== "keys" || draft.slot === "hold")) return "Only tapped keys can repeat."
  return ""
}

function args(draft) {
  var out = ["bind", draft.layer, draft.button]
  if (draft.slot === "hold") out.push("--hold")
  out.push("--" + draft.kind, String(draft.value).trim())
  if (draft.repeat && draft.kind === "keys" && draft.slot === "tap") out.push("--repeat")
  if (String(draft.label || "").trim() !== "") out.push("--label", String(draft.label).trim())
  if (draft.original) out.push("--replace", draft.original)
  return out
}

function unbindArgs(row) {
  var out = ["unbind", row.layer, row.button]
  if (row.slot === "hold") out.push("--hold")
  return out
}

// A new kind starts from a sensible value instead of the old kind's.
function withKind(draft, kind) {
  var next = Object.assign({}, draft, { kind: kind })
  if (kind !== draft.kind) {
    next.value = kind === "click" ? "left" : (kind === "mouse" ? "toggle" : "")
    if (kind !== "keys") next.repeat = false
    if (kind === "click") next.slot = "tap"
  }
  return next
}

if (typeof module !== "undefined") {
  module.exports = { CLICKS: CLICKS, MOUSE: MOUSE, blank: blank, fromRow: fromRow, captured: captured,
                     layerKeys: layerKeys, problem: problem, args: args, unbindArgs: unbindArgs,
                     withKind: withKind }
}
