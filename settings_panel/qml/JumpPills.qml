import QtQuick
import QtQuick.Layouts

// Floating "Top" / "Bottom" pills that fade in only when useful.
Row {
    id: pills
    required property Flickable flick
    required property SmoothWheel wheel
    spacing: 6

    readonly property bool canGoUp: flick.contentY > flick.originY + 40
    readonly property bool canGoDown: flick.contentY < wheel.maxY - 40

    FButton {
        objectName: "jumpTop"
        text: "\u2191 Top"
        compact: true
        variant: "default"
        opacity: pills.canGoUp ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 200 } }
        onClicked: pills.wheel.glideTo(pills.wheel.minY)
    }
    FButton {
        objectName: "jumpBottom"
        text: "\u2193 Bottom"
        compact: true
        opacity: pills.canGoDown ? 1 : 0
        visible: opacity > 0.01
        Behavior on opacity { NumberAnimation { duration: 200 } }
        onClicked: pills.wheel.glideTo(pills.wheel.maxY)
    }
}
