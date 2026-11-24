import QtQuick
import QtQuick.Controls.Basic

// Friday-styled button. variant: "default" | "primary" | "secondary" | "ghost" | "danger"
Button {
    id: control
    property string variant: "default"
    property bool compact: false

    readonly property color baseColor: variant === "primary" ? theme.accent
                                     : variant === "ghost" ? "transparent"
                                     : theme.panel
    readonly property color hoverColor: variant === "primary" ? Qt.lighter(theme.accent, 1.08)
                                      : variant === "danger" ? Qt.rgba(0.83, 0.61, 0.62, 0.16)
                                      : theme.hover
    readonly property color textColor: variant === "primary" ? theme.editor
                                     : variant === "danger" ? theme.danger
                                     : theme.fg

    implicitHeight: compact ? 24 : 30
    leftPadding: compact ? 8 : 12
    rightPadding: compact ? 8 : 12
    font.pixelSize: compact ? 11 : 12
    font.bold: variant === "primary"
    hoverEnabled: true
    focusPolicy: Qt.TabFocus

    opacity: enabled ? 1.0 : 0.45
    Behavior on opacity { NumberAnimation { duration: 160 } }

    scale: down ? 0.96 : 1.0
    Behavior on scale { NumberAnimation { duration: 110; easing.type: Easing.OutCubic } }

    contentItem: Text {
        text: control.text
        font: control.font
        color: control.textColor
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    background: Rectangle {
        radius: control.compact ? 6 : 8
        color: control.down ? Qt.darker(control.hoverColor, 1.1)
             : control.hovered ? control.hoverColor : control.baseColor
        border.width: control.variant === "ghost" || control.variant === "primary" ? 0 : 1
        border.color: control.variant === "danger" ? Qt.rgba(0.83, 0.61, 0.62, 0.35)
                    : control.visualFocus ? theme.accent : theme.border
        Behavior on color { ColorAnimation { duration: 140 } }
        Behavior on border.color { ColorAnimation { duration: 140 } }
    }
}
