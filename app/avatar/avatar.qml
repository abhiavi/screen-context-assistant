import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtWebEngine
import org.kde.layershell as LayerShellQt

Window {
    id: root
    width: 560
    height: 440
    visible: true
    color: "transparent"
    flags: Qt.FramelessWindowHint

    LayerShellQt.Window.layer: LayerShellQt.Window.LayerOverlay
    LayerShellQt.Window.anchors: LayerShellQt.Window.AnchorBottom | LayerShellQt.Window.AnchorRight
    LayerShellQt.Window.exclusionZone: -1
    // NOTE: was KeyboardInteractivityNone - the ask bar needs real keyboard
    // focus to type into, which that setting refuses at the compositor
    // level regardless of what Qt does on top of it. OnDemand grants focus
    // only while a text field inside this surface actually has it.
    LayerShellQt.Window.keyboardInteractivity: LayerShellQt.Window.KeyboardInteractivityOnDemand

    readonly property int charSize: 240
    property bool hasSomethingToShow: false

    property real charX: root.width - charSize - 20
    Behavior on charX { NumberAnimation { duration: 2600; easing.type: Easing.InOutQuad } }

    Timer {
        interval: 1000
        running: true
        repeat: true
        property int tick: 0
        onTriggered: {
            tick++
            if (tick % 25 === 0 && !root.hasSomethingToShow) {
                var lane = root.width - root.charSize
                root.charX = Math.max(0, Math.min(lane, Math.random() * lane))
            }
        }
    }

    // --- Live2D character -------------------------------------------
    WebEngineView {
        id: character
        width: root.charSize
        height: root.charSize
        x: root.charX
        y: root.height - root.charSize - 56  // leave room for the ask bar below
        backgroundColor: "transparent"
        url: Qt.resolvedUrl("live2d_view.html")
    }

    // Declared as a SIBLING after (not nested inside) the WebEngineView -
    // belt-and-suspenders on top of the renderingType fix above.
    MouseArea {
        x: character.x
        y: character.y
        width: character.width
        height: character.height
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onClicked: function(mouse) {
            if (mouse.button === Qt.RightButton) {
                var lane = root.width - root.charSize
                root.charX = Math.random() * lane
            } else {
                backend.requestRecall("")
            }
        }
    }

    Connections {
        target: backend
        function onRecallReady(summary, trackId, appName) { panel.show(summary) }
        function onRecallFailed(error) { panel.show("Couldn't reach the backend: " + error) }
        function onAnswerReady(answer) { panel.show(answer) }
        function onAnswerFailed(error) { panel.show("Couldn't reach the backend: " + error) }
        function onRecallRequested() {
            root.hasSomethingToShow = true
            character.runJavaScript("setAvatarState('attentive')")
        }
    }

    // --- ask bar (always visible - "a place to put the question") ------
    Item {
        id: askBar
        x: 10
        y: root.height - 46
        width: root.width - 20
        height: 40

        Rectangle {
            anchors.fill: parent
            radius: 20
            color: Qt.rgba(0.11, 0.09, 0.16, 0.55)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.18)
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: Qt.rgba(0.49, 0.23, 0.93, 0.30)
                shadowBlur: 0.6
                shadowVerticalOffset: 3
            }
        }

        TextField {
            id: askInput
            anchors.left: parent.left
            anchors.right: sendButton.left
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 16
            anchors.rightMargin: 6
            height: 34
            placeholderText: "Ask me anything about your recent activity..."
            placeholderTextColor: Qt.rgba(1, 1, 1, 0.4)
            color: "#f5f3fa"
            font.pixelSize: 13
            background: Item {}
            onAccepted: askBar.submit()
        }

        Rectangle {
            id: sendButton
            anchors.right: parent.right
            anchors.rightMargin: 5
            anchors.verticalCenter: parent.verticalCenter
            width: 30
            height: 30
            radius: 15
            color: askInput.text.length > 0 ? "#7c3aed" : Qt.rgba(1, 1, 1, 0.12)
            Behavior on color { ColorAnimation { duration: 150 } }
            Text {
                anchors.centerIn: parent
                text: "→"
                color: "white"
                font.pixelSize: 16
                font.bold: true
            }
            MouseArea {
                anchors.fill: parent
                onClicked: askBar.submit()
            }
        }

        function submit() {
            if (askInput.text.trim().length === 0) return
            backend.askQuestion(askInput.text)
            askInput.text = ""
        }
    }

    // --- glassmorphic answer/recall panel -----------------------------
    Item {
        id: panel
        visible: opacity > 0
        opacity: 0
        width: 360
        height: panelText.implicitHeight + 44
        x: Math.max(8, Math.min(root.width - width - 8, root.charX + root.charSize / 2 - width / 2))
        y: Math.max(8, character.y - height - 14)

        Behavior on opacity { NumberAnimation { duration: 260 } }
        Behavior on x { NumberAnimation { duration: 2600; easing.type: Easing.InOutQuad } }

        Rectangle {
            id: glass
            anchors.fill: parent
            radius: 20
            color: Qt.rgba(0.11, 0.09, 0.16, 0.55)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.18)

            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.10) }
                GradientStop { position: 0.35; color: Qt.rgba(0.11, 0.09, 0.16, 0.55) }
                GradientStop { position: 1.0; color: Qt.rgba(0.05, 0.04, 0.09, 0.62) }
            }

            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: Qt.rgba(0.49, 0.23, 0.93, 0.35)
                shadowBlur: 0.8
                shadowVerticalOffset: 6
            }
        }

        Rectangle {
            anchors.top: glass.top
            anchors.left: glass.left
            anchors.right: glass.right
            anchors.margins: 1
            height: 1
            color: Qt.rgba(1, 1, 1, 0.35)
        }

        Text {
            id: panelText
            anchors.centerIn: parent
            width: parent.width - 36
            wrapMode: Text.WordWrap
            color: "#f5f3fa"
            font.pixelSize: 14
            font.letterSpacing: 0.2
            lineHeight: 1.25
        }

        Timer {
            id: hideTimer
            interval: 18000
            onTriggered: panel.hide()
        }

        function show(text) {
            panelText.text = text
            opacity = 1
            root.hasSomethingToShow = true
            character.runJavaScript("setAvatarState('speaking')")
            hideTimer.restart()
        }
        function hide() {
            opacity = 0
            root.hasSomethingToShow = false
            character.runJavaScript("setAvatarState('idle')")
        }
    }
}
