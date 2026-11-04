// SPDX-License-Identifier: GPL-3.0-only
// Sinux companion overlay: a pointer that flies to what the assistant is talking
// about, plus a reply bubble. Full-screen, transparent and input-transparent.
import QtQuick
import QtQuick.Shapes
import QtQuick.Window
import org.kde.layershell 1.0 as LayerShell
import "Ink.mjs" as Ink

Window {
    id: root
    title: "Sinux Pointer"
    width: 1440; height: 900
    x: 0; y: 0
    visible: true
    color: "transparent"
    flags: Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
    // Top-most compositor layer: above apps, panels and the rail, never takes input.
    LayerShell.Window.scope: "sinux-pointer"
    LayerShell.Window.layer: LayerShell.Window.LayerOverlay
    LayerShell.Window.anchors: LayerShell.Window.AnchorTop | LayerShell.Window.AnchorBottom | LayerShell.Window.AnchorLeft | LayerShell.Window.AnchorRight
    LayerShell.Window.exclusionZone: -1
    LayerShell.Window.keyboardInteractivity: LayerShell.Window.KeyboardInteractivityNone

    property color surfaceColor: "#F6F2E9"
    property color inkColor: "#1D2327"
    property color accentColor: "#3E7CB1"
    property color mutedColor: "#6B7378"
    property color pointerColor: "#2F7BF6"
    property string statusText: ""
    property string replyText: ""
    property bool replyError: false
    property real barTop: height          // top edge of the Sinux bar when it is visible
    property bool suppressBubble: false   // the bar is showing the full conversation

    // ---------------------------------------------------------------- pointer
    property real fromX: width / 2
    property real fromY: height - 120
    property real toX: width / 2
    property real toY: height / 2
    property real t: 1
    property string pointerLabel: ""
    property bool clicking: false
    readonly property real ctrlX: (fromX + toX) / 2 + (toY - fromY) * 0.18
    readonly property real ctrlY: Math.min(fromY, toY) - Math.abs(toX - fromX) * 0.22 - 40
    readonly property real px: (1-t)*(1-t)*fromX + 2*(1-t)*t*ctrlX + t*t*toX
    readonly property real py: (1-t)*(1-t)*fromY + 2*(1-t)*t*ctrlY + t*t*toY

    function pointTo(x, y, label, click) {
        fromX = pointer.visible ? px : width / 2;
        fromY = pointer.visible ? py : height - 140;
        toX = x; toY = y; pointerLabel = label; clicking = click;
        pointer.visible = true; pointer.opacity = 1;
        flight.restart(); hideTimer.restart();
    }
    function say(text, isError) {
        replyText = text; replyError = isError;
        replyTimer.interval = Math.max(7000, Math.min(30000, text.length * 70));
        if (text.length) replyTimer.restart();
    }

    NumberAnimation {
        id: flight; target: root; property: "t"; from: 0; to: 1
        duration: 700; easing.type: Easing.OutCubic
        onFinished: { ripple.restart(); }
    }
    Timer { id: hideTimer; interval: 9000; onTriggered: fade.restart() }
    NumberAnimation { id: fade; target: pointer; property: "opacity"; to: 0; duration: 500
        onFinished: pointer.visible = false }

    Item {
        id: pointer
        visible: false
        x: root.px; y: root.py
        // Soft halo
        Rectangle { x: -26; y: -26; width: 52; height: 52; radius: 26; color: root.pointerColor; opacity: 0.16 }
        Rectangle { x: -15; y: -15; width: 30; height: 30; radius: 15; color: root.pointerColor; opacity: 0.22 }
        // Arrival ripple
        Rectangle {
            id: ring
            property real s: 0
            x: -s/2; y: -s/2; width: s; height: s; radius: s/2
            color: "transparent"; border.width: 3; border.color: root.pointerColor
            opacity: 1 - s / 90
            SequentialAnimation on s { id: ripple; running: false; loops: 2
                NumberAnimation { from: 10; to: 90; duration: 650; easing.type: Easing.OutQuad } }
        }
        // Arrow (tip at 0,0)
        Shape {
            x: 0; y: 0; width: 30; height: 34
            preferredRendererType: Shape.CurveRenderer
            ShapePath {
                fillColor: root.pointerColor; strokeColor: "white"; strokeWidth: 2.5; joinStyle: ShapePath.RoundJoin
                startX: 0; startY: 0
                PathLine { x: 24; y: 17 }
                PathLine { x: 13; y: 19 }
                PathLine { x: 7; y: 31 }
                PathLine { x: 0; y: 0 }
            }
        }
        Rectangle {
            visible: root.pointerLabel.length > 0
            x: (root.px > root.width - 300) ? -width - 12 : 30
            y: 26
            radius: height / 2
            color: root.pointerColor
            width: labelText.implicitWidth + 26; height: labelText.implicitHeight + 12
            Text { id: labelText; anchors.centerIn: parent; text: root.pointerLabel; color: "white"
                   font.pixelSize: 15; font.weight: Font.DemiBold }
        }
    }

    // ---------------------------------------------------------------- ink marks
    property var marks: []
    property int markSerial: 0
    function addMark(raw) {
        const m = JSON.parse(raw);
        m.key = ++markSerial; m.born = Date.now();
        marks = marks.concat([m]);
        markTimer.restart();
    }
    function clearMarks() { marks = []; }
    // ---- label layout: every label tries several spots around its mark and takes the
    // first that collides with neither another label nor any mark outline.
    FontMetrics { id: labelMetrics; font.pixelSize: 15; font.weight: Font.DemiBold }
    readonly property var labelLayout: layoutLabels(marks)
    function bounds(m) {
        const L = Math.min(m.x1, m.x2), R = Math.max(m.x1, m.x2), T = Math.min(m.y1, m.y2), B = Math.max(m.y1, m.y2);
        if (m.shape === "circle") { const w = (R - L) / 2 + 16, h = (B - T) / 2 + 14, cx = (L + R) / 2, cy = (T + B) / 2;
            return {l: cx - w * 1.06, t: cy - h * 1.06, r: cx + w * 1.06, b: cy + h * 1.06}; }
        if (m.shape === "box") return {l: L - 10, t: T - 11, r: R + 12, b: B + 11};
        if (m.shape === "underline") return {l: L - 4, t: B + 2, r: R + 4, b: B + 12};
        if (m.shape === "highlight") return {l: L - 4, t: T - 3, r: R + 4, b: B + 3};
        return {l: L, t: T, r: R, b: B};
    }
    function overlap(a, b) {
        const w = Math.min(a.r, b.r) - Math.max(a.l, b.l), h = Math.min(a.b, b.b) - Math.max(a.t, b.t);
        return w > 0 && h > 0 ? w * h : 0;
    }
    function layoutLabels(list) {
        const placed = [], obstacles = [], out = [];
        const edge = 9;
        for (const m of list) {
            if (m.shape === "arrow") {
                // Sample the shaft as small boxes.
                for (let i = 0; i <= 10; i++) { const x = m.x1 + (m.x2 - m.x1) * i / 10, y = m.y1 + (m.y2 - m.y1) * i / 10;
                    obstacles.push({l: x - 8, t: y - 8, r: x + 8, b: y + 8}); }
            } else {
                const k = bounds(m);
                obstacles.push({l: k.l - edge, t: k.t - edge, r: k.r + edge, b: k.t + edge}, {l: k.l - edge, t: k.b - edge, r: k.r + edge, b: k.b + edge},
                               {l: k.l - edge, t: k.t, r: k.l + edge, b: k.b}, {l: k.r - edge, t: k.t, r: k.r + edge, b: k.b});
            }
        }
        const limitB = Math.min(root.height, root.barTop) - 12;
        for (const m of list) {
            if (!m.label) continue;
            const w = Math.ceil(labelMetrics.advanceWidth(m.label)) + 26, h = 30, g = 14;
            const k = bounds(m), cands = [];
            if (m.shape === "arrow") {
                const x = m.x1, y = m.y1;
                cands.push([x - w / 2, y - h - 14], [x - w / 2, y + 14], [x + 16, y - h / 2], [x - w - 16, y - h / 2]);
            } else {
                cands.push([k.l, k.t - h - g], [k.r - w, k.t - h - g], [k.r + g, k.t], [k.l - w - g, k.t],
                           [k.l, k.b + g], [k.r - w, k.b + g], [k.r + g, k.b - h], [k.l - w - g, k.b - h],
                           [(k.l + k.r - w) / 2, k.t - h - g], [k.l + 12, k.t + 12]);
            }
            let best = null, bestCost = Infinity;
            for (const [cx, cy] of cands) {
                const x = Math.max(8, Math.min(root.width - w - 8, cx)), y = Math.max(8, Math.min(limitB - h, cy));
                const box = {l: x - 3, t: y - 3, r: x + w + 3, b: y + h + 3};
                let cost = (x !== cx || y !== cy) ? 40 : 0;
                if (m.shape !== "arrow") cost += overlap(box, k) * 3;   // never hide the thing being marked
                for (const o of placed) cost += overlap(box, o) * 10;
                for (const o of obstacles) cost += overlap(box, o);
                if (cost < bestCost) { best = {x: x, y: y, w: w, h: h}; bestCost = cost; }
                if (cost === 0) break;
            }
            placed.push({l: best.x - 4, t: best.y - 4, r: best.x + best.w + 4, b: best.y + best.h + 4});
            out.push({x: best.x, y: best.y, w: best.w, h: best.h, text: m.label, key: m.key, born: m.born});
        }
        return out;
    }
    Timer { id: markTimer; interval: 20000; onTriggered: root.marks = [] }
    Repeater {
        model: root.marks
        delegate: Item {
            id: mark
            required property var modelData
            anchors.fill: parent
            property real progress: 0
            readonly property var strokes: Ink.strokes(modelData.shape, modelData.x1, modelData.y1, modelData.x2, modelData.y2, modelData.key * 97 + 13)
            NumberAnimation on progress { from: 0; to: 1; duration: modelData.shape === "highlight" ? 380 : 750; easing.type: Easing.InOutQuad; running: true }
            Rectangle { // marker highlight
                visible: mark.modelData.shape === "highlight"
                x: Math.min(mark.modelData.x1, mark.modelData.x2) - 4
                y: Math.min(mark.modelData.y1, mark.modelData.y2) - 3
                width: (Math.abs(mark.modelData.x2 - mark.modelData.x1) + 8) * mark.progress
                height: Math.abs(mark.modelData.y2 - mark.modelData.y1) + 6
                radius: 4; color: "#FFD43B"; opacity: 0.38
            }
            Repeater {
                model: mark.strokes.length
                delegate: Shape {
                    required property int index
                    anchors.fill: parent
                    preferredRendererType: Shape.GeometryRenderer
                    antialiasing: true
                    // Shaft first, then arrow wings.
                    readonly property real local: mark.modelData.shape === "arrow"
                        ? (index === 0 ? Math.min(1, mark.progress / 0.75) : Math.max(0, (mark.progress - 0.75) / 0.25))
                        : Math.max(0, Math.min(1, mark.progress * mark.strokes.length - index))
                    ShapePath {
                        fillColor: root.pointerColor; strokeColor: Qt.rgba(1, 1, 1, 0.85); strokeWidth: 1.2
                        joinStyle: ShapePath.RoundJoin; fillRule: ShapePath.WindingFill
                        PathSvg { path: Ink.outline(mark.strokes[index], local, 8) }
                    }
                }
            }
        }
    }

    Repeater { // labels above every stroke
        model: root.labelLayout
        delegate: Rectangle {
            required property var modelData
            x: modelData.x; y: modelData.y; width: modelData.w; height: modelData.h
            radius: height / 2; color: root.pointerColor
            border.width: 2; border.color: "white"
            opacity: 0
            SequentialAnimation on opacity { running: true
                PauseAnimation { duration: Math.max(0, 650 - (Date.now() - modelData.born)) } NumberAnimation { to: 1; duration: 180 } }
            Text { anchors.centerIn: parent; text: modelData.text; color: "white"; font.pixelSize: 15; font.weight: Font.DemiBold }
        }
    }

    // ---------------------------------------------------------------- bubble
    Timer { id: replyTimer; onTriggered: root.replyText = "" }
    Rectangle {
        id: bubble
        // With the bar open, progress shows inside the bar; the card carries answers.
        readonly property bool statusHere: root.statusText.length > 0 && root.barTop >= root.height
        readonly property bool showing: !root.suppressBubble && (statusHere || root.replyText.length > 0)
        anchors.horizontalCenter: parent.horizontalCenter
        y: (root.barTop < root.height ? root.barTop - 12 : root.height - 34) - height
        Behavior on y { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        width: Math.min(680, content.implicitWidth + 44)
        height: content.implicitHeight + 30
        radius: 22
        color: root.surfaceColor
        border.width: 1; border.color: Qt.rgba(0, 0, 0, 0.10)
        opacity: showing ? 1 : 0
        scale: showing ? 1 : 0.96
        Behavior on opacity { NumberAnimation { duration: 220 } }
        Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        Rectangle { // shadow
            z: -1; anchors.fill: parent; anchors.topMargin: 6; anchors.leftMargin: -2; anchors.rightMargin: -2; anchors.bottomMargin: -8
            radius: parent.radius; color: Qt.rgba(0, 0, 0, 0.12)
        }
        Row {
            id: content
            anchors.centerIn: parent
            spacing: 14
            Rectangle {
                id: orb
                width: 26; height: 26; radius: 13
                anchors.verticalCenter: parent.verticalCenter
                color: root.replyError ? "#C0504D" : root.pointerColor
                SequentialAnimation on scale {
                    running: root.statusText.length > 0; loops: Animation.Infinite
                    NumberAnimation { to: 0.78; duration: 520; easing.type: Easing.InOutSine }
                    NumberAnimation { to: 1.0; duration: 520; easing.type: Easing.InOutSine }
                }
            }
            Column {
                spacing: 4
                anchors.verticalCenter: parent.verticalCenter
                TextMetrics { id: replyMetrics; text: root.replyText; font.pixelSize: 17 }
                Text {
                    visible: root.replyText.length > 0
                    width: Math.min(600, replyMetrics.advanceWidth + 4)
                    text: root.replyText; wrapMode: Text.Wrap; textFormat: Text.PlainText
                    color: root.inkColor; font.pixelSize: 17; lineHeight: 1.15
                }
                Text {
                    visible: bubble.statusHere
                    text: root.statusText; color: root.mutedColor; font.pixelSize: 14; font.italic: true
                }
            }
        }
    }
}
