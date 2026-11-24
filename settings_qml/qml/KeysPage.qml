import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// "Keys & Connect": every configured model/service with a live status.
Item {
    id: page
    required property FieldDialog fieldDialog
    required property ConfirmDialog confirm

    readonly property int contentWidth: Math.min(width - 36, 1280)

    // -- subheader ---------------------------------------------------------
    Rectangle {
        id: sub
        anchors { left: parent.left; right: parent.right; top: parent.top }
        height: subRow.implicitHeight + 14
        color: theme.panel
        RowLayout {
            id: subRow
            anchors { fill: parent; leftMargin: 14; rightMargin: 12 }
            Text {
                text: "Keys are masked. Check performs a real API call. IDE chat uses Coding; Friday chat uses Voice. Act Script uses its named faculties without cross-section fallback."
                color: theme.fg_dim
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            FButton { text: "Check all"; variant: "primary"; onClicked: backend.checkAll() }
        }
    }

    // -- error state -------------------------------------------------------
    Column {
        visible: backend.loadError.length > 0
        anchors { top: sub.bottom; left: parent.left; right: parent.right; margins: 18 }
        spacing: 6
        Text { text: "Settings couldn't fully load."; color: theme.danger; font.pixelSize: 13; font.bold: true }
        Text { text: backend.loadError; color: theme.fg_dim; font.pixelSize: 11; wrapMode: Text.WordWrap; width: parent.width }
    }

    // -- the list ------------------------------------------------------------
    ListView {
        id: list
        visible: backend.loadError.length === 0
        anchors { top: sub.bottom; bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
        anchors.topMargin: 10
        width: page.contentWidth
        clip: true
        spacing: 6
        model: backend.connections
        boundsBehavior: Flickable.StopAtBounds
        reuseItems: false
        cacheBuffer: 600

        SmoothWheel { id: wheel; flick: list }
        ScrollIndicator.vertical: ScrollIndicator {}

        header: Column {
            width: list.width
            spacing: 8
            bottomPadding: 8

            RowLayout {
                width: parent.width
                Text {
                    text: "Connected models & services"
                    color: theme.fg_bright
                    font.pixelSize: 14
                    font.bold: true
                    Layout.fillWidth: true
                }
                FButton { text: "+ Add image provider"; onClicked: page.fieldDialog.openAdd("Image Generation", "Add image provider") }
                FButton { text: "+ Add model"; onClicked: page.fieldDialog.openAdd("Thinking", "Add model") }
            }
            Text {
                width: parent.width
                text: "Friday tries task-matched chat models first, then other compatible chat models. Search, image, embedding, and local services are used only for supported tasks; video generation remains local."
                color: theme.fg_faint
                font.pixelSize: 11
                wrapMode: Text.WordWrap
            }
            RowLayout {
                width: parent.width
                spacing: 8
                StatPill { label: "Connected"; value: backend.connectedCount; tint: theme.success }
                StatPill { label: "Not connected"; value: backend.notConnectedCount; tint: theme.danger }
                StatPill { label: "Not checked"; value: backend.notCheckedCount; tint: theme.fg_faint }
                Item { Layout.fillWidth: true }
                FButton {
                    text: "Remove not connected (" + backend.notConnectedCount + ")"
                    variant: "danger"
                    enabled: backend.notConnectedCount > 0
                    onClicked: page.confirm.ask("Remove disconnected models",
                        "Remove " + backend.notConnectedCount + " model connection"
                        + (backend.notConnectedCount === 1 ? "" : "s")
                        + " whose latest check failed? Models that haven't been checked are kept.",
                        () => backend.removeDisconnected())
                }
            }
        }

        delegate: ConnectionRow {
            width: list.width
            onEditRequested: (key) => page.fieldDialog.openEdit(key)
            onRemoveRequested: (key) => page.confirm.ask("Remove model",
                "Remove \"" + backend.nameOf(key) + "\"?", () => backend.removeOne(key))
        }

        footer: Column {
            width: list.width
            topPadding: 8
            bottomPadding: 60
            spacing: 6
            Text {
                visible: list.count === 0
                text: "No connections added yet."
                color: theme.fg_faint
                font.pixelSize: 12
                font.italic: true
            }
            RowLayout {
                width: parent.width
                spacing: 4
                FButton { text: "All"; compact: true; variant: "ghost"; onClicked: backend.selectAll(true) }
                FButton { text: "None"; compact: true; variant: "ghost"; onClicked: backend.selectAll(false) }
                Item { Layout.fillWidth: true }
                FButton {
                    text: "Remove selected (" + backend.selectedCount + ")"
                    compact: true
                    variant: "danger"
                    enabled: backend.selectedCount > 0
                    onClicked: page.confirm.ask("Remove connections",
                        "Remove " + backend.selectedCount + " selected connection"
                        + (backend.selectedCount === 1 ? "" : "s") + "?",
                        () => backend.removeSelected())
                }
            }
        }

        // New rows rise and fade in; removed rows shrink away; the rest glide.
        add: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 260 }
                NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 300; easing.type: Easing.OutCubic }
            }
        }
        remove: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; to: 0; duration: 200 }
                NumberAnimation { property: "scale"; to: 0.94; duration: 220; easing.type: Easing.InCubic }
            }
        }
        displaced: Transition {
            NumberAnimation { properties: "x,y"; duration: 280; easing.type: Easing.OutCubic }
            NumberAnimation { property: "opacity"; to: 1; duration: 120 }
            NumberAnimation { property: "scale"; to: 1; duration: 120 }
        }
    }

    JumpPills {
        anchors { right: parent.right; bottom: parent.bottom; margins: 14 }
        flick: list
        wheel: wheel
    }

    component StatPill: Rectangle {
        property string label
        property int value
        property color tint
        implicitHeight: 24
        implicitWidth: pillText.implicitWidth + 20
        radius: 12
        color: Qt.rgba(tint.r, tint.g, tint.b, 0.12)
        border.color: Qt.rgba(tint.r, tint.g, tint.b, 0.3)
        Text {
            id: pillText
            anchors.centerIn: parent
            text: parent.label + "  " + parent.value
            color: parent.tint
            font.pixelSize: 11
            font.bold: true
        }
        // Count changes give a tiny pop.
        onValueChanged: pop.restart()
        SequentialAnimation {
            id: pop
            ScaleAnimator { target: pillText; to: 1.15; duration: 110; easing.type: Easing.OutCubic }
            ScaleAnimator { target: pillText; to: 1.0; duration: 220; easing.type: Easing.OutBack }
        }
    }
}
