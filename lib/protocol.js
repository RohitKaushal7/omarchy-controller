// The daemon's protocol (spec §7) from the shell's side: command lines in,
// and event lines out, checked before anything reads them.

var EVENTS = ["started", "state", "connected", "disconnected", "pad", "layer", "fire",
              "reloaded", "error", "stopped"]

function command(cmd, extra) {
  var message = { cmd: cmd }
  if (extra) for (var key in extra) message[key] = extra[key]
  return JSON.stringify(message) + "\n"
}

// The event object, or null for anything that is not one.
function parse(line) {
  if (typeof line !== "string" || line.length === 0 || line.length > 65536) return null
  var message
  try {
    message = JSON.parse(line)
  } catch (e) {
    return null
  }
  if (!message || typeof message !== "object" || Array.isArray(message)) return null
  if (EVENTS.indexOf(message.event) < 0) return null
  return message
}

if (typeof module !== "undefined") {
  module.exports = { EVENTS: EVENTS, command: command, parse: parse }
}
