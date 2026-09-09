import QtQuick
import QtQuick.Window
import QtQuick.Effects
import QtWebEngine
import org.kde.layershell as LayerShellQt

Window {
    id: root
    // A wide, short "lane" along one screen edge that the character roams
    // within (smooth x-animation, no compositor protocol involved), plus
    // headroom above for the glass recall panel. Layer-shell surfaces clip
    // to their own declared size - anything must fit inside these bounds.
    width: 560
    height: 420
    visible: true
    color: "transparent"
    flags: Qt.FramelessWindowHint

    LayerShellQt.Window.layer: LayerShellQt.Window.LayerOverlay
    LayerShellQt.Window.anchors: LayerShellQt.Window.AnchorBottom | LayerShellQt.Window.AnchorRight
    LayerShellQt.Window.exclusionZone: -1
    LayerShellQt.Window.keyboardInteractivity: LayerShellQt.Window.KeyboardInteractivityNone

    readonly property int charSize: 240
    property bool hasSomethingToShow: false

    // --- roaming ---------------------------------------------------
    property real charX: root.width - charSize - 20
    Behavior on charX { NumberAnimation { duration: 2600; easing.type: Easing.InOutQuad } }

    Timer {
        interval: 1000
        running: true
        repeat: true
        property int tick: 0
        onTriggered: {
            tick++
            // roam every ~25s while idle; skip while showing something (stay put, be findable)
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
        y: root.height - root.charSize
        backgroundColor: "transparent"
        url: Qt.resolvedUrl("live2d_view.html")
        onJavaScriptConsoleMessage: function(level, message, lineNumber, sourceID) {
            console.log("JS[" + level + "] " + sourceID + ":" + lineNumber + " " + message)
        }
        onLoadingChanged: function(loadRequest) {
            console.log("loading status=" + loadRequest.status + " url=" + loadRequest.url)
        }

        // WebEngineView eats mouse events by default; a plain MouseArea on
        // top intercepts clicks/right-clicks for our own handling.
        MouseArea {
            anchors.fill: parent
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
    }

    Connections {
        target: backend
        function onRecallReady(summary, trackId, appName) {
            panel.show(summary)
        }
        function onRecallFailed(error) {
            panel.show("Couldn't reach the backend: " + error)
        }
    }

    Component.onCompleted: {
        // "attentive" fires the instant a recall is requested (before the
        // network reply lands) so the character reacts immediately.
        backend.recallRequested.connect(function() {
            root.hasSomethingToShow = true
            character.runJavaScript("setAvatarState('attentive')")
        })
    }

    // --- glassmorphic recall panel -----------------------------------
    // True KWin blur-behind needs the org_kde_kwin_blur_manager Wayland
    // protocol, which has no QML/PySide6 binding available on this system
    // (checked: no such type in org.kde.kwindowsystem's QML module, and
    // pywayland - the only route to hand-roll the protocol - isn't
    // installed). This simulates the glass look with layered translucency
    // + a soft shadow instead of true see-through blur of the desktop.
    Item {
        id: panel
        visible: opacity > 0
        opacity: 0
        width: 360
        height: panelText.implicitHeight + 44
        x: Math.max(8, Math.min(root.width - width - 8, root.charX + root.charSize / 2 - width / 2))
        y: root.height - root.charSize - height - 14

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
                shadowHorizontalOffset: 0
            }
        }

        // subtle top highlight line - the classic glass "edge catches light" cue
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
            interval: 16000
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
