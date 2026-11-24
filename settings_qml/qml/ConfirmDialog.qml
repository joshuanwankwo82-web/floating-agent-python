import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Animated yes/no (or OK-only) dialog.  ask(title, message, onYes) / inform(title, message)
Popup {
    id: dlg
    property string title: ""
    property string message: ""
    property bool infoOnly: false
    property var onYes: null

    function ask(t, m, yes) { title = t; message = m; infoOnly = false; onYes = yes; open() }
    function inform(t, m) { title = t; message = m; infoOnly = true; onYes = null; open() }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(400, parent ? parent.width - 40 : 400)
    modal: true
    dim: true
    padding: 18
    transformOrigin: Item.Center

    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.45) }

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 160 }
            NumberAnimation { property: "scale"; from: 0.92; to: 1; duration: 240; easing.type: Easing.OutBack; easing.overshoot: 1.2 }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; to: 0; duration: 130 }
            NumberAnimation { property: "scale"; to: 0.96; duration: 130 }
        }
    }

    background: Rectangle { radius: 12; color: theme.panel; border.color: theme.border }

    contentItem: ColumnLayout {
        spacing: 10
        Text {
            text: dlg.title
            color: theme.fg_bright
            font.pixelSize: 14
            font.bold: true
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
        Text {
            text: dlg.message
            color: theme.fg_dim
            font.pixelSize: 12
            Layout.fillWidth: true
            wrapMode: Text.WordWrap
        }
        RowLayout {
            Layout.topMargin: 6
            Item { Layout.fillWidth: true }
            FButton {
                text: "Cancel"
                visible: !dlg.infoOnly
                onClicked: dlg.close()
            }
            FButton {
                text: dlg.infoOnly ? "OK" : "Yes"
                variant: dlg.infoOnly ? "primary" : "danger"
                onClicked: {
                    const cb = dlg.onYes
                    dlg.close()
                    if (cb) cb()
                }
            }
        }
    }
}
