import QtQuick

// Cursor-edge auto-scroll (user-directed; carried over from the Tk panel's
// touchpad_scroll.attach_edge_autoscroll): rest the pointer within `zone`
// px of the list's top or bottom edge and it glides that way -- slowly at
// the rim, faster deeper in (depth^ramp), up to maxSpeed px/s. Advances
// once per displayed frame, so the glide is as smooth as the screen.
Item {
    id: edge
    width: 0; height: 0     // logic only; the hover handler lives on the view
    required property Flickable flick
    property real zone: 64
    property real maxSpeed: 250          // px/s (the Tk panel's 5 px per 20 ms)
    property real ramp: 2.0
    property real velocity: 0            // px/s, signed; read by tests
    property real pointerY: -1           // last pointer y over the view (tests)

    HoverHandler {
        id: hover
        parent: edge.flick
        onPointChanged: edge.update()
        onHoveredChanged: edge.update()
    }

    function update() {
        if (!hover.hovered || flick.height <= 0) { velocity = 0; return }
        // Map from the window, not the handler's own item: views reparent
        // handlers onto their scrolling content, whose y moves with it.
        const y = flick.mapFromItem(null, hover.point.scenePosition.x,
                                    hover.point.scenePosition.y).y
        pointerY = y
        if (y < 0 || y > flick.height) { velocity = 0; return }
        const h = flick.height
        if (y > h - zone)
            velocity = Math.pow(Math.min(1, (y - (h - zone)) / zone), ramp) * maxSpeed
        else if (y < zone)
            velocity = -Math.pow(Math.min(1, (zone - y) / zone), ramp) * maxSpeed
        else
            velocity = 0
    }

    FrameAnimation {
        running: edge.velocity !== 0 && !edge.flick.moving
        onTriggered: {
            const f = edge.flick
            const maxY = Math.max(f.originY, f.originY + f.contentHeight - f.height)
            const next = Math.max(f.originY,
                                  Math.min(maxY, f.contentY + edge.velocity * frameTime))
            if (next !== f.contentY)
                f.contentY = next
        }
    }
}
