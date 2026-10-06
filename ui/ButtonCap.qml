// One controller button as it looks on the pad: A B X Y round in their Xbox
// colours, everything else a square cap of the same size, so a column of
// caps lines up whatever the buttons are.
import QtQuick
import qs.Commons
import "../lib/bindings.js" as Bindings

Rectangle {
  id: root

  property string button: ""
  property color ink: Color.foreground
  property string fontFamily: Style.font.family
  property real size: Style.space(22)
  // Held right now on the pad.
  property bool lit: false

  readonly property var cap: Bindings.cap(button)
  readonly property bool face: cap.kind === "face"
  // Glyph size by kind: two-letter caps smaller, arrows and the logo larger.
  readonly property var scales: ({ face: 0.52, dpad: 0.6, shoulder: 0.4, stick: 0.4, system: 0.42, logo: 0.62 })
  readonly property color tint: face ? Bindings.FACE_COLORS[button] : ink

  implicitWidth: size
  implicitHeight: size
  radius: face ? size / 2 : Math.max(3, Math.min(Style.cornerRadius, size / 4))
  color: lit ? (face ? tint : Color.accent) : Util.alpha(tint, face ? 0.14 : 0.07)
  border.width: face && !lit ? 1 : 0
  border.color: Util.alpha(tint, 0.5)

  Behavior on color { ColorAnimation { duration: 90 } }

  Text {
    anchors.centerIn: parent
    text: root.cap.text
    color: root.lit ? Color.background : (root.face ? root.tint : Qt.darker(root.ink, 1.1))
    font.family: root.fontFamily
    font.bold: root.face
    font.pixelSize: Math.round(root.size * (root.scales[root.cap.kind] || 0.45))
  }
}
