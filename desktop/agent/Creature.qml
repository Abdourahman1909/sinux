// SPDX-License-Identifier: GPL-3.0-only
// Sinux's living character: a soft blue body with two eyes that blink, look at
// what Sinux is pointing at, widen while listening and bounce while speaking.
import QtQuick

Item {
    id: creature
    property real size: 26
    property string mood: "idle"     // idle listening typing thinking speaking answer error offline
    property real level: 0           // microphone level 0..1 while listening
    property real lookX: 0           // -1..1, where Sinux is looking (pointer target)
    property real lookY: 0
    property bool reducedMotion: false
    width: size; height: size

    readonly property bool warm: mood === "listening"
    readonly property color topColor: mood === "offline" ? "#A1A1A6" : warm ? "#FF8F86" : "#78ACFF"
    readonly property color bottomColor: mood === "offline" ? "#6E6E73" : warm ? "#E5484D" : "#2F7BF6"
    readonly property real eyeOpen: blinking ? 0.12
        : mood === "offline" ? 0.35 : mood === "error" ? 0.6
        : mood === "speaking" || mood === "answer" ? 0.62
        : mood === "listening" ? 1.22 : 1
    readonly property real gazeX: mood === "thinking" ? thinkGaze : mood === "typing" ? 0 : lookX
    readonly property real gazeY: mood === "thinking" ? -0.7 : mood === "typing" ? 0.7 : lookY

    property bool blinking: false
    property real thinkGaze: -0.8
    property real bounce: 0
    property real breath: 1

    // ---------------------------------------------------------------- body
    Item {
        id: body
        anchors.centerIn: parent
        width: creature.size; height: creature.size
        y: -creature.bounce * creature.size * 0.08
        rotation: creature.mood === "error" ? -10 : creature.mood === "thinking" ? wobble.angle : 0
        Behavior on rotation { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        scale: creature.breath * (creature.mood === "listening" ? 1 + creature.level * 0.2 : 1)
        Behavior on scale { NumberAnimation { duration: 90 } }

        Rectangle { // soft glow
            anchors.centerIn: parent
            width: parent.width * 1.5; height: width; radius: width / 2
            color: creature.bottomColor; opacity: creature.mood === "idle" || creature.mood === "offline" ? 0 : 0.2
            Behavior on opacity { NumberAnimation { duration: 220 } }
            Behavior on color { ColorAnimation { duration: 220 } }
        }
        Rectangle {
            anchors.fill: parent
            radius: width * 0.44
            gradient: Gradient {
                GradientStop { position: 0; color: creature.topColor; Behavior on color { ColorAnimation { duration: 220 } } }
                GradientStop { position: 1; color: creature.bottomColor; Behavior on color { ColorAnimation { duration: 220 } } }
            }
            border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.25)
        }
        Rectangle { // highlight
            x: parent.width * 0.2; y: parent.height * 0.1
            width: parent.width * 0.36; height: parent.height * 0.16; radius: height / 2
            color: Qt.rgba(1, 1, 1, 0.28)
        }
        Row {
            anchors.centerIn: parent
            anchors.horizontalCenterOffset: creature.gazeX * creature.size * 0.09
            anchors.verticalCenterOffset: creature.gazeY * creature.size * 0.08 - creature.size * 0.02
            Behavior on anchors.horizontalCenterOffset { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
            Behavior on anchors.verticalCenterOffset { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
            spacing: creature.size * 0.14
            Repeater {
                model: 2
                Rectangle {
                    width: creature.size * 0.15
                    height: Math.max(1.5, creature.size * 0.3 * creature.eyeOpen)
                    radius: width / 2
                    anchors.verticalCenter: parent.verticalCenter
                    color: "white"
                    Behavior on height { NumberAnimation { duration: creature.blinking ? 60 : 160; easing.type: Easing.OutCubic } }
                }
            }
        }
    }

    // ---------------------------------------------------------------- life
    Timer { // blink every few seconds
        interval: 2600 + Math.random() * 3400; running: !creature.reducedMotion && creature.mood !== "offline"; repeat: true
        onTriggered: { creature.blinking = true; unblink.restart(); interval = 2600 + Math.random() * 3400; }
    }
    Timer { id: unblink; interval: 130; onTriggered: creature.blinking = false }
    SequentialAnimation on breath { // idle breathing
        running: !creature.reducedMotion && (creature.mood === "idle" || creature.mood === "typing"); loops: Animation.Infinite
        onRunningChanged: if (!running) creature.breath = 1
        NumberAnimation { to: 1.04; duration: 1600; easing.type: Easing.InOutSine }
        NumberAnimation { to: 1.0; duration: 1600; easing.type: Easing.InOutSine }
    }
    SequentialAnimation on thinkGaze { // eyes dart while thinking
        running: !creature.reducedMotion && creature.mood === "thinking"; loops: Animation.Infinite
        NumberAnimation { to: 0.8; duration: 420; easing.type: Easing.InOutQuad }
        PauseAnimation { duration: 380 }
        NumberAnimation { to: -0.8; duration: 420; easing.type: Easing.InOutQuad }
        PauseAnimation { duration: 380 }
    }
    Item {
        id: wobble
        visible: false
        property real angle: 0
        SequentialAnimation on angle {
            running: !creature.reducedMotion && creature.mood === "thinking"; loops: Animation.Infinite
            NumberAnimation { to: 5; duration: 500; easing.type: Easing.InOutSine }
            NumberAnimation { to: -5; duration: 500; easing.type: Easing.InOutSine }
        }
    }
    SequentialAnimation on bounce { // gentle bounce while speaking
        running: !creature.reducedMotion && creature.mood === "speaking"; loops: Animation.Infinite
        onRunningChanged: if (!running) creature.bounce = 0
        NumberAnimation { to: 1; duration: 190; easing.type: Easing.OutQuad }
        NumberAnimation { to: 0; duration: 210; easing.type: Easing.InQuad }
        PauseAnimation { duration: 90 }
    }
}
