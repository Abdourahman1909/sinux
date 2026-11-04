// SPDX-License-Identifier: GPL-3.0-only
// Sinux Island: the assistant's presence at the top of the screen. A small pill
// that springs open to listen, think, speak and answer, then settles back.
import QtQuick
import QtQuick.Controls
import QtQuick.Window
import org.kde.layershell 1.0 as LayerShell

Window {
    id: island
    title: "Sinux Island"
    width: 820; height: 340
    visible: true
    color: "transparent"
    flags: Qt.FramelessWindowHint
    LayerShell.Window.scope: "sinux-island"
    LayerShell.Window.layer: LayerShell.Window.LayerTop
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop
    LayerShell.Window.exclusionZone: -1
    // While typing, take the keyboard outright (an app may have focus); otherwise only on click.
    LayerShell.Window.keyboardInteractivity: typing ? LayerShell.Window.KeyboardInteractivityExclusive
                                                    : LayerShell.Window.KeyboardInteractivityOnDemand

    // ------------------------------------------------------------ state (set by the service)
    property bool connected: false
    property bool busy: false
    property string status: ""
    property bool listening: false
    property string heard: ""
    property real level: 0
    property bool speaking: false
    property string caption: ""
    property bool captionActive: false     // narration in progress (also when the voice is muted)
    property string reply: ""
    property bool replyError: false
    property bool typing: false
    property bool reducedMotion: false
    property string talkKeyLabel: "Right Ctrl"
    property real lookX: 0      // where the creature looks (set when Sinux points or draws)
    property real lookY: 0
    property color accent: "#2F7BF6"

    readonly property color ink: "#F5F5F7"
    readonly property color dim: Qt.rgba(1, 1, 1, 0.58)
    readonly property color shell: "#0D0D0F"
    readonly property color danger: "#FF5B5F"

    property bool showReply: false
    readonly property string mode: listening ? "listening"
        : typing ? "typing"
        : (speaking || captionActive) && caption.length ? "speaking"
        : busy ? "thinking"
        : showReply && reply.length ? "answer"
        : "idle"

    onReplyChanged: {
        if (!reply.length) { showReply = false; return; }
        showReply = true;
        replyTimer.interval = Math.max(5000, Math.min(16000, reply.length * 65));
        replyTimer.restart();
    }
    Timer { id: replyTimer; onTriggered: if (!hover.hovered) island.showReply = false; else restart() }
    // Layer-shell applies keyboard interactivity when the surface is mapped: re-map briefly on
    // each typing change so the island takes the keyboard (and later gives it back).
    onTypingChanged: { island.visible = false; remap.restart(); }
    Timer {
        id: remap; interval: 40
        onTriggered: {
            island.visible = true;
            island.syncMask();
            if (island.typing) Qt.callLater(function() { island.requestActivate(); input.forceActiveFocus(); });
        }
    }

    // Live waveform history for listening.
    property var levels: []
    onLevelChanged: levels = levels.slice(-17).concat([level])
    onListeningChanged: if (listening) levels = []

    // ------------------------------------------------------------ geometry per mode
    TextMetrics { id: statusMetrics; text: island.status || "Thinking"; font.pixelSize: 15 }
    readonly property real targetWidth: ({
        idle: island.connected ? 128 : 178, listening: 480, typing: 540,
        thinking: Math.min(540, statusMetrics.advanceWidth + 96),
        speaking: 600, answer: 600 })[mode]
    readonly property real targetHeight: ({
        idle: 34, listening: 60, typing: 56, thinking: 44,
        speaking: Math.max(56, captionText.implicitHeight + 32),
        answer: Math.max(56, replyText.implicitHeight + 34) })[mode]

    // ------------------------------------------------------------ the pill
    Rectangle {
        id: pill
        anchors.horizontalCenter: parent.horizontalCenter
        y: 8
        width: island.targetWidth
        height: island.targetHeight
        radius: Math.min(height / 2, 26)
        color: island.shell
        border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.07)
        clip: true
        // Springs keep the morph alive and interruptible (state can change mid-flight).
        Behavior on width { enabled: !island.reducedMotion; SpringAnimation { spring: 3.4; damping: 0.34; epsilon: 0.3 } }
        Behavior on height { enabled: !island.reducedMotion; SpringAnimation { spring: 3.4; damping: 0.38; epsilon: 0.3 } }
        onXChanged: island.syncMask()
        onWidthChanged: island.syncMask()
        onHeightChanged: island.syncMask()

        HoverHandler { id: hover }
        TapHandler {
            id: openTap
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onTapped: function(point, button) {
                if (button === Qt.RightButton || !island.connected) sinux.OpenSettings();
                else if (island.mode === "idle" || island.mode === "answer") { sinux.ToggleBar(); }
            }
        }
        scale: openTap.pressed ? 0.97 : 1
        Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }

        // -------------------------------------------------------- orb (shared by all modes)
        Item {
            id: orbBox
            width: 26; height: 26
            x: island.mode === "idle" ? 10 : 15
            y: island.mode === "speaking" || island.mode === "answer" ? 15 : (pill.height - height) / 2
            Behavior on x { enabled: !island.reducedMotion; NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
            Creature {
                anchors.centerIn: parent
                size: island.mode === "idle" ? 22 : 26
                Behavior on size { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                mood: !island.connected ? "offline" : island.mode === "answer" && island.replyError ? "error" : island.mode
                level: island.level
                lookX: island.lookX; lookY: island.lookY
                reducedMotion: island.reducedMotion
            }
        }

        // -------------------------------------------------------- idle
        Text {
            opacity: island.mode === "idle" ? 1 : 0; visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
            anchors { left: orbBox.right; leftMargin: 8; verticalCenter: parent.verticalCenter }
            text: island.connected ? "Muse" : "Muse · connect"; color: island.dim
            font.pixelSize: 13; font.weight: Font.DemiBold; font.letterSpacing: 0.3
        }

        // -------------------------------------------------------- listening: live words + waveform
        Item {
            anchors.fill: parent
            opacity: island.mode === "listening" ? 1 : 0; visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
            Text {
                anchors { left: parent.left; leftMargin: 54; right: wave.left; rightMargin: 14; verticalCenter: parent.verticalCenter }
                text: island.heard.length ? island.heard : "Listening…"
                color: island.heard.length ? island.ink : island.dim
                font.pixelSize: 16; elide: Text.ElideLeft
            }
            Row {
                id: wave
                anchors { right: parent.right; rightMargin: 20; verticalCenter: parent.verticalCenter }
                spacing: 3
                Repeater {
                    model: 18
                    Rectangle {
                        required property int index
                        readonly property real v: island.levels.length > index ? island.levels[island.levels.length - 18 + index] || 0 : 0
                        width: 3; radius: 1.5
                        height: 4 + v * 26
                        anchors.verticalCenter: parent.verticalCenter
                        color: Qt.rgba(1, 1, 1, 0.35 + v * 0.65)
                        Behavior on height { NumberAnimation { duration: 80 } }
                    }
                }
            }
        }

        // -------------------------------------------------------- typing
        Item {
            anchors.fill: parent
            opacity: island.mode === "typing" ? 1 : 0; visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
            TextField {
                id: input
                anchors { left: parent.left; leftMargin: 50; right: parent.right; rightMargin: 18; verticalCenter: parent.verticalCenter }
                background: Item {}
                color: island.ink; font.pixelSize: 16
                placeholderText: "Ask Sinux…  (hold " + island.talkKeyLabel + " to talk)"; placeholderTextColor: island.dim
                selectionColor: island.accent; selectedTextColor: "white"
                Accessible.name: "Message Sinux"
                onAccepted: {
                    const text = input.text.trim();
                    if (text.length) { sinux.Send(text); input.clear(); }
                    island.typing = false;
                }
                Keys.onEscapePressed: { input.clear(); island.typing = false; }
                onActiveFocusChanged: if (!activeFocus && !input.text.length) island.typing = false
            }
        }

        // -------------------------------------------------------- working: a light moving along the bottom edge
        Item {
            id: activity
            anchors { left: parent.left; right: parent.right; bottom: parent.bottom
                      leftMargin: pill.radius; rightMargin: pill.radius; bottomMargin: 4 }
            height: 2; clip: true
            opacity: island.busy && island.mode !== "listening" && island.mode !== "typing" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
            Rectangle {
                id: sweep
                width: activity.width * 0.34; height: parent.height; radius: 1
                x: island.reducedMotion ? (activity.width - width) / 2 : -width
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "transparent" }
                    GradientStop { position: 0.5; color: island.accent }
                    GradientStop { position: 1.0; color: "transparent" }
                }
                NumberAnimation on x {
                    from: -sweep.width; to: activity.width; duration: 1250; loops: Animation.Infinite
                    easing.type: Easing.InOutSine; running: activity.visible && !island.reducedMotion
                }
            }
        }

        // -------------------------------------------------------- thinking / acting
        Text {
            opacity: island.mode === "thinking" ? 1 : 0; visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
            anchors { left: parent.left; leftMargin: 52; right: parent.right; rightMargin: 18; verticalCenter: parent.verticalCenter }
            text: island.status || "Thinking…"; color: island.dim; font.pixelSize: 15; elide: Text.ElideRight
        }

        // -------------------------------------------------------- speaking: caption in sync with the voice
        Item {
            anchors.fill: parent
            opacity: island.mode === "speaking" ? 1 : 0; visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
            Text {
                id: captionText
                anchors { left: parent.left; leftMargin: 54; right: speakWave.left; rightMargin: 14; top: parent.top; topMargin: 16 }
                text: island.caption; color: island.ink; font.pixelSize: 16; wrapMode: Text.Wrap
                lineHeight: 1.12; maximumLineCount: 4; elide: Text.ElideRight
            }
            Row {
                id: speakWave
                anchors { right: parent.right; rightMargin: 20; top: parent.top; topMargin: 20 }
                spacing: 3
                Repeater {
                    model: 5
                    Rectangle {
                        id: bar
                        required property int index
                        width: 3; radius: 1.5; height: 6
                        color: island.accent
                        SequentialAnimation on height {
                            running: island.speaking && !island.reducedMotion; loops: Animation.Infinite
                            NumberAnimation { to: 8 + (bar.index * 7 % 13); duration: 140 + bar.index * 37; easing.type: Easing.InOutSine }
                            NumberAnimation { to: 4 + (bar.index * 5 % 6); duration: 160 + bar.index * 29; easing.type: Easing.InOutSine }
                        }
                    }
                }
            }
        }

        // -------------------------------------------------------- answer
        Item {
            anchors.fill: parent
            opacity: island.mode === "answer" ? 1 : 0; visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
            Text {
                id: replyText
                anchors { left: parent.left; leftMargin: 54; right: parent.right; rightMargin: 22; top: parent.top; topMargin: 17 }
                text: island.reply; color: island.replyError ? "#FFB4B4" : island.ink
                font.pixelSize: 16; wrapMode: Text.Wrap; lineHeight: 1.12; maximumLineCount: 6; elide: Text.ElideRight
            }
        }
    }

    // Only the pill takes input; the rest of this surface lets clicks through.
    function syncMask() {
        if (typeof sinux === "undefined" || !sinux) return;
        sinux.SetIslandMask(Math.floor(pill.x) - 2, Math.floor(pill.y) - 2, Math.ceil(pill.width) + 4, Math.ceil(pill.height) + 4);
    }
    Component.onCompleted: Qt.callLater(syncMask)
}
