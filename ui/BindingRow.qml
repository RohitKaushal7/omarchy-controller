// One action in the list, on one line: the button, what it does, and, dim on
// the right, what it sends. Lights while its button would fire it, flashes
// when it runs, opens the editor on click; delete shows on hover.
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

Item {
  id: root

  property var row: ({})
  property color ink: Color.foreground
  property string fontFamily: Style.font.family
  property bool lit: false
  property bool selected: false

  signal editRequested()
  signal deleteRequested()

  readonly property bool hot: hover.containsMouse || remove.hovering
  readonly property string how: row.slot === "hold" ? "hold" : (row.repeat ? "repeats" : "")

  implicitHeight: Style.space(30)

  function flash() { flashAnim.restart() }

  Rectangle {
    anchors.fill: parent
    radius: Math.max(3, Style.cornerRadius)
    color: root.selected ? Style.selectedAccentFill
         : (root.hot && root.row.editable ? Style.hoverFillFor(root.ink, Color.accent, Color.urgent) : "transparent")

    Rectangle {
      id: flashFill
      anchors.fill: parent
      radius: parent.radius
      color: Color.accent
      opacity: 0
      SequentialAnimation {
        id: flashAnim
        NumberAnimation { target: flashFill; property: "opacity"; to: 0.25; duration: 60 }
        NumberAnimation { target: flashFill; property: "opacity"; to: 0; duration: 520; easing.type: Easing.OutCubic }
      }
    }
  }

  MouseArea {
    id: hover
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: root.row.editable ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: if (root.row.editable) root.editRequested()
  }

  RowLayout {
    anchors.fill: parent
    anchors.leftMargin: Style.space(6)
    anchors.rightMargin: Style.space(2)
    spacing: Style.space(10)

    ButtonCap {
      button: root.row.button || ""
      ink: root.ink
      fontFamily: root.fontFamily
      lit: root.lit
    }

    Text {
      Layout.fillWidth: true
      text: root.row.label || root.row.summary || ""
      color: root.ink
      elide: Text.ElideRight
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
    }

    Text {
      visible: root.how !== ""
      text: root.how
      color: Color.accent
      opacity: 0.8
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }

    Text {
      Layout.maximumWidth: root.width * 0.42
      visible: root.row.label !== ""
      text: root.row.summary || ""
      color: Qt.darker(root.ink, 1.7)
      elide: Text.ElideMiddle
      horizontalAlignment: Text.AlignRight
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
    }

    // Always laid out, so summaries do not shift when it appears.
    PanelActionButton {
      id: remove
      readonly property bool hovering: _hot
      enabled: root.row.editable === true
      opacity: root.row.editable === true && root.hot ? 1 : 0
      iconText: String.fromCodePoint(0xF01B4) // md-delete
      tooltipText: "Remove"
      foreground: root.ink
      hoverColor: Color.urgent
      onClicked: root.deleteRequested()
    }
  }
}
