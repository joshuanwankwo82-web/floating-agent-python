import QtQuick
import QtQuick.Controls.Basic

// Small checkbox whose tick pops in with a spring.
CheckBox {
    id: box
    hoverEnabled: true
    padding: 0
    spacing: 6
    font.pixelSize: 12

    indicator: Rectangle {
        implicitWidth: 18
        implicitHeight: 18
        x: box.leftPadding
        y: (box.height - height) / 2
        radius: 5
        color: box.checked ? theme.accent : (box.hovered ? theme.hover : theme.input)
        border.width: box.checked ? 0 : 1
        border.color: theme.border
        Behavior on color { ColorAnimation { duration: 140 } }

        Text {
            anchors.centerIn: parent
            text: "\u2713"
            font.pixelSize: 13
            font.bold: true
            color: theme.editor
            scale: box.checked ? 1.0 : 0.2
            opacity: box.checked ? 1.0 : 0.0
            Behavior on scale { NumberAnimation { duration: 220; easing.type: Easing.OutBack } }
            Behavior on opacity { NumberAnimation { duration: 120 } }
        }
    }

    contentItem: Text {
        text: box.text
        visible: box.text.length > 0
        font: box.font
        color: theme.fg_dim
        verticalAlignment: Text.AlignVCenter
        leftPadding: box.indicator.width + box.spacing
    }
}
