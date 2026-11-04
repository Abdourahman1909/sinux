// SPDX-License-Identifier: GPL-3.0-only
// Sinux Dock: one floating row — apps button, pinned apps, running apps.
import QtQuick
import QtQuick.Window
import org.kde.kirigami as Kirigami
import org.kde.layershell 1.0 as LayerShell

Window {
    id: dock
    title: "Sinux Dock"
    visible: true
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
    width: Math.max(200, plate.width + 40)
    height: 128
    LayerShell.Window.scope: "sinux-dock"
    LayerShell.Window.layer: LayerShell.Window.LayerTop
    LayerShell.Window.anchors: LayerShell.Window.AnchorBottom
    LayerShell.Window.exclusionZone: 80
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone

    property string dockJson: "{}"
    readonly property var items: { try { return JSON.parse(dockJson).items || []; } catch (e) { return []; } }
    property bool launcherOpen: false
    property bool reducedMotion: false
    readonly property int iconSize: 48
    readonly property int cell: 62
    property string hoveredName: ""
    property real hoveredX: 0

    readonly property color plateColor: Qt.rgba(0.975, 0.968, 0.955, 0.93)
    readonly property color ink: "#1C1C1E"
    readonly property color accent: "#2F7BF6"

    function syncMask() {
        if (typeof sinux === "undefined" || !sinux) return;
        sinux.SetSurfaceMask("dock", Math.floor(plate.x), Math.floor(plate.y), Math.ceil(plate.width), Math.ceil(plate.height));
    }

    // Name bubble above the hovered icon.
    Rectangle {
        id: tip
        opacity: dock.hoveredName.length ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        x: Math.max(4, Math.min(dock.width - width - 4, dock.hoveredX - width / 2))
        y: plate.y - height - 10
        width: tipText.implicitWidth + 22; height: 28; radius: 14
        color: "#0D0D0F"
        Text { id: tipText; anchors.centerIn: parent; text: dock.hoveredName; color: "#F5F5F7"
            font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium }
    }

    // Soft shadow, then the plate.
    Rectangle {
        x: plate.x + 2; y: plate.y + 8; width: plate.width - 4; height: plate.height
        radius: plate.radius; color: Qt.rgba(0, 0, 0, 0.13)
    }
    Rectangle {
        id: plate
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height - height - 12
        height: 74
        width: row.implicitWidth + 20
        radius: 24
        color: dock.plateColor
        border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.7)
        Behavior on width { enabled: !dock.reducedMotion; SpringAnimation { spring: 4; damping: 0.42; epsilon: 0.3 } }
        onWidthChanged: Qt.callLater(dock.syncMask)
        onXChanged: Qt.callLater(dock.syncMask)

        Row {
            id: row
            anchors.centerIn: parent
            spacing: 2

            // Apps button (launcher)
            DockButton {
                name: "Apps"
                onActivated: sinux.ToggleLauncher()
                Grid {
                    anchors.centerIn: parent
                    columns: 3; spacing: 4
                    Repeater { model: 9
                        Rectangle { width: 7; height: 7; radius: 2.5; color: dock.launcherOpen ? dock.accent : Qt.rgba(0.11, 0.11, 0.12, 0.78) } }
                }
            }
            Rectangle { width: 1; height: 34; anchors.verticalCenter: parent.verticalCenter; color: Qt.rgba(0, 0, 0, 0.1) }
            Item { width: 4; height: 1 }

            Repeater {
                model: dock.items
                delegate: DockButton {
                    required property var modelData
                    name: modelData.name
                    running: modelData.running
                    active: modelData.active
                    onActivated: sinux.DockActivate(modelData.id)
                    Kirigami.Icon {
                        anchors.centerIn: parent
                        width: dock.iconSize; height: dock.iconSize
                        roundToIconSize: false
                        source: modelData.icon || "application-x-executable"
                    }
                }
            }
        }
    }

    component DockButton: Item {
        id: button
        property string name
        property bool running: false
        property bool active: false
        default property alias content: face.data
        signal activated()
        width: dock.cell; height: dock.cell
        anchors.verticalCenter: parent ? parent.verticalCenter : undefined
        Item {
            id: face
            anchors.fill: parent
            // Lift on hover, press-in on click: feedback without fuss.
            y: hover.hovered ? -4 : 0
            scale: tap.pressed ? 0.92 : hover.hovered ? 1.06 : 1
            Behavior on y { enabled: !dock.reducedMotion; SpringAnimation { spring: 5; damping: 0.5; epsilon: 0.2 } }
            Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
        }
        Rectangle { // running indicator
            anchors.horizontalCenter: parent.horizontalCenter
            y: parent.height - 3
            width: button.active ? 14 : 4; height: 4; radius: 2
            color: button.active ? dock.accent : Qt.rgba(0.11, 0.11, 0.12, 0.45)
            opacity: button.running ? 1 : 0
            Behavior on width { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
            Behavior on opacity { NumberAnimation { duration: 160 } }
        }
        HoverHandler {
            id: hover
            onHoveredChanged: {
                if (hovered) { dock.hoveredName = button.name; dock.hoveredX = button.mapToItem(null, button.width / 2, 0).x; }
                else if (dock.hoveredName === button.name) dock.hoveredName = "";
            }
        }
        TapHandler { id: tap; onTapped: button.activated() }
    }

    Component.onCompleted: Qt.callLater(syncMask)
}
