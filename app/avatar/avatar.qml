import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtWebEngine
import org.kde.layershell as LayerShellQt

Window {
    id: root
    width: 460
    height: 760
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
    readonly property int collapsedCharSize: 72
    property bool hasSomethingToShow: false
    property bool capturePaused: false
    property bool recording: false
    property bool speaking: false
    property bool answerIsFromVoice: false  // set true only by the mic path, so TTS
                                             // only speaks answers to voice-asked
                                             // questions, never typed ones

    // Starts small and out of the way ("should not come in maximum size
    // upfront") - a click expands it; it quietly re-collapses after a
    // while of no engagement.
    property bool expanded: false
    property int currentCharSize: expanded ? charSize : collapsedCharSize

    function expand() {
        if (!expanded) expanded = true
        collapseTimer.restart()
    }

    Timer {
        id: collapseTimer
        interval: 45000
        onTriggered: {
            if (root.expanded && !panel.visible && !askInput.activeFocus) {
                root.expanded = false
            } else {
                collapseTimer.restart()  // still showing something / being typed into - check again later
            }
        }
    }

    property real charX: root.width - charSize - 20
    Behavior on charX { NumberAnimation { duration: 2600; easing.type: Easing.InOutQuad } }
    Behavior on currentCharSize { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }

    Timer {
        interval: 1000
        running: true
        repeat: true
        property int tick: 0
        onTriggered: {
            tick++
            if (tick % 25 === 0 && !root.hasSomethingToShow && root.expanded) {
                var lane = root.width - root.charSize
                root.charX = Math.max(0, Math.min(lane, Math.random() * lane))
            }
        }
    }

    // --- Live2D character -------------------------------------------
    property string currentAvatarId: initialAvatarId

    WebEngineView {
        id: character
        width: root.currentCharSize
        height: root.currentCharSize
        x: root.charX
        y: root.height - height - (root.expanded ? 56 : 20)  // leave room for the ask bar once expanded
        backgroundColor: "transparent"
        url: Qt.resolvedUrl("live2d_view.html?avatar=" + root.currentAvatarId)
        // The embedded browser view intercepts right-click for its own
        // native context menu (Reload/Inspect/etc.) before our sibling
        // MouseArea ever sees it - confirmed live: right-click-to-move-
        // corners silently did nothing (Operator report, 2026-09-10).
        // Suppress that menu outright rather than relying on it never
        // appearing.
        onContextMenuRequested: function(request) { request.accepted = true }
    }

    // Visible companion state when paused (upgrade roadmap "Now" item
    // 3/6) - a plain icon overlay rather than anything subtler, since the
    // whole point is that pausing capture should never be silent. Shown
    // regardless of collapsed/expanded state.
    Rectangle {
        visible: root.capturePaused
        x: character.x + character.width - width + 4
        y: character.y - 4
        width: 22
        height: 22
        radius: 11
        color: "#dc2626"
        border.width: 2
        border.color: Qt.rgba(1, 1, 1, 0.85)
        Row {
            anchors.centerIn: parent
            spacing: 3
            Rectangle { width: 3; height: 9; color: "white"; radius: 1 }
            Rectangle { width: 3; height: 9; color: "white"; radius: 1 }
        }
    }

    // Declared as a SIBLING after (not nested inside) the WebEngineView -
    // belt-and-suspenders on top of the renderingType fix above.
    MouseArea {
        x: character.x
        y: character.y
        width: character.width
        height: character.height
        acceptedButtons: Qt.LeftButton | Qt.MiddleButton
        onClicked: function(mouse) {
            if (!root.expanded) {
                root.expand()  // first click just opens it, doesn't also trigger a recall
                return
            }
            collapseTimer.restart()
            if (mouse.button === Qt.LeftButton && (mouse.modifiers & Qt.ControlModifier)) {
                // Move to a new corner. Was right-click - moved off it
                // since that never reached this handler (see the
                // suppressed contextMenuRequested above); Ctrl+click routes
                // through the same LeftButton path already proven to work.
                var lane = root.width - root.charSize
                root.charX = Math.random() * lane
            } else if (mouse.button === Qt.MiddleButton) {
                // cycle to the next available Live2D character, live -
                // no page reload, no restart needed (see setAvatar() in
                // live2d_view.html). Only lasts for this session; edit
                // avatar_id in avatar.json to change the default.
                var ids = availableAvatarIds
                var idx = ids.indexOf(root.currentAvatarId)
                root.currentAvatarId = ids[(idx + 1) % ids.length]
                character.runJavaScript("setAvatar('" + root.currentAvatarId + "')")
            } else {
                panel.leaveHistoryMode()
                backend.requestRecall("")
            }
        }
        // Scroll to browse past exchanges - "different discussion on
        // different work" (plan). Lazily fetches history on first use.
        onWheel: function(wheel) {
            root.expand()
            if (!panel.historyLoaded) {
                panel.historyLoaded = true
                backend.loadHistory("")
            }
            if (wheel.angleDelta.y > 0) {
                panel.showHistoryAt(panel.historyIndex + 1)
            } else {
                panel.showHistoryAt(panel.historyIndex - 1)
            }
        }
    }

    // Window controls - only meaningful once expanded (nothing to
    // minimize when already collapsed to the small icon). No OS-drawn
    // title bar exists here (Qt.FramelessWindowHint, layer-shell surface),
    // so these are hand-drawn rather than coming for free.
    Row {
        id: windowControls
        visible: root.expanded
        opacity: root.expanded ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 180 } }
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 8
        anchors.rightMargin: 8
        spacing: 6
        z: 10  // above the panel/character so it's never covered

        Rectangle {
            width: 22; height: 22; radius: 11
            color: minimizeHover.hovered ? Qt.rgba(1, 1, 1, 0.22) : Qt.rgba(1, 1, 1, 0.1)
            Behavior on color { ColorAnimation { duration: 120 } }
            Text { anchors.centerIn: parent; text: "–"; color: "white"; font.pixelSize: 14 }
            HoverHandler { id: minimizeHover }
            MouseArea {
                anchors.fill: parent
                onClicked: root.expanded = false
            }
        }
        Rectangle {
            width: 22; height: 22; radius: 11
            color: closeHover.hovered ? "#e0433b" : Qt.rgba(1, 1, 1, 0.1)
            Behavior on color { ColorAnimation { duration: 120 } }
            Text { anchors.centerIn: parent; text: "×"; color: "white"; font.pixelSize: 15 }
            HoverHandler { id: closeHover }
            MouseArea {
                anchors.fill: parent
                onClicked: Qt.quit()
            }
        }
    }

    Connections {
        target: backend
        function onRecallReady(summary, trackId, appName) { panel.leaveHistoryMode(); panel.show(summary) }
        function onRecallFailed(error) { panel.leaveHistoryMode(); panel.show("Couldn't reach the backend: " + error) }
        function onAnswerReady(answer) {
            panel.leaveHistoryMode()
            panel.show(answer)
            if (root.answerIsFromVoice) {
                voice.speak(answer)
                root.answerIsFromVoice = false
            }
        }
        function onAnswerFailed(error) { panel.leaveHistoryMode(); panel.show("Couldn't reach the backend: " + error) }
        function onHistoryReady(jsonText) {
            panel.historyEntries = JSON.parse(jsonText)
        }
        function onCapturePausedChanged(paused) {
            root.capturePaused = paused
        }
        function onRecallRequested() {
            root.expand()  // covers the proactive idle-return path too, which never goes through the character's own click handler
            root.hasSomethingToShow = true
            character.runJavaScript("setAvatarState('attentive')")
        }
        // A single layer-shell surface lives on exactly one output at a
        // time (the protocol has no "spans multiple monitors" concept) -
        // "roam across all three monitors" means relocate to whichever one
        // has the Operator's focused window, not slide across the gap.
        function onActiveScreenChanged(index) {
            if (index < 0 || index >= Qt.application.screens.length) return
            var s = Qt.application.screens[index]
            if (s === root.screen) return
            // A layer-shell surface's output binding is fixed at creation
            // time - changing LayerShellQt.Window.screen on an already-
            // mapped window is a silent no-op (confirmed live: the QML
            // property updated correctly but the surface stayed put).
            // Hiding and re-showing forces Qt to tear down and recreate
            // the platform surface on the new output.
            root.visible = false
            root.screen = s
            LayerShellQt.Window.screen = s
            root.visible = true
        }
    }

    Connections {
        target: voice
        function onRecordingChanged(isRecording) { root.recording = isRecording }
        function onSpeakingChanged(isSpeaking) { root.speaking = isSpeaking }
        function onTranscriptionReady(text) {
            root.answerIsFromVoice = true
            collapseTimer.restart()
            backend.askQuestion(text)
        }
        function onTranscriptionFailed(error) {
            root.answerIsFromVoice = false
            panel.leaveHistoryMode()
            panel.show("Voice: " + error)
        }
    }

    // --- ask bar ("a place to put the question") - only once expanded,
    // so the collapsed companion doesn't take up screen space upfront ----
    Item {
        id: askBar
        visible: root.expanded
        enabled: root.expanded
        opacity: root.expanded ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 180 } }
        x: 10
        y: root.height - 46
        width: root.width - 20
        height: 40

        Rectangle {
            anchors.fill: parent
            radius: 20
            color: Qt.rgba(0.08, 0.07, 0.12, 0.90)
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

        // Push-to-talk mic button - only shown if voice models are
        // actually configured (voiceEnabled context property, main.py).
        // Click to start listening, click again to stop and transcribe;
        // not hold-to-talk, since tracking press-and-hold reliably across
        // a MouseArea risks losing the release event (e.g. cursor drifts
        // off the button while held) and cutting recording early.
        Rectangle {
            id: micButton
            visible: typeof voiceEnabled !== "undefined" && voiceEnabled
            anchors.left: parent.left
            anchors.leftMargin: 5
            anchors.verticalCenter: parent.verticalCenter
            width: 30
            height: 30
            radius: 15
            // Same button doubles as "stop speaking" while TTS is playing -
            // the mic obviously can't record while the avatar is talking
            // over it anyway, so reusing the slot instead of a second
            // button avoids a layout jump for something the same physical
            // spot never needs simultaneously.
            color: (root.recording || root.speaking) ? "#e0433b" : Qt.rgba(1, 1, 1, 0.12)
            Behavior on color { ColorAnimation { duration: 150 } }
            SequentialAnimation on opacity {
                running: root.recording || root.speaking
                loops: Animation.Infinite
                NumberAnimation { to: 0.5; duration: 500 }
                NumberAnimation { to: 1.0; duration: 500 }
            }
            Text {
                anchors.centerIn: parent
                text: root.speaking ? "■" : "●"  // square = stop, dot standing in for a mic icon
                color: "white"
                font.pixelSize: 13
            }
            MouseArea {
                anchors.fill: parent
                onClicked: {
                    if (root.speaking) {
                        voice.stopSpeaking()
                    } else if (root.recording) {
                        voice.stopRecording()
                    } else {
                        root.answerIsFromVoice = false  // reset from any previous failed attempt
                        voice.startRecording()
                    }
                }
            }
        }

        TextField {
            id: askInput
            anchors.left: (typeof voiceEnabled !== "undefined" && voiceEnabled) ? micButton.right : parent.left
            anchors.right: sendButton.left
            anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: 8
            anchors.rightMargin: 6
            height: 34
            placeholderText: root.recording ? "Listening..." : (root.speaking ? "Speaking... (click to stop)" : "Ask me anything about your recent activity...")
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
            collapseTimer.restart()
            root.answerIsFromVoice = false
            backend.askQuestion(askInput.text)
            askInput.text = ""
        }
    }

    // --- glassmorphic answer/recall panel -----------------------------
    // Fixed to the top area of the window (decoupled from the character's
    // roaming x) so there's always full, predictable room for a long
    // markdown-formatted expert answer, with internal scrolling for
    // anything past that - was clipped/unreadable before (plan feedback:
    // "response is not fully visible since scrolling is not enabled").
    Item {
        id: panel
        visible: opacity > 0
        opacity: 0
        readonly property int maxHeight: 420
        width: root.width - 40
        height: Math.min(maxHeight, panelText.implicitHeight + (historyIndex >= 0 ? 68 : 44))
        x: 20
        y: 20

        property var historyEntries: []
        property int historyIndex: -1  // -1 = showing live content, not browsing
        property bool historyLoaded: false

        Behavior on opacity { NumberAnimation { duration: 260 } }
        Behavior on height { NumberAnimation { duration: 180; easing.type: Easing.OutQuad } }

        Rectangle {
            id: glass
            anchors.fill: parent
            radius: 20
            color: Qt.rgba(0.08, 0.07, 0.12, blurActive ? 0.6 : 0.92)
            border.width: 1
            border.color: Qt.rgba(1, 1, 1, 0.18)

            // Desktop content behind the panel used to bleed through and
            // merge with our own text at ~0.55 alpha with only a simulated
            // tint (no real blurring of what's behind) - confirmed live via
            // screenshot. blurActive tracks whether RealBlur.qml (real
            // compositor blur-behind, see wayland_blur/) actually attached
            // at runtime: when it has, the content behind is genuinely
            // softened so a lower, glassier alpha stays legible; when it
            // hasn't (older KWin, blur effect disabled, non-KWin
            // compositor), we fall back to the old near-opaque tint so
            // legibility never regresses.
            readonly property bool blurActive: waylandBlurLoader.item ? waylandBlurLoader.item.supported : false

            gradient: Gradient {
                GradientStop { position: 0.0; color: Qt.rgba(1, 1, 1, 0.08) }
                GradientStop { position: 0.35; color: Qt.rgba(0.08, 0.07, 0.12, glass.blurActive ? 0.6 : 0.92) }
                GradientStop { position: 1.0; color: Qt.rgba(0.04, 0.03, 0.07, glass.blurActive ? 0.65 : 0.95) }
            }

            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: Qt.rgba(0.49, 0.23, 0.93, 0.35)
                shadowBlur: 0.8
                shadowVerticalOffset: 6
            }

            // Real compositor-side blur-behind (ext_background_effect_v1),
            // in its own file so a missing/unbuilt plugin only fails this
            // Loader, never the whole app. Paints nothing itself - it just
            // tells KWin which region of the window to blur underneath.
            Loader {
                id: waylandBlurLoader
                anchors.fill: parent
                active: typeof hasWaylandBlur !== "undefined" && hasWaylandBlur
                source: active ? Qt.resolvedUrl("RealBlur.qml") : ""
                onStatusChanged: if (status === Loader.Error)
                    console.log("real blur unavailable, using simulated glass")
            }

            // The compositor-side blur region set below is NOT tied to Qt's
            // own opacity/rendering - set_blur_region() persists on KWin's
            // side until explicitly cleared, regardless of whether this
            // Rectangle is still drawing anything. Without this binding,
            // panel.hide() (opacity -> 0) makes Qt stop rendering the panel
            // but leaves the compositor blurring that screen region
            // forever - confirmed live (2026-09-10): a panel shown once,
            // then correctly hidden by hideTimer, still had a visibly
            // blurred rectangle sitting on the desktop hours later, with no
            // panel content in it at all, because BackgroundBlur.active was
            // never set to false to make it call clearRegion().
            Binding {
                target: waylandBlurLoader.item
                property: "active"
                value: panel.opacity > 0
                when: waylandBlurLoader.item !== null
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

        // History browsing header - only visible while stepped back
        Row {
            id: historyHeader
            visible: panel.historyIndex >= 0
            x: 18
            y: 12
            width: parent.width - 36
            Text {
                text: "History " + (panel.historyIndex + 1) + " / " + panel.historyEntries.length
                    + (panel.historyEntries[panel.historyIndex] && panel.historyEntries[panel.historyIndex].question
                       ? "  ·  “" + panel.historyEntries[panel.historyIndex].question + "”" : "")
                color: Qt.rgba(1, 1, 1, 0.55)
                font.pixelSize: 11
                elide: Text.ElideRight
                width: parent.width
            }
        }

        Flickable {
            id: panelFlick
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: historyHeader.visible ? historyHeader.bottom : parent.top
            anchors.bottom: parent.bottom
            anchors.margins: 18
            anchors.topMargin: historyHeader.visible ? 4 : 18
            clip: true
            contentWidth: width
            contentHeight: panelText.implicitHeight
            boundsBehavior: Flickable.StopAtBounds

            Text {
                id: panelText
                width: panelFlick.width
                wrapMode: Text.WordWrap
                textFormat: Text.MarkdownText
                color: "#f5f3fa"
                font.pixelSize: 14
                font.letterSpacing: 0.1
                lineHeight: 1.3
                onLinkActivated: {}  // no browser to hand off to; just don't crash on a link click
            }
        }

        // thin scroll indicator - only shown when content overflows
        Rectangle {
            visible: panelFlick.contentHeight > panelFlick.height
            anchors.right: parent.right
            anchors.rightMargin: 6
            anchors.top: panelFlick.top
            width: 3
            radius: 1.5
            color: Qt.rgba(1, 1, 1, 0.25)
            height: Math.max(20, panelFlick.height * (panelFlick.height / panelFlick.contentHeight))
            y: panelFlick.y + (panelFlick.height - height) * (panelFlick.contentY / Math.max(1, panelFlick.contentHeight - panelFlick.height))
        }

        Timer {
            id: hideTimer
            interval: 25000
            onTriggered: panel.hide()
        }

        function show(text) {
            root.expand()
            panelText.text = text
            panelFlick.contentY = 0
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
        function showHistoryAt(idx) {
            if (historyEntries.length === 0) return
            root.expand()
            historyIndex = Math.max(0, Math.min(historyEntries.length - 1, idx))
            var entry = historyEntries[historyIndex]
            panelText.text = entry.answer
            panelFlick.contentY = 0
            opacity = 1
            root.hasSomethingToShow = true
            hideTimer.restart()  // each scroll pushes the deadline out, so active
                                  // browsing isn't interrupted, but it still
                                  // auto-hides ~25s after the last scroll instead
                                  // of staying open forever once you stop
            character.runJavaScript("setAvatarState('speaking')")
        }
        function leaveHistoryMode() {
            historyIndex = -1
        }
    }
}
