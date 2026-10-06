// The popup (spec §8): the pad and its switches, search, the layer chips,
// every binding grouped by layer, and the editor.
import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui
import "../lib/bindings.js" as Bindings
import "../lib/editor.js" as Editor

ColumnLayout {
  id: root

  property QtObject bar: null
  property var service: null
  property bool opened: false

  readonly property color ink: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property var config: service ? service.config : null
  readonly property bool connected: service ? service.connected === true : false
  readonly property bool switchedOn: service ? service.switchedOn === true : true
  readonly property var held: service ? service.held : []
  readonly property string liveLayer: service ? service.activeLayer : "base"
  readonly property string liveLayerName: {
    var layers = config && config.layers ? config.layers : []
    for (var i = 0; i < layers.length; i++) if (layers[i].id === liveLayer) return layers[i].name
    return liveLayer
  }

  property string filter: "all"
  property bool editorOpen: false
  property string editingId: ""
  readonly property string query: search.text
  // Holding a layer key while the popup is open shows that layer: the
  // quickest way to learn it.
  readonly property string shownLayer: liveLayer !== "base" && query === "" && !editorOpen ? liveLayer : filter
  readonly property var groups: Bindings.groups(config, query, shownLayer)
  readonly property int total: Bindings.count(config)
  readonly property bool typing: search.activeFocus || (editorLoader.item && editorLoader.item.textFocused)

  signal closeRequested()

  spacing: Style.space(12)

  // A key typed anywhere in the popup starts a search.
  function focusSearch(text) {
    if (text) search.text += text
    search.forceActiveFocus()
  }

  function openEditor(draft, id) {
    editingId = id || ""
    editorOpen = true
    Qt.callLater(function() { if (editorLoader.item) editorLoader.item.load(draft) })
  }

  function closeEditor() {
    if (service) service.stopCapture()
    editorOpen = false
    editingId = ""
  }

  function remove(row) {
    if (service) service.edit(Editor.unbindArgs(row))
  }

  // The row whose action just ran flashes; the tick makes a repeat of the
  // same action flash again.
  property string flashId: ""
  property int flashTick: 0
  Connections {
    target: root.service
    function onFired(button, layer, slot, label) {
      root.flashId = layer + ":" + button + ":" + slot
      root.flashTick++
    }
  }

  // ---- the pad ------------------------------------------------------------

  // As the shipped panels open: a glyph, the name, a status line in caps,
  // and here the buttons held right now on the trailing edge.
  PanelHero {
    Layout.fillWidth: true
    foreground: root.ink
    fontFamily: root.fontFamily
    iconOpacity: root.connected && root.switchedOn ? 1 : 0.45
    iconComponent: Component {
      Text {
        text: String.fromCodePoint(root.switchedOn ? 0xF05BA : 0xF05BB) // md-microsoft_xbox_controller(_off)
        color: root.connected && root.switchedOn ? Color.accent : root.ink
        font.family: root.fontFamily
        font.pixelSize: Style.font.display
      }
    }
    title: root.connected ? (root.service.device || "Controller") : "No controller"
    meta: !root.service ? "Starting"
        : !root.connected ? "Pair or plug one in"
        : !root.switchedOn ? "Off"
        : root.total + " bindings" + (root.liveLayer !== "base" ? " · " + root.liveLayerName : "")
    trailingControl: Component {
      Row {
        spacing: Style.space(3)
        Repeater {
          model: root.held
          ButtonCap {
            required property string modelData
            button: modelData
            size: Style.space(20)
            lit: true
            ink: root.ink
            fontFamily: root.fontFamily
          }
        }
      }
    }
  }

  PanelSeparator {
    Layout.fillWidth: true
    foreground: root.ink
  }

  SettingRow {
    Layout.fillWidth: true
    icon: String.fromCodePoint(0xF02B4) // md-google_controller
    title: "Controller"
    description: "Off for games, then back. On the pad:"
    shortcut: root.config && root.config.toggleCombo ? root.config.toggleCombo : []
    checked: root.switchedOn
    ink: root.ink
    fontFamily: root.fontFamily
    onToggled: if (root.service) root.service.setEnabled(!root.switchedOn)
  }

  SettingRow {
    Layout.fillWidth: true
    icon: String.fromCodePoint(0xF0BB4) // md-pan: a thumbstick
    title: "Stick mouse" + (root.service && root.service.precise ? "  ·  precision" : "")
    description: "Left stick points, right stick scrolls. Hold"
    shortcut: ["GUIDE"]
    checked: root.service ? root.service.mouseOn : false
    ink: root.ink
    fontFamily: root.fontFamily
    onToggled: if (root.service) root.service.setMouseDefault(!root.service.mouseOn)
  }

  PanelSeparator {
    Layout.fillWidth: true
    foreground: root.ink
  }

  PanelSectionHeader {
    visible: !root.editorOpen
    text: "BINDINGS"
    foreground: root.ink
    fontFamily: root.fontFamily
  }

  // ---- search and layers --------------------------------------------------

  // While the form is open it is the whole popup below the pad.
  RowLayout {
    visible: !root.editorOpen
    Layout.fillWidth: true
    spacing: Style.space(8)

    TextField {
      id: search
      Layout.fillWidth: true
      foreground: root.ink
      font.family: root.fontFamily
      placeholderText: "Search " + root.total + " bindings"
      Keys.onEscapePressed: {
        if (text !== "") text = ""
        else root.closeRequested()
      }
    }
    PanelActionButton {
      iconText: String.fromCodePoint(0xF0415) // md-plus
      tooltipText: "Add a binding"
      foreground: root.ink
      onClicked: root.openEditor(Editor.blank(root.filter === "all" ? "base" : root.filter), "")
    }
  }

  Segmented {
    visible: !root.editorOpen
    Layout.fillWidth: true
    options: [{ value: "all", label: "All" }].concat(root.config ? root.config.layers.map(function(l) {
      return { value: l.id, label: l.name, key: l.key || "" }
    }) : [])
    value: root.shownLayer
    live: root.liveLayer
    ink: root.ink
    fontFamily: root.fontFamily
    onPicked: function(value) { root.filter = value }
  }

  // ---- bindings -----------------------------------------------------------

  Flickable {
    id: list
    visible: !root.editorOpen
    Layout.fillWidth: true
    Layout.preferredHeight: Math.min(listColumn.implicitHeight, Style.space(420))
    contentWidth: width
    contentHeight: listColumn.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height
    opacity: root.switchedOn ? 1 : 0.5

    ColumnLayout {
      id: listColumn
      width: list.width
      spacing: 0

      Text {
        visible: root.groups.length === 0
        Layout.fillWidth: true
        Layout.topMargin: Style.space(12)
        Layout.bottomMargin: Style.space(12)
        horizontalAlignment: Text.AlignHCenter
        text: root.config ? "Nothing matches “" + root.query + "”" : "Loading…"
        color: Qt.darker(root.ink, 1.5)
        font.family: root.fontFamily
        font.pixelSize: Style.font.body
      }

      Repeater {
        model: root.groups

        ColumnLayout {
          id: group
          required property var modelData
          Layout.fillWidth: true
          Layout.bottomMargin: Style.space(8)
          spacing: 0

          readonly property bool live: root.liveLayer === group.modelData.id

          RowLayout {
            Layout.fillWidth: true
            Layout.bottomMargin: Style.space(2)
            spacing: Style.space(8)
            PanelSectionHeader {
              Layout.fillWidth: true
              text: group.modelData.name.toUpperCase() + (group.modelData.key !== "" ? "  ·  HOLD " + group.modelData.key : "")
              foreground: group.live ? Color.accent : Qt.darker(root.ink, 1.3)
            }
            Text {
              text: group.modelData.rows.length
              color: Qt.darker(root.ink, 1.8)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Repeater {
            model: group.modelData.rows

            BindingRow {
              id: bindingRow
              required property var modelData
              Connections {
                target: root
                function onFlashTickChanged() { if (root.flashId === bindingRow.modelData.id) bindingRow.flash() }
              }
              Layout.fillWidth: true
              row: modelData
              ink: root.ink
              fontFamily: root.fontFamily
              selected: root.editingId === modelData.id
              // Lit while held, in the layer it would fire in now.
              lit: root.held.indexOf(modelData.button) >= 0
                   && Bindings.resolves(root.config, root.liveLayer, modelData.button) === modelData.layer
              onEditRequested: root.openEditor(Editor.fromRow(modelData), modelData.id)
              onDeleteRequested: root.remove(modelData)
            }
          }
        }
      }
    }
  }

  // ---- editor -------------------------------------------------------------

  Text {
    Layout.fillWidth: true
    visible: text !== ""
    text: root.service && root.service.lastError ? root.service.lastError : ""
    color: Color.urgent
    wrapMode: Text.WordWrap
    maximumLineCount: 2
    elide: Text.ElideRight
    font.family: root.fontFamily
    font.pixelSize: Style.font.caption
  }

  Loader {
    id: editorLoader
    Layout.fillWidth: true
    active: root.editorOpen
    // A Loader keeps its last size after unloading, which would leave the
    // closed form's height as empty space under the list.
    visible: active
    Layout.preferredHeight: active && item ? item.implicitHeight : 0
    sourceComponent: EditorCard {
      service: root.service
      ink: root.ink
      fontFamily: root.fontFamily
      onClosed: root.closeEditor()
    }
  }

  onOpenedChanged: {
    if (opened) {
      if (service) service.refresh()
      Qt.callLater(function() { root.focusSearch("") })
    } else {
      closeEditor()
      search.text = ""
    }
  }
}
