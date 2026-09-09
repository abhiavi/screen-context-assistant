import QtQuick
import QtQuick.Window
import org.kde.layershell as LayerShellQt

Window {
    id: root
    // Window must be large enough to contain the face AND the speech
    // bubble - a layer-shell surface clips to its own declared size, a
    // child positioned outside those bounds simply never renders (learned
    // the hard way - verified via live screenshot on mini).
    width: 320
    height: 240
    visible: true
    color: "transparent"
    flags: Qt.FramelessWindowHint

    LayerShellQt.Window.layer: LayerShellQt.Window.LayerOverlay
    LayerShellQt.Window.anchors: cornerAnchors[cornerIndex]
    LayerShellQt.Window.exclusionZone: -1
    LayerShellQt.Window.keyboardInteractivity: LayerShellQt.Window.KeyboardInteractivityNone

    property int cornerIndex: 0
    property var cornerAnchors: [
        LayerShellQt.Window.AnchorBottom | LayerShellQt.Window.AnchorRight,
        LayerShellQt.Window.AnchorBottom | LayerShellQt.Window.AnchorLeft,
        LayerShellQt.Window.AnchorTop | LayerShellQt.Window.AnchorLeft,
        LayerShellQt.Window.AnchorTop | LayerShellQt.Window.AnchorRight
    ]
    property bool dockRight: cornerIndex === 0 || cornerIndex === 3
    property bool dockBottom: cornerIndex === 0 || cornerIndex === 1

    // --- avatar face -------------------------------------------------
    Rectangle {
        id: face
        width: 84
        height: 84
        anchors.right: root.dockRight ? parent.right : undefined
        anchors.left: root.dockRight ? undefined : parent.left
        anchors.bottom: root.dockBottom ? parent.bottom : undefined
        anchors.top: root.dockBottom ? undefined : parent.top
        radius: width / 2
        color: "#7c3aed"
        border.color: "#ffffff"
        border.width: 3

        SequentialAnimation on scale {
            loops: Animation.Infinite
            NumberAnimation { to: 1.04; duration: 1400; easing.type: Easing.InOutSine }
            NumberAnimation { to: 1.0; duration: 1400; easing.type: Easing.InOutSine }
        }

        Row {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: -6
            spacing: 14
            Rectangle { width: 10; height: 10; radius: 5; color: "white" }
            Rectangle { width: 10; height: 10; radius: 5; color: "white" }
        }
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: 14
            width: 26
            height: 12
            radius: 6
            color: "white"
        }

        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            onClicked: function(mouse) {
                if (mouse.button === Qt.RightButton) {
                    root.cornerIndex = (root.cornerIndex + 1) % 4
                } else {
                    bubble.show("...")
                    backend.requestRecall("")
                }
            }
        }
    }

    // --- speech bubble -------------------------------------------------
    Rectangle {
        id: bubble
        visible: opacity > 0
        opacity: 0
        width: 260
        height: bubbleText.implicitHeight + 28

        anchors.right: root.dockRight ? face.right : undefined
        anchors.left: root.dockRight ? undefined : face.left
        anchors.bottom: root.dockBottom ? face.top : undefined
        anchors.top: root.dockBottom ? undefined : face.bottom
        anchors.bottomMargin: root.dockBottom ? 10 : 0
        anchors.topMargin: root.dockBottom ? 0 : 10

        radius: 14
        color: "#1e1b2e"
        border.color: "#7c3aed"
        border.width: 2

        Behavior on opacity { NumberAnimation { duration: 220 } }

        Text {
            id: bubbleText
            anchors.centerIn: parent
            width: parent.width - 28
            wrapMode: Text.WordWrap
            color: "white"
            font.pixelSize: 13
        }

        Timer {
            id: hideTimer
            interval: 14000
            onTriggered: bubble.opacity = 0
        }

        function show(text) {
            bubbleText.text = text
            opacity = 1
            hideTimer.restart()
        }
    }

    Connections {
        target: backend
        function onRecallReady(summary, trackId, appName) {
            bubble.show(summary)
        }
        function onRecallFailed(error) {
            bubble.show("Couldn't reach the backend: " + error)
        }
    }
}
