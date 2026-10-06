const test = require("node:test")
const assert = require("node:assert")
const B = require("../../lib/bindings.js")
const config = require("./fixture.json")

test("a tap and a hold on one button are two rows", () => {
  const back = B.rows(config).filter(r => r.layer === "system" && r.button === "BACK")
  assert.deepStrictEqual(back.map(r => [r.slot, r.label]), [["tap", "Former workspace"], ["hold", "Lock"]])
})

test("the sticks show in the base layer but cannot be edited", () => {
  const sticks = B.rows(config).filter(r => r.kind === "stick")
  assert.deepStrictEqual(sticks.map(r => [r.button, r.label, r.editable]),
                         [["LS", "Move pointer", false], ["RS", "Scroll", false]])
  assert.strictEqual(B.count(config), B.rows(config).length - 2)
})

test("search matches labels, keys, buttons, layers and synonyms", () => {
  const find = q => B.groups(config, q, "all").flatMap(g => g.rows.map(r => r.id))
  assert.deepStrictEqual(find("backspace"), ["text:B:tap", "text:X:tap"])
  assert.deepStrictEqual(find("volume up"), ["system:UP:tap"])
  assert.ok(find("super+w").includes("system:B:hold"))
  assert.ok(find("bumper").length >= 4)
  assert.ok(find("text").every(id => id.startsWith("text:")))
  assert.deepStrictEqual(find("hold lock"), ["system:BACK:hold"])
  assert.deepStrictEqual(find("nothing like this"), [])
})

test("groups follow config order and the layer filter", () => {
  assert.deepStrictEqual(B.groups(config, "", "all").map(g => g.id), ["base", "text", "system"])
  assert.deepStrictEqual(B.groups(config, "", "text").map(g => [g.id, g.key]), [["text", "LT"]])
})

test("the hint shows a layer's own actions and what falls through", () => {
  const text = B.hint(config, "text")
  assert.strictEqual(text.key, "LT")
  const a = text.right.find(i => i.button === "A")
  assert.deepStrictEqual([a.label, a.inherited], ["Enter", false])
  const guide = text.right.find(i => i.button === "GUIDE")
  assert.strictEqual(guide.inherited, true)
  const sys = B.hint(config, "system")
  const back = sys.left.find(i => i.button === "BACK")
  assert.deepStrictEqual([back.label, back.hold], ["Former workspace", "Lock"])
  const close = sys.right.find(i => i.button === "B")
  assert.deepStrictEqual([close.label, close.hold], ["Close window", "hold"])
})

test("summaries read as what is sent", () => {
  assert.strictEqual(B.summary({ kind: "keys", value: "SUPER+W" }), "SUPER + W")
  assert.strictEqual(B.summary({ kind: "exec", value: "true" }), "$ true")
  assert.strictEqual(B.summary({ kind: "click", value: "left" }), "left click")
})

test("a held button resolves in the live layer or falls through", () => {
  assert.strictEqual(B.resolves(config, "text", "A"), "text")
  assert.strictEqual(B.resolves(config, "text", "GUIDE"), "base")
  assert.strictEqual(B.resolves(config, "base", "A"), "base")
  assert.strictEqual(B.resolves(config, "base", "LT"), "")
})
