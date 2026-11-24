import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Friday's Settings panel in Qt Quick: GPU-rendered and synced to the
// display's refresh rate. Inside Friday, `host` is the panel manager's
// adapter (settings_panel.SettingsPanel); standalone it is null.
ApplicationWindow {
    id: win
    width: 820
    height: 640
    minimumWidth: 600
    minimumHeight: 440
    visible: false
    title: "Settings"
    color: "transparent"
    // Qt.Tool = no taskbar button (like Friday's circle and the Tk panels).
    flags: Qt.Tool | Qt.FramelessWindowHint
    font.family: "Segoe UI"

    property string page: typeof initialPage === "string" ? initialPage : "keys"
    property bool isClosing: false
    readonly property bool managed: typeof host !== "undefined" && host !== null
    // Minimized panels become a pill under Friday's circle; standalone there
    // is nothing to restore from, so no minimize button.
    readonly property bool canMinimize: managed

    // Friday's active-panel glow (same rules as the other panels): the one
    // active panel pulses while Friday is idle and shows his state otherwise.
    property bool activePanel: false
    property string fridayState: "idle"
    property bool fridayError: false
    readonly property bool glowPulse: activePanel && fridayState === "idle"
    readonly property color statusColor:
        fridayState === "responding" && fridayError ? "#e74c3c"
        : (fridayState === "listening" || fridayState === "loading") ? "#3b82f6"
        : fridayState === "responding" ? "#2ecc71" : theme.accent

    onPageChanged: if (managed) host.pageShown(page)

    // Emitted once when the window really closes (not when it is hidden).
    signal finished()
    onClosing: (close) => {
        if (managed && !isClosing) {     // e.g. Alt+F4: let the manager close it
            close.accepted = false
            host.requestClose()
            return
        }
        finished()
    }

    function requestMinimize() { if (managed) host.requestMinimize(); else animatedHide() }
    function requestClose() { if (managed) host.requestClose(); else animatedClose() }

    palette.window: theme.panel
    palette.windowText: theme.fg
    palette.base: theme.input
    palette.text: theme.fg
    palette.button: theme.panel
    palette.buttonText: theme.fg
    palette.highlight: theme.hover
    palette.highlightedText: theme.fg_bright
    palette.placeholderText: theme.fg_faint
    palette.mid: theme.border
    palette.dark: theme.border
    palette.light: theme.hover

    function animatedClose() {
        if (isClosing) return
        isClosing = true
        hideAnim.stop()
        closeAnim.start()
    }

    // "Minimize": fade out and hide. No taskbar button, so nothing to
    // minimize into -- reveal() (from the menu item) brings it back.
    function animatedHide() {
        if (isClosing || !visible) return
        hideAnim.start()
    }

    function reveal() {
        if (isClosing) return
        hideAnim.stop()
        if (!visible) {
            frame.opacity = 0
            frame.scale = 0.97
            visible = true
            openAnim.start()
        }
        raise()
        requestActivate()
    }

    Component.onCompleted: {
        if (typeof startGeometry !== "undefined" && startGeometry.length === 4) {
            x = startGeometry[0]; y = startGeometry[1]
            width = startGeometry[2]; height = startGeometry[3]
        }
        visible = true
        openAnim.start()
        requestActivate()
    }

    // -- window chrome ---------------------------------------------------------
    Rectangle {
        id: frame
        anchors.fill: parent
        radius: 12
        color: theme.editor
        border.color: theme.border
        border.width: 1
        clip: true
        opacity: 0
        scale: 0.97

        ParallelAnimation {
            id: openAnim
            OpacityAnimator { target: frame; from: 0; to: 1; duration: 200; easing.type: Easing.OutCubic }
            ScaleAnimator { target: frame; from: 0.97; to: 1; duration: 260; easing.type: Easing.OutCubic }
        }
        SequentialAnimation {
            id: closeAnim
            ParallelAnimation {
                OpacityAnimator { target: frame; to: 0; duration: 150; easing.type: Easing.InCubic }
                ScaleAnimator { target: frame; to: 0.97; duration: 150; easing.type: Easing.InCubic }
            }
            ScriptAction { script: win.close() }
        }
        SequentialAnimation {
            id: hideAnim
            ParallelAnimation {
                OpacityAnimator { target: frame; to: 0; duration: 150; easing.type: Easing.InCubic }
                ScaleAnimator { target: frame; to: 0.97; duration: 150; easing.type: Easing.InCubic }
            }
            ScriptAction { script: win.hide() }
        }

        // Titlebar
        Rectangle {
            id: titlebar
            anchors { left: parent.left; right: parent.right; top: parent.top }
            height: 38
            color: theme.panel
            radius: 12
            Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom } height: 12; color: theme.panel }
            Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom } height: 1; color: theme.border_soft }

            DragHandler {
                target: null
                onActiveChanged: if (active) win.startSystemMove()
            }
            TapHandler {
                onDoubleTapped: win.visibility === Window.Maximized ? win.showNormal() : win.showMaximized()
            }

            Text {
                id: titleText
                anchors { left: parent.left; leftMargin: 14; verticalCenter: parent.verticalCenter }
                text: "Settings \u2014 AI Models"
                color: theme.fg
                font.pixelSize: 12
                font.bold: true
            }

            Row {
                anchors { right: parent.right; rightMargin: 6; verticalCenter: parent.verticalCenter }
                spacing: 2
                ChromeButton { glyph: "\u2013"; visible: win.canMinimize; onClicked: win.requestMinimize() }
                ChromeButton { glyph: "\u2715"; danger: true; onClicked: win.requestClose() }
            }
        }

        // Segmented page switch with a sliding highlight.
        Rectangle {
            id: seg
            anchors { top: titlebar.bottom; topMargin: 10; horizontalCenter: parent.horizontalCenter }
            width: 260; height: 34; radius: 17
            color: theme.panel
            border.color: theme.border

            Rectangle {
                id: slider
                objectName: "pageSlider"
                y: 3; height: parent.height - 6
                width: parent.width / 2 - 3
                x: win.page === "keys" ? 3 : parent.width / 2
                radius: 14
                color: theme.accent
                Behavior on x { NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
            }
            Row {
                anchors.fill: parent
                Repeater {
                    model: [["keys", "Keys & Connect"], ["profile", "Profile"]]
                    delegate: Item {
                        required property var modelData
                        width: seg.width / 2; height: seg.height
                        Text {
                            anchors.centerIn: parent
                            text: modelData[1]
                            font.pixelSize: 12
                            font.bold: true
                            color: win.page === modelData[0] ? theme.editor : theme.fg_dim
                            Behavior on color { ColorAnimation { duration: 220 } }
                        }
                        TapHandler { onTapped: win.page = modelData[0] }
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
            }
        }

        // Pages: cross-fade with a short slide in the direction of travel.
        Item {
            id: pages
            anchors { top: seg.bottom; topMargin: 10; left: parent.left; right: parent.right; bottom: parent.bottom }
            clip: true

            KeysPage {
                id: keysPage
                objectName: "keysPage"
                width: pages.width; height: pages.height
                fieldDialog: fieldDialog
                confirm: confirm
                readonly property bool shown: win.page === "keys"
                x: shown ? 0 : -48
                opacity: shown ? 1 : 0
                visible: opacity > 0.01
                enabled: shown
                Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                Behavior on opacity { NumberAnimation { duration: 220 } }
            }
            ProfilePage {
                id: profilePage
                width: pages.width; height: pages.height
                confirm: confirm
                readonly property bool shown: win.page === "profile"
                x: shown ? 0 : 48
                opacity: shown ? 1 : 0
                visible: opacity > 0.01
                enabled: shown
                Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                Behavior on opacity { NumberAnimation { duration: 220 } }
            }
        }

        // Resize grip (bottom-right) and edges.
        ResizeEdge { anchors { right: parent.right; top: parent.top; bottom: parent.bottom; topMargin: 38 } width: 5; edges: Qt.RightEdge; cursor: Qt.SizeHorCursor }
        ResizeEdge { anchors { left: parent.left; top: parent.top; bottom: parent.bottom; topMargin: 38 } width: 5; edges: Qt.LeftEdge; cursor: Qt.SizeHorCursor }
        ResizeEdge { anchors { left: parent.left; right: parent.right; bottom: parent.bottom } height: 5; edges: Qt.BottomEdge; cursor: Qt.SizeVerCursor }
        ResizeEdge { anchors { right: parent.right; bottom: parent.bottom } width: 14; height: 14; edges: Qt.RightEdge | Qt.BottomEdge; cursor: Qt.SizeFDiagCursor }

        // Active-panel glow ring, drawn over everything. The pulse runs on
        // the render thread (OpacityAnimator).
        Rectangle {
            id: glowRing
            objectName: "glowRing"
            anchors.fill: parent
            z: 1000
            radius: frame.radius
            color: "transparent"
            border.width: 2
            border.color: win.statusColor
            visible: win.activePanel
            Behavior on border.color { ColorAnimation { duration: 220 } }

            SequentialAnimation {
                id: pulse
                running: win.glowPulse && glowRing.visible
                loops: Animation.Infinite
                OpacityAnimator { target: glowRing; from: 1; to: 0.45; duration: 1300; easing.type: Easing.InOutSine }
                OpacityAnimator { target: glowRing; from: 0.45; to: 1; duration: 1300; easing.type: Easing.InOutSine }
                onRunningChanged: if (!running) glowRing.opacity = 1
            }
        }
    }

    FieldDialog { id: fieldDialog; objectName: "fieldDialog"; notify: (t, m) => confirm.inform(t, m) }
    // e.g. "Saved under Image" when a fetched model belongs to another tab
    Connections { target: backend; function onNotice(title, message) { confirm.inform(title, message) } }
    ConfirmDialog { id: confirm; objectName: "confirm" }

    component ChromeButton: Rectangle {
        id: cb
        property string glyph
        property bool danger: false
        signal clicked()
        width: 34; height: 26; radius: 6
        color: cbHover.hovered ? (danger ? Qt.rgba(0.83, 0.61, 0.62, 0.22) : theme.hover) : "transparent"
        Behavior on color { ColorAnimation { duration: 120 } }
        Text { anchors.centerIn: parent; text: cb.glyph; color: cbHover.hovered && cb.danger ? theme.danger : theme.fg_dim; font.pixelSize: 12 }
        HoverHandler { id: cbHover }
        TapHandler { onTapped: cb.clicked() }
    }

    component ResizeEdge: Item {
        property int edges
        property int cursor
        HoverHandler { cursorShape: parent.cursor }
        DragHandler {
            target: null
            onActiveChanged: if (active) win.startSystemResize(parent.edges)
        }
    }
}
