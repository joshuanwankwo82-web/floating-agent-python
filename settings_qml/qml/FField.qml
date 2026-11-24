import QtQuick
import QtQuick.Controls.Basic

// Friday-styled single-line text field with an animated focus ring.
// secret: true masks the text; set reveal to show it.
TextField {
    id: field
    property bool secret: false
    property bool reveal: false

    echoMode: secret && !reveal ? TextInput.Password : TextInput.Normal
    passwordCharacter: "\u2022"
    color: theme.fg
    placeholderTextColor: theme.fg_faint
    selectionColor: theme.hover
    selectedTextColor: theme.fg_bright
    font.pixelSize: 12
    implicitHeight: 32
    leftPadding: 10
    rightPadding: 10
    selectByMouse: true

    background: Rectangle {
        radius: 8
        color: theme.input
        border.width: 1
        border.color: field.activeFocus ? theme.accent : (field.hovered ? Qt.lighter(theme.border, 1.25) : theme.border)
        Behavior on border.color { ColorAnimation { duration: 160 } }
    }
}
