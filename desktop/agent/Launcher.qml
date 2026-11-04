// SPDX-License-Identifier: GPL-3.0-only
// Sinux Launcher: dim the desktop, type to find, Enter to open, Esc to leave.
import QtQuick
import QtQuick.Controls
import QtQuick.Window
import org.kde.kirigami as Kirigami
import org.kde.layershell 1.0 as LayerShell

Window {
    id: launcher
    title: "Sinux Launcher"
    visible: false
    color: "transparent"
    flags: Qt.FramelessWindowHint
    LayerShell.Window.scope: "sinux-launcher"
    LayerShell.Window.layer: LayerShell.Window.LayerTop
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityExclusive

    property string appsJson: "[]"
    readonly property var apps: { try { return JSON.parse(appsJson); } catch (e) { return []; } }
    readonly property var shown: {
        const q = search.text.trim().toLowerCase();
        return q ? apps.filter(a => (a.name + " " + (a.generic || "") + " " + (a.keywords || "")).toLowerCase().indexOf(q) >= 0) : apps;
    }
    property bool open: false
    property string wallpaper: ""
    property bool reducedMotion: false

    onOpenChanged: {
        if (open) { search.clear(); visible = true; Qt.callLater(function() { launcher.requestActivate(); search.forceActiveFocus(); }); }
        else hideTimer.restart();
    }
    Timer { id: hideTimer; interval: 140; onTriggered: if (!launcher.open) launcher.visible = false }
    function launch(app) { if (app) { sinux.LaunchApp(app.id); sinux.ToggleLauncher(); } }

    Item { // opaque frosted backdrop: nothing behind bleeds into the grid
        anchors.fill: parent
        opacity: launcher.open ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: launcher.open ? 200 : 120; easing.type: Easing.OutCubic } }
        Rectangle { anchors.fill: parent; color: "#1E2633" }
        Image {
            // A tiny copy of the wallpaper, smoothly scaled up, reads as a soft blur.
            anchors.fill: parent
            source: launcher.wallpaper ? "file://" + launcher.wallpaper : ""
            sourceSize: Qt.size(48, 30)
            fillMode: Image.PreserveAspectCrop
            smooth: true
        }
        Rectangle { anchors.fill: parent; color: Qt.rgba(0.06, 0.07, 0.09, 0.38) }
        TapHandler { onTapped: sinux.ToggleLauncher() }
    }

    Item {
        id: content
        anchors.fill: parent
        opacity: launcher.open ? 1 : 0
        scale: launcher.open || launcher.reducedMotion ? 1 : 0.98
        Behavior on opacity { NumberAnimation { duration: launcher.open ? 200 : 120; easing.type: Easing.OutCubic } }
        Behavior on scale { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }

        TextField {
            id: search
            anchors.horizontalCenter: parent.horizontalCenter
            y: parent.height * 0.13
            width: 440; height: 46
            leftPadding: 44; rightPadding: 16
            font.family: "Inter"; font.pixelSize: 16; color: "#1C1C1E"
            placeholderText: "Search apps"; placeholderTextColor: "#8E8E93"
            background: Rectangle { radius: 23; color: Qt.rgba(1, 1, 1, 0.94) }
            Kirigami.Icon { source: "search"; width: 18; height: 18; x: 16; anchors.verticalCenter: parent.verticalCenter; color: "#8E8E93"; isMask: true }
            Keys.onEscapePressed: sinux.ToggleLauncher()
            Keys.onReturnPressed: launcher.launch(grid.currentIndex >= 0 ? launcher.shown[grid.currentIndex] : launcher.shown[0])
            Keys.onDownPressed: grid.moveCurrentIndexDown()
            Keys.onUpPressed: grid.moveCurrentIndexUp()
            Keys.onLeftPressed: function(e) { if (search.cursorPosition === 0) grid.moveCurrentIndexLeft(); else e.accepted = false; }
            Keys.onRightPressed: function(e) { if (search.cursorPosition === search.text.length) grid.moveCurrentIndexRight(); else e.accepted = false; }
            onTextChanged: grid.currentIndex = 0
        }

        GridView {
            id: grid
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: search.bottom; anchors.topMargin: 44
            width: Math.min(parent.width - 160, cellWidth * 6)
            height: parent.height - y - 140
            cellWidth: 132; cellHeight: 124
            clip: true
            model: launcher.shown
            currentIndex: 0
            highlightFollowsCurrentItem: false
            delegate: Item {
                required property var modelData
                required property int index
                width: grid.cellWidth; height: grid.cellHeight
                readonly property bool lit: hover.hovered || (grid.currentIndex === index && search.text.length > 0)
                Rectangle {
                    anchors.centerIn: parent; anchors.verticalCenterOffset: -4
                    width: 112; height: 112; radius: 22
                    color: Qt.rgba(1, 1, 1, lit ? 0.16 : 0)
                    Behavior on color { ColorAnimation { duration: 120 } }
                }
                Kirigami.Icon {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 18; width: 60; height: 60
                    roundToIconSize: false
                    source: modelData.icon || "application-x-executable"
                    scale: tap.pressed ? 0.92 : 1
                    Behavior on scale { NumberAnimation { duration: 120 } }
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 86; width: parent.width - 12
                    horizontalAlignment: Text.AlignHCenter; elide: Text.ElideRight
                    text: modelData.name; color: "white"
                    font.family: "Inter"; font.pixelSize: 13; font.weight: Font.Medium
                    style: Text.Raised; styleColor: Qt.rgba(0, 0, 0, 0.35)
                }
                HoverHandler { id: hover }
                TapHandler { id: tap; onTapped: launcher.launch(modelData) }
            }
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter; anchors.top: search.bottom; anchors.topMargin: 60
            visible: launcher.shown.length === 0
            text: "No app called “" + search.text + "”. Try asking Sinux."
            color: "white"; font.family: "Inter"; font.pixelSize: 15
        }
    }
}
