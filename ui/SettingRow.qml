// A switch with an icon, a title and one line saying what it does. The line
// can end with buttons drawn as caps, for the pad shortcut that does the same.
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

RowLayout {
  id: root

  property string icon: ""
  property string title: ""
  property string description: ""
  // Pad buttons for the same switch, shown after the description: ["GUIDE", "BACK"].
  property var shortcut: []
  property bool checked: false
  property color ink: Color.foreground
  property string fontFamily: Style.font.family

  signal toggled()

  spacing: Style.space(12)

  Text {
    Layout.preferredWidth: Style.space(26)
    Layout.alignment: Qt.AlignVCenter
    horizontalAlignment: Text.AlignHCenter
    text: root.icon
    color: root.checked ? Color.accent : Qt.darker(root.ink, 1.5)
    font.family: root.fontFamily
    font.pixelSize: Style.font.icon
    Behavior on color { ColorAnimation { duration: 150 } }
  }

  ColumnLayout {
    Layout.fillWidth: true
    spacing: Style.space(3)

    Text {
      Layout.fillWidth: true
      text: root.title
      color: root.ink
      elide: Text.ElideRight
      font.family: root.fontFamily
      font.pixelSize: Style.font.body
    }
    Flow {
      Layout.fillWidth: true
      spacing: Style.space(4)
      Text {
        height: Style.space(16)
        verticalAlignment: Text.AlignVCenter
        text: root.description
        color: Qt.darker(root.ink, 1.5)
        font.family: root.fontFamily
        font.pixelSize: Style.font.caption
      }
      Repeater {
        model: root.shortcut
        Row {
          id: part
          required property string modelData
          required property int index
          spacing: Style.space(4)
          Text {
            visible: part.index > 0
            height: Style.space(16)
            verticalAlignment: Text.AlignVCenter
            text: "+"
            color: Qt.darker(root.ink, 1.5)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
          ButtonCap {
            button: part.modelData
            size: Style.space(16)
            ink: root.ink
            fontFamily: root.fontFamily
          }
        }
      }
    }
  }

  ToggleSwitch {
    Layout.alignment: Qt.AlignVCenter
    checked: root.checked
    foreground: root.ink
    onToggled: root.toggled()
  }
}
