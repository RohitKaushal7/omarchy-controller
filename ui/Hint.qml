// The layer hint (spec §8): while a layer key is held past hintDelayMs with
// nothing else pressed, a card near the bottom of the screen shows what every
// button does in that layer, laid out as the pad is held. Also shows the base layer's map on
// request (GUIDE tap, `omarchy-shell dev.reuk.controller hint`) and short
// notices such as "Mouse off". It never takes input.
import QtQuick
import Quickshell
import Quickshell.Wayland
import qs.Commons
import qs.Ui
import "../lib/bindings.js" as Bindings

Item {
  id: root

  property var service: null

  // "map" (a layer's buttons), "notice" (one line), or "".
  property string mode: ""
  property string mapLayer: ""
  property string message: ""

  readonly property string activeLayer: service ? service.activeLayer : "base"
  readonly property var map: Bindings.hint(service ? service.config : null, mapLayer)
  readonly property var held: service ? service.held : []
  readonly property bool shown: mode !== ""

  function showMap(layerId, seconds) {
    mapLayer = layerId || "base"
    mode = "map"
    // On request the card stays a while; held, it goes with the layer key.
    if (seconds) hideTimer.interval = seconds * 1000
    if (seconds) hideTimer.restart()
    else hideTimer.stop()
  }

  function notice(text) {
    if (mode === "map" && activeLayer !== "base") return
    message = text
    mode = "notice"
    hideTimer.interval = 1200
    hideTimer.restart()
  }

  onActiveLayerChanged: {
    if (activeLayer !== "base") {
      holdTimer.interval = service ? service.hintDelay : 350
      holdTimer.restart()
    } else {
      holdTimer.stop()
      if (mode === "map" && !hideTimer.running) mode = ""
    }
  }

  // GUIDE's tap asks for the map through IPC.
  function request() { showMap(activeLayer, 6) }

  // Pressing a button in the layer before the card is up means the layer is
  // already known: skip the card for this hold. Once up, it stays up.
  Connections {
    target: root.service
    function onButtonDown(button) {
      if (holdTimer.running && root.service.layerKeys.indexOf(button) < 0) holdTimer.stop()
    }
  }

  Timer {
    id: holdTimer
    onTriggered: if (root.activeLayer !== "base") root.showMap(root.activeLayer, 0)
  }
  Timer {
    id: hideTimer
    onTriggered: root.mode = ""
  }

  readonly property int pad: Style.space(16)
  readonly property int capSize: Style.space(26)

  PanelWindow {
    visible: root.shown
    anchors { top: true; bottom: true; left: true; right: true }
    color: "transparent"
    WlrLayershell.namespace: "dev-reuk-controller-hint"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
    exclusionMode: ExclusionMode.Ignore
    mask: Region {}

    BorderSurface {
      id: card
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.bottom: parent.bottom
      anchors.bottomMargin: Style.space(67)
      width: body.implicitWidth + card.borderLeft + card.borderRight + root.pad * 2
      height: body.implicitHeight + card.borderTop + card.borderBottom + root.pad * 2
      color: Util.alpha(Color.background, 0.96)
      borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
      radius: Style.cornerRadius

      Column {
        id: body
        x: card.borderLeft + root.pad
        y: card.borderTop + root.pad
        spacing: Style.space(12)

        Text {
          visible: root.mode === "notice"
          text: root.message
          color: Color.popups.text
          font.family: Style.font.family
          font.pixelSize: Style.font.title
          font.bold: true
        }

        Row {
          visible: root.mode === "map"
          spacing: Style.space(8)
          ButtonCap {
            visible: root.map.key !== ""
            button: root.map.key
            size: root.capSize
            lit: true
            ink: Color.popups.text
          }
          Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.map.name + (root.map.key !== "" ? "" : "  ·  sticks move and scroll")
            color: Color.popups.text
            font.family: Style.font.family
            font.pixelSize: Style.font.title
            font.bold: true
          }
        }

        Row {
          visible: root.mode === "map"
          spacing: Style.space(32)

          Repeater {
            model: [root.map.left, root.map.right]

            Column {
              id: side
              required property var modelData
              spacing: Style.space(7)

              Repeater {
                model: side.modelData

                Row {
                  id: item
                  required property var modelData
                  spacing: Style.space(10)
                  opacity: item.modelData.inherited ? 0.55 : 1

                  ButtonCap {
                    button: item.modelData.button
                    size: root.capSize
                    lit: root.held.indexOf(item.modelData.button) >= 0
                    ink: Color.popups.text
                    anchors.verticalCenter: parent.verticalCenter
                  }
                  Column {
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                      text: item.modelData.label
                      color: Color.popups.text
                      font.family: Style.font.family
                      font.pixelSize: Style.font.body
                    }
                    Text {
                      visible: item.modelData.hold !== ""
                      text: item.modelData.hold === "hold" ? "hold" : "hold: " + item.modelData.hold
                      color: Util.alpha(Color.popups.text, 0.6)
                      font.family: Style.font.family
                      font.pixelSize: Style.font.caption
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
