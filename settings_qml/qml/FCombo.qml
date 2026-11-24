import QtQuick
import QtQuick.Controls.Basic

// Friday-styled combo box; its list unfolds with a short height/opacity animation.
ComboBox {
    id: combo
    font.pixelSize: 12
    implicitHeight: 32
    hoverEnabled: true

    contentItem: Item {
        implicitWidth: 120
        TextField {
            id: editor
            visible: combo.editable
            anchors.fill: parent
            anchors.rightMargin: 26
            text: combo.editText
            onTextEdited: combo.editText = text
            font: combo.font
            color: theme.fg
            placeholderTextColor: theme.fg_faint
            selectByMouse: true
            leftPadding: 10
            verticalAlignment: Text.AlignVCenter
            background: null
        }
        Text {
            visible: !combo.editable
            anchors.fill: parent
            anchors.rightMargin: 26
            leftPadding: 10
            text: combo.displayText
            font: combo.font
            color: theme.fg
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
    }

    indicator: Text {
        x: combo.width - width - 10
        y: (combo.height - height) / 2
        text: "\u25be"
        color: theme.fg_dim
        font.pixelSize: 12
        rotation: combo.popup.visible ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }
    }

    background: Rectangle {
        radius: 8
        color: theme.input
        border.width: 1
        border.color: combo.activeFocus || combo.popup.visible ? theme.accent
                    : (combo.hovered ? Qt.lighter(theme.border, 1.25) : theme.border)
        Behavior on border.color { ColorAnimation { duration: 160 } }
    }

    delegate: ItemDelegate {
        id: option
        required property int index
        required property var modelData
        width: ListView.view ? ListView.view.width : combo.width
        height: 28
        hoverEnabled: true
        contentItem: Text {
            text: option.modelData
            color: combo.currentIndex === option.index ? theme.fg_bright : theme.fg
            font.pixelSize: 12
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: 6
            color: option.hovered ? theme.hover : "transparent"
            Behavior on color { ColorAnimation { duration: 100 } }
        }
    }

    popup: Popup {
        y: combo.height + 4
        width: combo.width
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 300)
        padding: 4
        transformOrigin: Item.Top

        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 140 }
                NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic }
            }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; to: 0; duration: 110 }
        }

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: combo.popup.visible ? combo.delegateModel : null
            currentIndex: combo.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
            ScrollIndicator.vertical: ScrollIndicator {}
        }

        background: Rectangle {
            radius: 8
            color: theme.panel
            border.color: theme.border
        }
    }
}
