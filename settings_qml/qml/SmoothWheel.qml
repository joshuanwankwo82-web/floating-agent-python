import QtQuick

// Gives a Flickable/ListView eased mouse-wheel scrolling: each notch glides
// instead of jumping. Touchpads keep Qt's native pixel-precise scrolling.
// Also provides glideTo() for the Top/Bottom pills.
WheelHandler {
    id: wheel
    required property Flickable flick
    property real step: 1.1          // pixels per wheel unit (120 units = 1 notch)

    parent: flick
    target: null
    acceptedDevices: PointerDevice.Mouse
    orientation: Qt.Vertical

    readonly property real minY: flick.originY
    readonly property real maxY: Math.max(flick.originY,
                                          flick.originY + flick.contentHeight - flick.height)

    function clamp(v) { return Math.max(minY, Math.min(maxY, v)) }

    function glideTo(y, ms) {
        anim.stop()
        anim.duration = ms === undefined ? 420 : ms
        anim.to = clamp(y)
        anim.start()
    }

    onWheel: (event) => {
        const base = anim.running ? anim.to : flick.contentY
        anim.stop()
        anim.duration = 260
        anim.to = clamp(base - event.angleDelta.y * step)
        anim.start()
        event.accepted = true
    }

    property NumberAnimation anim: NumberAnimation {
        target: wheel.flick
        property: "contentY"
        easing.type: Easing.OutCubic
    }
}
