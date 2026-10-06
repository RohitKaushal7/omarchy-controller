// Pick one of a few options: text with an accent underline under the chosen
// one, in place of a row of boxed buttons. An option can carry a button cap
// (`key`), as the layer tabs do. Options: [{value, label, key?}].
import QtQuick
import qs.Commons

Row {
  id: root

  property var options: []
  property string value: ""
  // An option to light, such as the layer held on the pad right now.
  property string live: ""
  property color ink: Color.foreground
  property string fontFamily: Style.font.family
  property real fontSize: Style.font.body

  signal picked(string value)

  spacing: Style.space(16)

  Repeater {
    model: root.options

    Item {
      id: option
      required property var modelData
      readonly property bool current: root.value === option.modelData.value
      implicitWidth: line.implicitWidth
      implicitHeight: line.implicitHeight + Style.space(8)

      Row {
        id: line
        spacing: Style.space(5)
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: option.modelData.label
          color: option.current ? root.ink : Qt.darker(root.ink, mouse.containsMouse ? 1.3 : 1.7)
          font.family: root.fontFamily
          font.pixelSize: root.fontSize
          font.bold: option.current
        }
        ButtonCap {
          visible: !!option.modelData.key
          anchors.verticalCenter: parent.verticalCenter
          button: option.modelData.key || ""
          size: Math.round(root.fontSize * 1.3)
          lit: root.live !== "" && root.live === option.modelData.value
          ink: root.ink
          fontFamily: root.fontFamily
        }
      }

      Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: Math.max(2, Style.space(2))
        radius: height / 2
        color: Color.accent
        visible: option.current
      }

      MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.picked(option.modelData.value)
      }
    }
  }
}
