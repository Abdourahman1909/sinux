// SPDX-License-Identifier: GPL-3.0-only
// The top edge: reserves room for the island so maximised windows stay below it,
// and shows only the date and time. Never takes input.
import QtQuick
import QtQuick.Window
import org.kde.layershell 1.0 as LayerShell

Window {
    id: top
    title: "Sinux Top"
    visible: true
    color: "transparent"
    height: 46
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    LayerShell.Window.scope: "sinux-top"
    LayerShell.Window.layer: LayerShell.Window.LayerBottom
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: 46
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone

    property bool showClock: true
    property date now: new Date()
    Timer { interval: 1000; running: top.showClock; repeat: true; onTriggered: top.now = new Date() }

    Text {
        visible: top.showClock
        anchors { right: parent.right; rightMargin: 22; verticalCenter: parent.verticalCenter; verticalCenterOffset: -2 }
        text: Qt.formatDateTime(top.now, "ddd d MMM") + "   " + Qt.formatTime(top.now, "HH:mm")
        color: "white"
        font.family: "Inter"; font.pixelSize: 14; font.weight: Font.DemiBold; font.features: { "tnum": 1 }
        style: Text.Raised; styleColor: Qt.rgba(0, 0, 0, 0.28)
    }
}
