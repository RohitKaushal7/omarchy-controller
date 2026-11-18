const test = require("node:test")
const assert = require("node:assert")
const E = require("../../lib/editor.js")
const B = require("../../lib/bindings.js")
const P = require("../../lib/protocol.js")
const config = require("./fixture.json")

test("an edited row saves over itself, moving if re-recorded", () => {
  const row = B.rows(config).find(r => r.id === "text:B:tap")
  const draft = E.fromRow(row)
  assert.deepStrictEqual(E.args(draft),
    ["bind", "text", "B", "--keys", "BACKSPACE", "--repeat", "--label", "Backspace", "--replace", "text:B:tap"])
  draft.button = "Y"
  assert.deepStrictEqual(E.args(draft).slice(0, 3), ["bind", "text", "Y"])
})

test("hold drafts pass --hold and never --repeat", () => {
  const draft = Object.assign(E.blank("system"), { button: "X", slot: "hold", value: "SUPER+Q", repeat: true })
  assert.notStrictEqual(E.problem(draft, config), "")
  draft.repeat = false
  assert.strictEqual(E.problem(draft, config), "")
  assert.deepStrictEqual(E.args(draft), ["bind", "system", "X", "--hold", "--keys", "SUPER+Q"])
})

test("problems are explained", () => {
  assert.match(E.problem(E.blank(), config), /Record/)
  assert.match(E.problem(Object.assign(E.blank(), { button: "LT", value: "A" }), config), /holds a layer/)
  assert.match(E.problem(Object.assign(E.blank(), { button: "A" }), config), /keys to send/)
})

test("recording picks the layer from the held layer key", () => {
  assert.deepStrictEqual(E.captured(config, [], "A"), { layer: "base", button: "A" })
  assert.deepStrictEqual(E.captured(config, ["LT"], "A"), { layer: "text", button: "A" })
  assert.deepStrictEqual(E.captured(config, ["LT", "RT"], "B"), { layer: "system", button: "B" })
})

test("changing kind resets the value", () => {
  const draft = E.withKind(Object.assign(E.blank(), { value: "SUPER+W", repeat: true }), "click")
  assert.deepStrictEqual([draft.value, draft.repeat, draft.slot], ["left", false, "tap"])
})

test("unbind removes just the slot", () => {
  assert.deepStrictEqual(E.unbindArgs({ layer: "system", button: "BACK", slot: "hold" }),
                         ["unbind", "system", "BACK", "--hold"])
})

test("protocol lines", () => {
  assert.strictEqual(P.command("mouse", { on: "toggle" }), '{"cmd":"mouse","on":"toggle"}\n')
  assert.strictEqual(P.parse('{"event":"layer","layer":"text"}').layer, "text")
  assert.strictEqual(P.parse('{"event":"bogus"}'), null)
  assert.strictEqual(P.parse("not json"), null)
  assert.strictEqual(P.parse("[1]"), null)
})

test("saving over another action says what it replaces", () => {
  // +, record A, keys SPACE: A's click would go.
  const fresh = Object.assign(E.blank("base"), { button: "A", value: "SPACE" })
  assert.strictEqual(E.replaces(fresh, config), "Click")
  // Editing a row in place replaces nothing but itself.
  const back = E.fromRow(B.rows(config).find(r => r.id === "system:BACK:tap"))
  assert.strictEqual(E.replaces(back, config), "")
  // Switched from tap to hold, it would land on the hold.
  assert.strictEqual(E.replaces(Object.assign({}, back, { slot: "hold" }), config), "Lock")
  // Recorded again onto another bound button.
  assert.strictEqual(E.replaces(Object.assign({}, back, { button: "A" }), config), "Video fullscreen")
  // Free slots: a hold where only a tap is bound, a button with nothing.
  assert.strictEqual(E.replaces(Object.assign({}, fresh, { slot: "hold" }), config), "")
  assert.strictEqual(E.replaces(Object.assign(E.blank("system"), { button: "B" }), config), "")
  assert.strictEqual(E.replaces(Object.assign(E.blank("system"), { button: "B", slot: "hold" }), config), "Close window")
  assert.strictEqual(E.replaces(E.blank("base"), config), "")
  // No label: what it sends.
  const unlabelled = { layers: [{ id: "base", bindings: { X: { keys: "SUPER+Q" } } }] }
  assert.strictEqual(E.replaces(Object.assign(E.blank("base"), { button: "X" }), unlabelled), "SUPER+Q")
})
