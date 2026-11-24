import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Token calculator: the total tokens Friday has pulled from its models
// (prompt + reply, from each provider's own usage report). Ticks up live
// while Friday works; click for the breakdown.
AbstractButton {
    id: meter
    objectName: "tokenMeter"
    readonly property var usage: backend.tokenUsage
    hoverEnabled: true
    focusPolicy: Qt.TabFocus
    implicitHeight: 30
    implicitWidth: meterRow.implicitWidth + 22
    onClicked: details.opened ? details.close() : details.open()
    Accessible.name: "Tokens used: " + usage.exact

    // A small bump whenever the total grows.
    property string _last: usage.total
    onUsageChanged: {
        if (usage.total !== _last && !usage.empty)
            bump.restart()
        _last = usage.total
    }
    SequentialAnimation {
        id: bump
        NumberAnimation { target: totalText; property: "scale"; to: 1.18; duration: 110; easing.type: Easing.OutCubic }
        NumberAnimation { target: totalText; property: "scale"; to: 1.0; duration: 260; easing.type: Easing.OutBack }
    }

    background: Rectangle {
        radius: height / 2
        color: meter.down ? Qt.darker(theme.hover, 1.1)
             : (meter.hovered || details.opened) ? theme.accent_hover : theme.chat
        border.width: 1
        border.color: meter.visualFocus ? theme.accent : theme.border
        Behavior on color { ColorAnimation { duration: 140 } }
    }
    contentItem: Item {
        RowLayout {
            id: meterRow
            anchors.centerIn: parent
            spacing: 7
            Text {   // Σ
                text: "\u03a3"
                color: theme.warn
                font.pixelSize: 13
                font.bold: true
            }
            Text {
                id: totalText
                objectName: "tokenTotal"
                text: meter.usage.total
                color: theme.fg_bright
                font.pixelSize: 12
                font.bold: true
                font.family: "Consolas"
            }
            Text {
                text: "tokens"
                color: theme.fg_dim
                font.pixelSize: 11
            }
            Rectangle { width: 1; height: 14; color: theme.border; visible: !meter.usage.empty }
            Text {
                visible: !meter.usage.empty
                text: "today " + meter.usage.today
                color: theme.fg_faint
                font.pixelSize: 10
            }
        }
    }

    // -- breakdown ---------------------------------------------------------
    Popup {
        id: details
        objectName: "tokenDetails"
        y: meter.height + 6
        x: meter.width - width
        width: 320
        padding: 14
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 150 }
                NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 180; easing.type: Easing.OutCubic }
            }
        }
        exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 110 } }
        background: Rectangle {
            radius: 10
            color: theme.panel
            border.width: 1
            border.color: theme.border
        }

        contentItem: ColumnLayout {
            spacing: 8
            Text {
                text: "Tokens pulled from your models"
                color: theme.fg_bright
                font.pixelSize: 13
                font.bold: true
            }
            Text {
                text: meter.usage.exact + " total" + (meter.usage.providerCount > 0
                      ? " from " + meter.usage.providerCount + " provider" + (meter.usage.providerCount === 1 ? "" : "s") : "")
                      + (meter.usage.since ? "  \u00b7  since " + meter.usage.since : "")
                color: theme.fg_dim
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            ColumnLayout {
                spacing: 3
                Layout.fillWidth: true
                Repeater {
                    model: [
                        ["Prompt (sent)", meter.usage.input],
                        ["Reply (received)", meter.usage.output],
                        ["Today", meter.usage.today],
                        ["This tab's models", meter.usage.tab],
                        ["Model calls", meter.usage.calls]
                    ]
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Text { text: modelData[0]; color: theme.fg_faint; font.pixelSize: 11; Layout.fillWidth: true }
                        Text { text: modelData[1]; color: theme.fg; font.pixelSize: 11; font.family: "Consolas" }
                    }
                }
            }
            // -- every provider, totalled ---------------------------------
            Rectangle { Layout.fillWidth: true; height: 1; color: theme.border_soft; visible: meter.usage.providerCount > 0 }
            Text {
                visible: meter.usage.providerCount > 0
                text: "By provider"
                color: theme.fg_dim
                font.pixelSize: 11
                font.bold: true
            }
            ListView {
                id: providerList
                objectName: "tokenProviders"
                visible: meter.usage.providerCount > 0
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, 168)
                clip: true
                interactive: contentHeight > height
                boundsBehavior: Flickable.StopAtBounds
                spacing: 5
                model: meter.usage.providers
                ScrollIndicator.vertical: ScrollIndicator {}
                delegate: Column {
                    required property var modelData
                    width: providerList.width
                    spacing: 2
                    RowLayout {
                        width: parent.width
                        spacing: 6
                        Text {
                            text: modelData.name
                            color: theme.fg
                            font.pixelSize: 11
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                        Text {
                            text: Math.round(modelData.share * 100) + "%"
                            color: theme.fg_faint
                            font.pixelSize: 10
                        }
                        Text { text: modelData.tokens; color: theme.fg; font.pixelSize: 11; font.family: "Consolas" }
                    }
                    Rectangle {          // share of the grand total
                        width: parent.width; height: 3; radius: 1.5
                        color: theme.border_soft
                        Rectangle {
                            width: Math.max(2, parent.width * modelData.share)
                            height: parent.height; radius: 1.5
                            color: theme.warn
                            opacity: 0.8
                            Behavior on width { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
                        }
                    }
                }
            }
            Rectangle {
                visible: meter.usage.providerCount > 1
                Layout.fillWidth: true; height: 1; color: theme.border_soft
            }
            RowLayout {
                visible: meter.usage.providerCount > 1
                Layout.fillWidth: true
                Text { text: "All providers"; color: theme.fg_bright; font.pixelSize: 11; font.bold: true; Layout.fillWidth: true }
                Text { text: meter.usage.exact; color: theme.fg_bright; font.pixelSize: 11; font.bold: true; font.family: "Consolas" }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: theme.border_soft; visible: meter.usage.top.length > 0 }
            Text {
                visible: meter.usage.top.length > 0
                text: "Biggest users"
                color: theme.fg_dim
                font.pixelSize: 11
                font.bold: true
            }
            Repeater {
                model: meter.usage.top
                delegate: RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 6
                    Text {
                        text: modelData.model || modelData.name
                        color: modelData.connected ? theme.fg : theme.fg_faint
                        font.pixelSize: 11
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                    Text { text: modelData.tokens; color: theme.fg; font.pixelSize: 11; font.family: "Consolas" }
                }
            }
            Text {
                visible: meter.usage.empty
                text: "Nothing yet -- the count starts with Friday's next model call."
                color: theme.fg_faint
                font.pixelSize: 11
                font.italic: true
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Text {
                visible: meter.usage.hasEstimated
                text: meter.usage.estimated + " estimated from text length (those replies didn't report usage)."
                color: theme.fg_faint
                font.pixelSize: 10
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                FButton {
                    objectName: "tokenReset"
                    text: "Reset count"
                    compact: true
                    variant: "ghost"
                    enabled: !meter.usage.empty
                    onClicked: backend.resetTokens()
                }
            }
        }
    }
}
