// Add or edit one binding (spec §8). A new binding starts by listening: press
// the button on the pad, holding LT or RT first for those layers. Then pick
// when it fires and what it does, give it a label, save.
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui
import "../lib/editor.js" as Editor

ColumnLayout {
  id: root

  property var service: null
  property var draft: Editor.blank()
  property color ink: Color.foreground
  property string fontFamily: Style.font.family
  property string error: ""
  property bool saving: false

  readonly property var config: service ? service.config : null
  readonly property bool editing: draft.original !== ""
  readonly property bool capturing: service ? service.capturing === true : false
  readonly property bool textual: draft.kind === "keys" || draft.kind === "exec"
  // The draft with what is typed so far.
  readonly property var effective: Object.assign({}, draft, {
    value: textual ? valueField.text : draft.value, label: labelField.text })
  readonly property string problem: Editor.problem(effective, config)
  readonly property bool textFocused: valueField.activeFocus || labelField.activeFocus
  readonly property var draftLayer: config ? config.layers.filter(function(l) { return l.id === draft.layer })[0] || null : null
  readonly property color dim: Qt.darker(ink, 1.7)

  signal closed()

  spacing: Style.space(12)

  function set(changes) {
    draft = Object.assign({}, draft, changes)
    error = ""
  }

  function load(next) {
    draft = next
    error = ""
    valueField.text = next.kind === "keys" || next.kind === "exec" ? next.value : ""
    labelField.text = next.label
    // Nothing to edit until there is a button: listen for it straight away.
    if (next.button === "" && service && service.connected) service.startCapture()
  }

  function save() {
    if (problem !== "" || !service || saving) return
    saving = true
    service.edit(Editor.args(effective), function(ok, message) {
      saving = false
      if (ok) root.closed()
      else root.error = message
    })
  }

  function remove() {
    if (!service || !editing) return
    var parts = draft.original.split(":")
    service.edit(Editor.unbindArgs({ layer: parts[0], button: parts[1], slot: parts[2] }), function(ok, message) {
      if (ok) root.closed()
      else root.error = message
    })
  }

  Connections {
    target: root.service
    function onCaptured(layer, button) {
      root.set({ layer: layer, button: button })
      if (root.textual) valueField.forceActiveFocus()
    }
  }

  PanelSectionHeader {
    Layout.fillWidth: true
    text: root.editing ? "EDIT BINDING" : "NEW BINDING"
    foreground: Qt.darker(root.ink, 1.3)
  }

  // ---- the button ----------------------------------------------------------

  // One tile is the whole first step: listening, or the button it got.
  Rectangle {
    Layout.fillWidth: true
    implicitHeight: Style.space(56)
    radius: Math.max(3, Style.cornerRadius)
    color: root.capturing ? Util.alpha(Color.accent, 0.10) : Style.hoverFillFor(root.ink, Color.accent, Color.urgent)
    border.width: 1
    border.color: root.capturing ? Color.accent : Util.alpha(root.ink, 0.12)

    SequentialAnimation on opacity {
      running: root.capturing
      loops: Animation.Infinite
      alwaysRunToEnd: true
      NumberAnimation { to: 0.6; duration: 700; easing.type: Easing.InOutSine }
      NumberAnimation { to: 1; duration: 700; easing.type: Easing.InOutSine }
    }

    MouseArea {
      anchors.fill: parent
      enabled: root.draft.button === "" || root.capturing
      cursorShape: Qt.PointingHandCursor
      onClicked: {
        if (!root.service) return
        if (root.capturing) root.service.stopCapture()
        else if (root.service.connected) root.service.startCapture()
      }
    }

    RowLayout {
      anchors.fill: parent
      anchors.leftMargin: Style.space(14)
      anchors.rightMargin: Style.space(14)
      spacing: Style.space(12)

      ButtonCap {
        visible: root.draft.button !== "" && !root.capturing
        button: root.draft.button
        size: Style.space(30)
        ink: root.ink
        fontFamily: root.fontFamily
      }
      ColumnLayout {
        Layout.fillWidth: true
        spacing: Style.space(1)
        Text {
          Layout.fillWidth: true
          text: root.capturing ? "Press a button on the controller"
              : root.draft.button === "" ? (root.service && root.service.connected ? "Record a button" : "Connect the controller")
              : (root.draftLayer ? root.draftLayer.name : root.draft.layer)
          color: root.capturing ? Color.accent : root.ink
          elide: Text.ElideRight
          font.family: root.fontFamily
          font.pixelSize: Style.font.body
          font.bold: true
        }
        Text {
          Layout.fillWidth: true
          text: root.capturing ? "Hold LT or RT first to bind it in that layer"
              : root.draft.button === "" ? ""
              : (root.draftLayer && root.draftLayer.key ? "Hold " + root.draftLayer.key + ", then press it" : "Press it on its own")
          visible: text !== ""
          color: root.dim
          elide: Text.ElideRight
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
      TextLink {
        visible: !root.capturing && root.draft.button !== ""
        text: "Record again"
        onClicked: if (root.service) root.service.startCapture()
      }
    }
  }

  // ---- when, and what ------------------------------------------------------

  GridLayout {
    Layout.fillWidth: true
    columns: 2
    columnSpacing: Style.space(16)
    rowSpacing: Style.space(4)

    Text { text: "When"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
    Segmented {
      ink: root.ink
      fontFamily: root.fontFamily
      value: root.draft.slot
      options: [{ value: "tap", label: "Tap" }, { value: "hold", label: "Hold" }]
      onPicked: function(value) { root.set({ slot: value, repeat: value === "hold" ? false : root.draft.repeat }) }
    }

    Text { text: "Does"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
    Segmented {
      ink: root.ink
      fontFamily: root.fontFamily
      value: root.draft.kind
      options: [{ value: "keys", label: "Keys" }, { value: "exec", label: "Command" },
                { value: "click", label: "Click" }, { value: "mouse", label: "Mouse" }]
      onPicked: function(value) {
        root.draft = Editor.withKind(root.draft, value)
        valueField.text = ""
      }
    }

    Text {
      visible: !root.textual
      text: "Which"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
    }
    Segmented {
      visible: !root.textual
      ink: root.ink
      fontFamily: root.fontFamily
      value: root.draft.value
      options: root.draft.kind === "click"
        ? Editor.CLICKS.map(function(c) { return { value: c, label: c.charAt(0).toUpperCase() + c.slice(1) } })
        : [{ value: "toggle", label: "On / off" }, { value: "precision", label: "Precision" }]
      onPicked: function(value) { root.set({ value: value }) }
    }
  }

  TextField {
    id: valueField
    Layout.fillWidth: true
    visible: root.textual
    foreground: root.ink
    font.family: root.fontFamily
    placeholderText: root.draft.kind === "exec" ? "Command to run, e.g. omarchy capture screenshot" : "Keys to send, e.g. SUPER+RETURN"
    onTextEdited: root.error = ""
    onAccepted: root.save()
    Keys.onEscapePressed: root.closed()
  }

  TextField {
    id: labelField
    Layout.fillWidth: true
    foreground: root.ink
    font.family: root.fontFamily
    placeholderText: "Label, e.g. Next tab"
    onAccepted: root.save()
    Keys.onEscapePressed: root.closed()
  }

  // ---- what is missing, and the buttons -----------------------------------

  Text {
    Layout.fillWidth: true
    readonly property string why: root.error !== "" ? root.error : (root.draft.button === "" ? "" : root.problem)
    visible: why !== ""
    text: why
    color: root.error !== "" ? Color.urgent : root.dim
    wrapMode: Text.WordWrap
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }

  RowLayout {
    Layout.fillWidth: true
    spacing: Style.space(14)

    // A tick box drawn from the theme, for the one yes-or-no choice.
    Item {
      visible: root.draft.kind === "keys" && root.draft.slot === "tap"
      implicitWidth: tick.implicitWidth
      implicitHeight: tick.implicitHeight
      Row {
        id: tick
        spacing: Style.space(6)
        Rectangle {
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(14)
          height: width
          radius: Math.max(2, Style.cornerRadius / 2)
          color: root.draft.repeat ? Color.accent : "transparent"
          border.width: root.draft.repeat ? 0 : 1
          border.color: Util.alpha(root.ink, 0.4)
          Text {
            anchors.centerIn: parent
            visible: root.draft.repeat
            text: "✓"
            color: Color.background
            font.pixelSize: Math.round(parent.height * 0.8)
            font.bold: true
          }
        }
        Text {
          anchors.verticalCenter: parent.verticalCenter
          text: "Repeat while held"
          color: root.ink
          font.family: root.fontFamily
          font.pixelSize: Style.font.caption
        }
      }
      MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.set({ repeat: !root.draft.repeat })
      }
    }

    Item { Layout.fillWidth: true }

    TextLink {
      visible: root.editing
      text: "Delete"
      hoverColor: Color.urgent
      onClicked: root.remove()
    }
    TextLink {
      text: "Cancel"
      onClicked: root.closed()
    }
    Button {
      text: root.saving ? "Saving…" : (root.editing ? "Save" : "Add")
      bordered: true
      selected: true
      foreground: root.ink
      fontFamily: root.fontFamily
      enabled: !root.saving && root.problem === ""
      onClicked: root.save()
    }
  }

  component TextLink: Text {
    id: link
    property color hoverColor: root.ink
    signal clicked()
    color: linkMouse.containsMouse ? hoverColor : root.dim
    font.family: root.fontFamily
    font.pixelSize: Style.font.body
    MouseArea {
      id: linkMouse
      anchors.fill: parent
      anchors.margins: -Style.space(6)
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: link.clicked()
    }
  }
}
