// SPDX-License-Identifier: GPL-3.0-only
// Sinux Settings: connect to Meta, choose how to talk to Sinux, and what it may do.
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Window

Window {
    id: app
    title: "Sinux Settings"
    width: 900; height: 620
    minimumWidth: 760; minimumHeight: 520
    visible: false
    color: bg

    // ------------------------------------------------------------ data (from the service)
    property string configJson: "{}"
    readonly property var config: { try { return JSON.parse(configJson); } catch (e) { return ({}); } }
    readonly property var prefs: config.settings || ({})
    property string page: "connection"
    property bool connecting: false
    property string connectMessage: ""
    property bool connectFailed: false

    function save(patch) { sinux.SaveSettings(JSON.stringify(patch)); }

    // ------------------------------------------------------------ design tokens
    readonly property color bg: "#F4F3F0"
    readonly property color sidebar: "#ECEAE5"
    readonly property color card: "#FFFFFF"
    readonly property color ink: "#1C1C1E"
    readonly property color muted: "#6E6E73"
    readonly property color hairline: Qt.rgba(0, 0, 0, 0.08)
    readonly property color accent: "#2F7BF6"
    readonly property color good: "#30A46C"
    readonly property color bad: "#E5484D"
    readonly property string family: "Inter"

    // ------------------------------------------------------------ small components
    component Toggle: AbstractButton {
        id: toggle
        checkable: true
        implicitWidth: 44; implicitHeight: 26
        background: Rectangle {
            radius: 13; color: toggle.checked ? app.accent : "#D1D1D6"
            Behavior on color { ColorAnimation { duration: 160 } }
            Rectangle {
                width: 22; height: 22; radius: 11; y: 2
                x: toggle.checked ? parent.width - width - 2 : 2
                color: "white"
                Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
                layer.enabled: false
                border.width: 1; border.color: Qt.rgba(0, 0, 0, 0.06)
            }
        }
        contentItem: Item {}
        scale: down ? 0.96 : 1
        Behavior on scale { NumberAnimation { duration: 120 } }
    }

    component Card: Rectangle {
        default property alias content: column.data
        property alias spacing: column.spacing
        Layout.fillWidth: true
        implicitHeight: column.implicitHeight + 36
        radius: 14; color: app.card
        border.width: 1; border.color: app.hairline
        ColumnLayout { id: column; anchors { left: parent.left; right: parent.right; top: parent.top; margins: 18 } spacing: 14 }
    }

    component Row2: RowLayout {
        property string title
        property string detail
        default property alias control: holder.data
        Layout.fillWidth: true
        spacing: 16
        ColumnLayout {
            Layout.fillWidth: true; spacing: 3
            Text { text: title; color: app.ink; font.family: app.family; font.pixelSize: 15; font.weight: Font.Medium }
            Text { visible: detail.length > 0; Layout.fillWidth: true; text: detail; color: app.muted
                   font.family: app.family; font.pixelSize: 13; wrapMode: Text.Wrap }
        }
        Item { id: holder; implicitWidth: childrenRect.width; implicitHeight: childrenRect.height }
    }

    component Pill: AbstractButton {
        id: pill
        property bool selected: false
        implicitHeight: 34; implicitWidth: label.implicitWidth + 28
        contentItem: Text { id: label; text: pill.text; color: pill.selected ? "white" : app.ink
            font.family: app.family; font.pixelSize: 14; font.weight: Font.Medium
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 17; color: pill.selected ? app.accent : pill.hovered ? "#E5E4E0" : "#EFEEEA"
            Behavior on color { ColorAnimation { duration: 140 } } }
        scale: down ? 0.96 : 1
        Behavior on scale { NumberAnimation { duration: 120 } }
    }

    component PrimaryButton: AbstractButton {
        id: button
        property bool quiet: false
        implicitHeight: 38; implicitWidth: label.implicitWidth + 36
        contentItem: Text { id: label; text: button.text; color: button.quiet ? app.ink : "white"
            font.family: app.family; font.pixelSize: 14; font.weight: Font.DemiBold
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
        background: Rectangle { radius: 10
            color: button.quiet ? (button.hovered ? "#E5E4E0" : "#EFEEEA") : !button.enabled ? "#A9C4F5" : button.hovered ? "#2569D9" : app.accent }
        scale: down ? 0.97 : 1
        Behavior on scale { NumberAnimation { duration: 120 } }
    }

    // ------------------------------------------------------------ layout
    RowLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle { // sidebar
            Layout.fillHeight: true; Layout.preferredWidth: 230
            color: app.sidebar
            ColumnLayout {
                anchors { fill: parent; margins: 18; topMargin: 26 }
                spacing: 4
                RowLayout {
                    spacing: 10; Layout.bottomMargin: 22; Layout.leftMargin: 6
                    Rectangle { width: 26; height: 26; radius: 13
                        gradient: Gradient { GradientStop { position: 0; color: "#6AA3FF" } GradientStop { position: 1; color: app.accent } } }
                    Text { text: "Sinux"; color: app.ink; font.family: app.family; font.pixelSize: 20; font.weight: Font.Bold }
                }
                Repeater {
                    model: [{id: "connection", label: "Connection"}, {id: "voice", label: "Voice & talk key"},
                            {id: "assistant", label: "Assistant"}, {id: "about", label: "About"}]
                    delegate: AbstractButton {
                        required property var modelData
                        Layout.fillWidth: true; implicitHeight: 38
                        onClicked: app.page = modelData.id
                        background: Rectangle { radius: 9
                            color: app.page === modelData.id ? "white" : parent.hovered ? Qt.rgba(1, 1, 1, 0.5) : "transparent"
                            border.width: app.page === modelData.id ? 1 : 0; border.color: app.hairline }
                        contentItem: Text { leftPadding: 12; text: modelData.label; verticalAlignment: Text.AlignVCenter
                            color: app.ink; font.family: app.family; font.pixelSize: 14
                            font.weight: app.page === modelData.id ? Font.DemiBold : Font.Normal }
                    }
                }
                Item { Layout.fillHeight: true }
                RowLayout {
                    spacing: 8; Layout.leftMargin: 8
                    Rectangle { width: 8; height: 8; radius: 4; color: app.config.connected ? app.good : app.bad }
                    Text { text: app.config.connected ? "Connected to Meta" : "Not connected"; color: app.muted
                        font.family: app.family; font.pixelSize: 13 }
                }
            }
        }

        Flickable { // content
            Layout.fillWidth: true; Layout.fillHeight: true
            contentHeight: pageColumn.implicitHeight + 64
            clip: true
            ScrollBar.vertical: ScrollBar {}
            ColumnLayout {
                id: pageColumn
                width: Math.min(parent.width - 72, 620)
                x: 36; y: 30
                spacing: 16

                Text {
                    text: ({connection: "Connection", voice: "Voice & talk key", assistant: "Assistant", about: "About Sinux"})[app.page]
                    color: app.ink; font.family: app.family; font.pixelSize: 26; font.weight: Font.Bold
                    Layout.bottomMargin: 4
                }

                // ================================================ Connection
                ColumnLayout {
                    visible: app.page === "connection"; Layout.fillWidth: true; spacing: 16
                    Card {
                        RowLayout {
                            spacing: 14
                            Rectangle { width: 44; height: 44; radius: 22
                                color: app.config.connected ? Qt.rgba(0.19, 0.64, 0.42, 0.14) : Qt.rgba(0.9, 0.28, 0.3, 0.12)
                                Rectangle { anchors.centerIn: parent; width: 14; height: 14; radius: 7; color: app.config.connected ? app.good : app.bad } }
                            ColumnLayout {
                                spacing: 2
                                Text { text: app.config.connected ? "Sinux is connected to Meta" : "Connect Sinux to Meta"
                                    color: app.ink; font.family: app.family; font.pixelSize: 17; font.weight: Font.DemiBold }
                                Text { text: app.config.connected
                                        ? (app.config.models || {})[app.config.model] + " · " + (app.config.used || 0) + " of " + (app.config.limit || 0)
                                          + " requests · " + Math.round((app.config.tokens || 0) / 1000) + "k tokens this session"
                                        : "Add your Meta API key below to start talking to Sinux."
                                    color: app.muted; font.family: app.family; font.pixelSize: 13 }
                            }
                        }
                    }
                    Card {
                        Text { text: "Meta API key"; color: app.ink; font.family: app.family; font.pixelSize: 15; font.weight: Font.Medium }
                        RowLayout {
                            Layout.fillWidth: true; spacing: 10
                            TextField {
                                id: keyField
                                Layout.fillWidth: true; implicitHeight: 40
                                echoMode: showKey.checked ? TextInput.Normal : TextInput.Password
                                placeholderText: app.config.connected ? "Connected — paste a new key to replace it" : "Paste your key"
                                font.family: app.family; font.pixelSize: 14; color: app.ink; leftPadding: 12
                                background: Rectangle { radius: 10; color: "#F6F5F2"; border.width: keyField.activeFocus ? 2 : 1
                                    border.color: keyField.activeFocus ? app.accent : app.hairline }
                                onAccepted: connectButton.clicked()
                            }
                            Pill { id: showKey; checkable: true; text: checked ? "Hide" : "Show"; selected: false }
                        }
                        Row2 {
                            title: "Remember on this computer"
                            detail: "Saved only on your laptop (~/.config/sinux, readable by you only), never inside the desktop. Off: the key is forgotten when Sinux stops."
                            Toggle { id: remember; checked: !!app.config.remembered }
                        }
                        RowLayout {
                            spacing: 10
                            PrimaryButton {
                                id: connectButton
                                text: app.connecting ? "Checking…" : "Connect"
                                enabled: !app.connecting && keyField.text.trim().length > 0
                                onClicked: { app.connecting = true; app.connectMessage = ""; sinux.Connect(keyField.text.trim(), remember.checked); }
                            }
                            PrimaryButton { quiet: true; visible: !!app.config.remembered; text: "Forget saved key"
                                onClicked: sinux.Forget(false) }
                            Text { Layout.fillWidth: true; text: app.connectMessage; wrapMode: Text.Wrap
                                color: app.connectFailed ? app.bad : app.good; font.family: app.family; font.pixelSize: 13 }
                        }
                        Text { Layout.fillWidth: true; wrapMode: Text.Wrap; color: app.muted; font.family: app.family; font.pixelSize: 12
                            text: "Connecting sends one tiny test request. Get a key from the Meta developer platform (dev.meta.ai)." }
                    }
                    Card {
                        Text { text: "Model"; color: app.ink; font.family: app.family; font.pixelSize: 15; font.weight: Font.Medium }
                        Repeater {
                            model: Object.keys(app.config.models || {})
                            delegate: AbstractButton {
                                required property string modelData
                                readonly property bool chosen: app.config.model === modelData
                                Layout.fillWidth: true; implicitHeight: 54
                                onClicked: app.save({model: modelData})
                                background: Rectangle { radius: 11; color: chosen ? Qt.rgba(0.18, 0.48, 0.96, 0.08) : "#F6F5F2"
                                    border.width: chosen ? 2 : 1; border.color: chosen ? app.accent : app.hairline }
                                contentItem: RowLayout {
                                    spacing: 12
                                    Rectangle { Layout.leftMargin: 14; width: 18; height: 18; radius: 9; color: "white"
                                        border.width: chosen ? 6 : 1.5; border.color: chosen ? app.accent : "#C7C7CC" }
                                    Text { Layout.fillWidth: true; text: (app.config.models || {})[modelData] || modelData
                                        color: app.ink; font.family: app.family; font.pixelSize: 14 }
                                }
                            }
                        }
                    }
                }

                // ================================================ Voice
                ColumnLayout {
                    visible: app.page === "voice"; Layout.fillWidth: true; spacing: 16
                    Card {
                        Text { text: "Talk key"; color: app.ink; font.family: app.family; font.pixelSize: 15; font.weight: Font.Medium }
                        Text { Layout.fillWidth: true; wrapMode: Text.Wrap; color: app.muted; font.family: app.family; font.pixelSize: 13
                            text: "Hold it and speak, release to send. A quick tap opens the island for typing. The microphone is only used while you hold the key." }
                        Flow {
                            Layout.fillWidth: true; spacing: 8
                            Repeater {
                                model: Object.keys(app.config.talkKeys || {})
                                delegate: Pill { required property string modelData
                                    text: app.config.talkKeys[modelData]; selected: app.prefs.talkKey === modelData
                                    onClicked: app.save({talkKey: modelData}) }
                            }
                        }
                    }
                    Card {
                        Row2 { title: "Speak answers"; detail: "Sinux reads its replies and narrates what it draws (Piper voice, runs on your laptop)."
                            Toggle { checked: app.prefs.speak !== false; onToggled: app.save({speak: checked}) } }
                        Row2 {
                            title: "Voice speed"; detail: Math.round((app.prefs.voiceSpeed || 1) * 100) + "%"
                            Slider {
                                id: speed; implicitWidth: 200; from: 0.7; to: 1.5; stepSize: 0.05
                                value: app.prefs.voiceSpeed || 1.0
                                onMoved: speedSave.restart()
                                Timer { id: speedSave; interval: 300; onTriggered: app.save({voiceSpeed: Math.round(speed.value * 100) / 100}) }
                                background: Rectangle { x: speed.leftPadding; y: speed.topPadding + speed.availableHeight / 2 - 2
                                    width: speed.availableWidth; height: 4; radius: 2; color: "#E5E4E0"
                                    Rectangle { width: speed.visualPosition * parent.width; height: 4; radius: 2; color: app.accent } }
                                handle: Rectangle { x: speed.leftPadding + speed.visualPosition * (speed.availableWidth - width)
                                    y: speed.topPadding + speed.availableHeight / 2 - height / 2
                                    width: 22; height: 22; radius: 11; color: "white"; border.width: 1; border.color: Qt.rgba(0, 0, 0, 0.12) }
                            }
                        }
                    }
                    Card {
                        Text { text: "Languages you speak"; color: app.ink; font.family: app.family; font.pixelSize: 15; font.weight: Font.Medium }
                        Text { Layout.fillWidth: true; wrapMode: Text.Wrap; color: app.muted; font.family: app.family; font.pixelSize: 13
                            text: "Helps Meta Muse Voice Transcribe recognise you. You can mix languages in one sentence." }
                        Flow {
                            Layout.fillWidth: true; spacing: 8
                            Repeater {
                                model: ["English", "Turkish", "French", "Arabic", "Spanish", "German", "Italian", "Portuguese"]
                                delegate: Pill {
                                    required property string modelData
                                    readonly property var chosen: app.prefs.languages || ["English"]
                                    text: modelData; selected: chosen.indexOf(modelData) >= 0
                                    onClicked: {
                                        let next = chosen.filter(l => l !== modelData);
                                        if (!selected) next = next.concat([modelData]);
                                        if (next.length) app.save({languages: next});
                                    }
                                }
                            }
                        }
                    }
                }

                // ================================================ Assistant
                ColumnLayout {
                    visible: app.page === "assistant"; Layout.fillWidth: true; spacing: 16
                    Card {
                        Row2 { title: "Let Sinux act for you"
                            detail: "Open apps and files, arrange windows, click and type. When off, Sinux only explains and points."
                            Toggle { checked: app.prefs.allowActions !== false; onToggled: app.save({allowActions: checked}) } }
                        Rectangle { Layout.fillWidth: true; height: 1; color: app.hairline }
                        Row2 { title: "Allow terminal commands"
                            detail: "Lets Sinux run shell commands for tasks no other tool covers."
                            Toggle { checked: app.prefs.allowShell !== false; onToggled: app.save({allowShell: checked}) } }
                        Rectangle { Layout.fillWidth: true; height: 1; color: app.hairline }
                        Row2 { title: "Reduce motion"; detail: "Calmer island and pointer animations."
                            Toggle { checked: !!app.prefs.reducedMotion; onToggled: app.save({reducedMotion: checked}) } }
                    }
                    Card {
                        Row2 { title: "Start a new conversation"; detail: "Sinux forgets this session's chat. Your files are not touched."
                            PrimaryButton { quiet: true; text: "Clear"; onClicked: sinux.Reset() } }
                    }
                }

                // ================================================ About
                ColumnLayout {
                    visible: app.page === "about"; Layout.fillWidth: true; spacing: 16
                    Card {
                        Text { text: "Sinux — an operating system with an assistant built in"; color: app.ink
                            font.family: app.family; font.pixelSize: 16; font.weight: Font.DemiBold }
                        Text { Layout.fillWidth: true; wrapMode: Text.Wrap; color: app.muted; font.family: app.family; font.pixelSize: 13; lineHeight: 1.2
                            text: "Thinking and seeing: Meta Muse Spark / Muse Wonder\nHearing: Meta Muse Voice Transcribe\nSpeaking: Piper (local, open source)\nDesktop: KDE Plasma / KWin, with Sinux's own island, pointer and tools.\n\nDevelopment preview — runs in an isolated desktop, not your everyday system." }
                    }
                    Card {
                        Text { text: "Privacy"; color: app.ink; font.family: app.family; font.pixelSize: 15; font.weight: Font.Medium }
                        Text { Layout.fillWidth: true; wrapMode: Text.Wrap; color: app.muted; font.family: app.family; font.pixelSize: 13; lineHeight: 1.2
                            text: "• Screenshots are sent to Meta only when a request is about the screen.\n• The microphone records only while you hold the talk key.\n• Your API key stays outside the desktop and is saved only if you choose to remember it." }
                    }
                }
            }
        }
    }
}
