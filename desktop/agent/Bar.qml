// SPDX-License-Identifier: GPL-3.0-only
// Sinux command bar: a slim pill at the bottom of the screen. Answers appear on
// the overlay above it; earlier conversation expands upward on request.
import QtQuick
import QtQuick.Controls
import QtQuick.Window
import QtQuick.Shapes
import org.kde.layershell 1.0 as LayerShell

Window {
    id: bar
    title: "Sinux Bar"
    visible: false
    color: "transparent"
    flags: Qt.FramelessWindowHint
    // Sits above applications and the rail, anchored to the bottom edge.
    LayerShell.Window.scope: "sinux-bar"
    LayerShell.Window.layer: LayerShell.Window.LayerTop
    LayerShell.Window.anchors: LayerShell.Window.AnchorBottom
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityOnDemand
    width: 640
    height: pill.height + (expanded ? history.height + 10 : 0) + 22

    property color surfaceColor: "#F6F2E9"
    property color raisedColor: "#ECE6DA"
    property color inkColor: "#1D2327"
    property color mutedColor: "#6B7378"
    property color pointerColor: "#2F7BF6"
    property string stateJson: "{}"
    readonly property var state: { try { return JSON.parse(stateJson); } catch (e) { return ({}); } }
    readonly property var entries: state.entries || []
    readonly property bool busy: !!state.busy
    readonly property bool connected: !!state.connected
    property bool expanded: false
    property bool canListen: false
    property bool canSpeak: false
    property bool listening: false
    property string heard: ""
    property bool speakOn: true
    property int recall: -1
    readonly property var sent: entries.filter(e => e.kind === "user").map(e => e.text)

    function focusInput() { input.forceActiveFocus(); }
    function submit() {
        const text = input.text.trim();
        if (!text || busy) return;
        sinux.Send(text); input.clear(); recall = -1;
    }
    onVisibleChanged: if (visible) Qt.callLater(focusInput)
    Shortcut { sequences: ["Ctrl+Space"]; enabled: bar.canListen; onActivated: sinux.ToggleListen() }
    onListeningChanged: Qt.callLater(function() { if (bar.listening) keyCatcher.forceActiveFocus(); else bar.focusInput(); })
    Item { // keys while listening: Enter sends, Esc cancels
        id: keyCatcher
        Keys.onReturnPressed: sinux.ToggleListen()
        Keys.onEnterPressed: sinux.ToggleListen()
        Keys.onEscapePressed: sinux.CancelListen()
    }
    onActiveChanged: if (active) Qt.callLater(focusInput)

    // ---------------------------------------------------------------- history
    Rectangle {
        id: history
        visible: bar.expanded
        anchors { left: parent.left; right: parent.right; bottom: pill.top; bottomMargin: 10; leftMargin: 8; rightMargin: 8 }
        height: Math.min(380, Math.max(100, list.contentHeight + 52))
        radius: 22
        color: bar.surfaceColor
        border.width: 1; border.color: Qt.rgba(0, 0, 0, 0.08)
        ListView {
            id: list
            anchors { fill: parent; margins: 14; bottomMargin: 34 }
            clip: true; spacing: 8
            model: bar.entries
            onCountChanged: Qt.callLater(function() { list.positionViewAtEnd(); })
            Component.onCompleted: positionViewAtEnd()
            ScrollBar.vertical: ScrollBar {}
            delegate: Item {
                required property var modelData
                readonly property bool mine: modelData.kind === "user"
                readonly property bool step: modelData.kind === "tool" || modelData.kind === "notice"
                width: list.width
                height: step ? stepText.implicitHeight + 2 : bubble.height
                Text {
                    id: stepText
                    visible: step; leftPadding: 4
                    width: parent.width
                    text: (modelData.kind === "notice" ? "■  " : modelData.ok ? "✓  " : "✕  ") + modelData.text
                    color: modelData.ok ? bar.mutedColor : "#B5463F"; font.pixelSize: 13; elide: Text.ElideRight
                    textFormat: Text.PlainText
                }
                Rectangle {
                    id: bubble
                    visible: !step
                    anchors.right: mine ? parent.right : undefined
                    anchors.left: mine ? undefined : parent.left
                    width: Math.min(parent.width * 0.86, metrics.advanceWidth + 32)
                    height: message.implicitHeight + 16
                    radius: 14
                    color: mine ? bar.pointerColor : modelData.kind === "error" ? Qt.rgba(0.75, 0.3, 0.28, 0.14) : bar.raisedColor
                    TextMetrics { id: metrics; text: modelData.text; font.pixelSize: 15 }
                    Text {
                        id: message
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8; leftMargin: 13; rightMargin: 13 }
                        text: modelData.text; wrapMode: Text.Wrap; textFormat: Text.PlainText
                        color: mine ? "white" : bar.inkColor; font.pixelSize: 15
                        opacity: modelData.interim ? 0.75 : 1
                    }
                }
            }
        }
        Text { // real usage reported by the Meta API
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: 14; bottomMargin: 10 }
            color: bar.mutedColor; font.pixelSize: 12; elide: Text.ElideRight
            readonly property var last: bar.state.last || ({})
            text: "Muse Spark · " + (bar.state.requests || 0) + " requests · " + ((bar.state.tokens || 0) / 1000).toFixed(1)
                  + "k tokens · " + (bar.state.images || 0) + " screenshots"
                  + (last.prompt ? "   ·   last: " + last.prompt + " in / " + last.completion + " out" + (last.image ? " (with screen)" : "") : "")
        }
        Text {
            anchors.centerIn: parent; visible: list.count === 0
            text: "Nothing yet. Ask me something."; color: bar.mutedColor; font.pixelSize: 14
        }
    }

    // ---------------------------------------------------------------- pill
    Rectangle {
        id: pillShadow
        anchors.fill: pill; anchors.topMargin: 4; anchors.bottomMargin: -5; anchors.leftMargin: 2; anchors.rightMargin: 2
        radius: pill.radius; color: Qt.rgba(0, 0, 0, 0.14)
    }
    Rectangle {
        id: pill
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 8; rightMargin: 8; bottomMargin: 14 }
        height: 58
        radius: 29
        color: bar.surfaceColor
        border.width: input.activeFocus ? 2 : 1
        border.color: input.activeFocus ? Qt.alpha(bar.pointerColor, 0.55) : Qt.rgba(0, 0, 0, 0.08)

        Rectangle {
            id: orb
            anchors { left: parent.left; leftMargin: 18; verticalCenter: parent.verticalCenter }
            width: 22; height: 22; radius: 11
            color: bar.listening ? "#E5484D" : bar.connected ? bar.pointerColor : bar.mutedColor
            SequentialAnimation on scale {
                running: bar.busy || bar.listening; loops: Animation.Infinite
                onRunningChanged: if (!running) orb.scale = 1
                NumberAnimation { to: 0.72; duration: 480; easing.type: Easing.InOutSine }
                NumberAnimation { to: 1.0; duration: 480; easing.type: Easing.InOutSine }
            }
            Rectangle { anchors.centerIn: parent; width: 8; height: 8; radius: 4; color: "white"; opacity: 0.9 }
        }
        Text { // live transcript while listening
            visible: bar.listening
            anchors { left: orb.right; leftMargin: 14; right: speakButton.left; rightMargin: 6; verticalCenter: parent.verticalCenter }
            text: bar.heard.length ? bar.heard : "Listening… tap the mic or press Enter to send"
            color: bar.heard.length ? bar.inkColor : bar.mutedColor; font.pixelSize: 17
            font.italic: !bar.heard.length; elide: Text.ElideLeft
        }
        TextField {
            id: input
            visible: !bar.listening
            anchors { left: orb.right; leftMargin: 12; right: speakButton.left; rightMargin: 6; verticalCenter: parent.verticalCenter }
            height: 44
            background: Item {}
            color: bar.inkColor; font.pixelSize: 17
            placeholderText: !bar.connected ? "AI isn't connected" : bar.busy ? (bar.state.status || "Working…") : "Ask Sinux, or tell it what to do"
            placeholderTextColor: bar.mutedColor
            selectionColor: bar.pointerColor; selectedTextColor: "white"
            readOnly: bar.busy
            enabled: bar.connected
            Accessible.name: "Message Sinux"
            onAccepted: bar.submit()
            Keys.onEscapePressed: sinux.HideBar()
            Keys.onUpPressed: { if (bar.sent.length) { bar.recall = bar.recall < 0 ? bar.sent.length - 1 : Math.max(0, bar.recall - 1); input.text = bar.sent[bar.recall]; } }
            Keys.onDownPressed: { if (bar.recall >= 0) { bar.recall++; if (bar.recall >= bar.sent.length) { bar.recall = -1; input.clear(); } else input.text = bar.sent[bar.recall]; } }
        }
        AbstractButton { // voice output on/off
            id: speakButton
            anchors { right: historyButton.left; rightMargin: 2; verticalCenter: parent.verticalCenter }
            width: bar.canSpeak ? 34 : 0; height: 34; visible: bar.canSpeak
            Accessible.name: bar.speakOn ? "Mute Sinux's voice" : "Let Sinux speak"
            onClicked: { bar.speakOn = !bar.speakOn; sinux.SetSpeak(bar.speakOn); }
            background: Rectangle { radius: 17; color: speakButton.hovered ? bar.raisedColor : "transparent" }
            contentItem: Item {
                Shape {
                    anchors.centerIn: parent; width: 20; height: 16
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath { fillColor: bar.mutedColor; strokeColor: "transparent"
                        startX: 1; startY: 5; PathLine { x: 5; y: 5 } PathLine { x: 10; y: 1 } PathLine { x: 10; y: 15 }
                        PathLine { x: 5; y: 11 } PathLine { x: 1; y: 11 } PathLine { x: 1; y: 5 } }
                    ShapePath { fillColor: "transparent"; strokeColor: bar.speakOn ? bar.mutedColor : "transparent"; strokeWidth: 1.8; capStyle: ShapePath.RoundCap
                        startX: 13; startY: 4.5; PathQuad { x: 13; y: 11.5; controlX: 16.5; controlY: 8 } }
                    ShapePath { fillColor: "transparent"; strokeColor: bar.speakOn ? bar.mutedColor : "transparent"; strokeWidth: 1.8; capStyle: ShapePath.RoundCap
                        startX: 15.5; startY: 2; PathQuad { x: 15.5; y: 14; controlX: 21; controlY: 8 } }
                    ShapePath { fillColor: "transparent"; strokeColor: bar.speakOn ? "transparent" : "#E5484D"; strokeWidth: 2; capStyle: ShapePath.RoundCap
                        startX: 13; startY: 4; PathLine { x: 19; y: 12 } }
                }
            }
        }
        AbstractButton {
            id: historyButton
            anchors { right: micButton.left; rightMargin: 2; verticalCenter: parent.verticalCenter }
            width: bar.entries.length > 0 ? 36 : 0; height: 36
            visible: bar.entries.length > 0
            Accessible.name: bar.expanded ? "Hide conversation" : "Show conversation"
            onClicked: { bar.expanded = !bar.expanded; sinux.SetExpanded(bar.expanded); bar.focusInput(); }
            contentItem: Text { text: bar.expanded ? "▾" : "▴"; color: bar.mutedColor; font.pixelSize: 18
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 18; color: historyButton.hovered ? bar.raisedColor : "transparent" }
        }
        AbstractButton { // push to talk (Meta Muse Voice Transcribe)
            id: micButton
            anchors { right: sendButton.left; rightMargin: 6; verticalCenter: parent.verticalCenter }
            width: bar.canListen ? 40 : 0; height: 40; visible: bar.canListen
            enabled: bar.connected && !bar.busy
            Accessible.name: bar.listening ? "Stop listening and send" : "Talk to Sinux"
            onClicked: sinux.ToggleListen()
            background: Rectangle {
                radius: 20
                color: bar.listening ? "#E5484D" : micButton.hovered ? bar.raisedColor : "transparent"
                border.width: bar.listening ? 0 : 1.5; border.color: Qt.alpha(bar.mutedColor, 0.5)
                Rectangle { // listening ripple
                    anchors.centerIn: parent; width: parent.width; height: width; radius: width / 2
                    color: "transparent"; border.width: 2; border.color: "#E5484D"; visible: bar.listening
                    SequentialAnimation on scale { running: bar.listening; loops: Animation.Infinite
                        NumberAnimation { from: 1; to: 1.45; duration: 900; easing.type: Easing.OutQuad } }
                    SequentialAnimation on opacity { running: bar.listening; loops: Animation.Infinite
                        NumberAnimation { from: 0.8; to: 0; duration: 900 } }
                }
            }
            contentItem: Item {
                Rectangle { // mic capsule
                    anchors.horizontalCenter: parent.horizontalCenter; y: 9
                    width: 9; height: 14; radius: 4.5
                    color: bar.listening ? "white" : bar.inkColor
                }
                Shape {
                    anchors.horizontalCenter: parent.horizontalCenter; y: 16; width: 16; height: 14
                    preferredRendererType: Shape.CurveRenderer
                    ShapePath { fillColor: "transparent"; strokeColor: bar.listening ? "white" : bar.inkColor; strokeWidth: 1.8; capStyle: ShapePath.RoundCap
                        startX: 1; startY: 1; PathQuad { x: 15; y: 1; controlX: 8; controlY: 14 } }
                    ShapePath { fillColor: "transparent"; strokeColor: bar.listening ? "white" : bar.inkColor; strokeWidth: 1.8; capStyle: ShapePath.RoundCap
                        startX: 8; startY: 7.5; PathLine { x: 8; y: 12 } }
                }
            }
        }
        AbstractButton {
            id: sendButton
            anchors { right: parent.right; rightMargin: 9; verticalCenter: parent.verticalCenter }
            width: 40; height: 40
            enabled: bar.connected && (bar.busy || bar.listening || input.text.trim().length > 0)
            Accessible.name: bar.busy ? "Stop" : "Send"
            onClicked: bar.listening ? sinux.ToggleListen() : bar.busy ? sinux.Cancel() : bar.submit()
            contentItem: Text { text: bar.busy ? "■" : "↑"; color: "white"; font.pixelSize: bar.busy ? 14 : 19; font.weight: Font.DemiBold
                horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { radius: 20; color: sendButton.enabled ? bar.pointerColor : Qt.alpha(bar.mutedColor, 0.35) }
        }
    }
}
