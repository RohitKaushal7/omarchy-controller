// Controller: the bar icon and its popup (spec §8).
//
// Button and popup live together on Ui/Panel, as in the other dev.reuk
// widgets, so the bar's panel navigation works. Everything shown comes from
// the plugin's service (Service.qml), which owns the daemon: one per shell,
// however many bars show this widget.

import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "ui"

Panel {
  id: root
  moduleName: "dev.reuk.controller"
  // The service owns the dev.reuk.controller IPC target.
  manageIpc: false

  readonly property string pluginId: "dev.reuk.controller"

  readonly property var glyphs: ({
    pad: String.fromCodePoint(0xF02B4),     // md-google_controller
    padOff: String.fromCodePoint(0xF02B5)   // md-google_controller_off
  })

  // serviceFor() is a function, so no binding notices the service appear.
  property var service: null
  Timer {
    interval: 500
    repeat: true
    running: root.service === null
    triggeredOnStart: true
    onTriggered: {
      var shellApi = root.bar ? root.bar.shell : null
      root.service = shellApi && typeof shellApi.serviceFor === "function" ? shellApi.serviceFor(root.pluginId) : null
    }
  }

  readonly property bool connected: service ? service.connected === true : false
  readonly property bool live: connected && service.switchedOn === true
  property bool pulsing: false

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  Connections {
    target: root.service
    function onFired() {
      root.pulsing = true
      pulse.restart()
    }
  }
  Timer {
    id: pulse
    interval: 160
    onTriggered: root.pulsing = false
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.live ? root.glyphs.pad : root.glyphs.padOff
    opacity: root.connected ? 1 : 0.45
    // Accent while the sticks drive the mouse; a blink when an action runs.
    active: root.live && (root.service.mouseOn || root.pulsing)
    activeColor: root.pulsing ? Color.foreground : Color.accent
    tooltipText: !root.service ? "Controller: starting"
      : !root.connected ? "Controller: no pad connected"
      : root.service.device + (root.service.switchedOn ? "" : " (off)") + (root.service.mouseOn ? " · mouse on" : "")
    onPressed: function(buttonCode) {
      if (buttonCode === Qt.MiddleButton) {
        if (root.service) root.service.setEnabled(!root.service.switchedOn)
      } else if (buttonCode === Qt.RightButton) {
        if (root.service) root.service.toggleMouse()
      } else {
        root.toggle()
      }
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keys
    contentWidth: panel.fittedContentWidth(Style.space(460))
    contentHeight: panel.fittedContentHeight(content.implicitHeight, Style.space(820))

    PanelKeyCatcher {
      id: keys
      anchors.fill: parent
      // Typing goes to the search box and the editor's fields.
      blocked: content.typing
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) { content.focusSearch(text) }

      PopupBody {
        id: content
        width: parent.width
        bar: root.bar
        service: root.service
        opened: root.opened
        onCloseRequested: root.close()
      }
    }
  }
}
