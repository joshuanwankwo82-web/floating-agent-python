import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// One model/service row: select box, name + quality score + status, model id
// + tokens used + masked key, the latest check's reason, and Check / Edit /
// Remove. Rows arrive ranked best first (backend._rank_key).
Rectangle {
    id: row
    required property int index
    required property string key
    required property string name
    required property string modelName
    required property string maskedKey
    required property string status
    required property string detail
    required property bool selected
    required property string score
    required property string scoreSource
    required property bool scoreEstimated
    required property string tokens

    signal editRequested(string key)
    signal removeRequested(string key)

    readonly property color statusColor: status === "connected" ? theme.success
                                       : status === "error" ? theme.danger
                                       : status === "checking" ? theme.warn
                                       : theme.fg_faint
    readonly property string statusText: status === "connected" ? "\u2713 Connected"
                                       : status === "error" ? "\u2715 Not connected"
                                       : status === "checking" ? "Checking\u2026"
                                       : "\u25cb Not checked yet"

    implicitHeight: layout.implicitHeight + 14
    radius: 9
    color: hover.hovered ? Qt.lighter(theme.chat, 1.35) : theme.chat
    border.width: 1
    border.color: selected ? Qt.rgba(0.78, 0.76, 0.74, 0.45) : theme.border_soft
    Behavior on border.color { ColorAnimation { duration: 160 } }
    Behavior on color { ColorAnimation { duration: 150 } }
    Behavior on implicitHeight { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

    HoverHandler { id: hover }

    // Brief glow in the result colour when a check finishes (render-thread animators).
    Rectangle {
        id: glow
        anchors.fill: parent
        radius: row.radius
        color: "transparent"
        border.width: 1.5
        border.color: row.statusColor
        opacity: 0
    }
    SequentialAnimation {
        id: flash
        OpacityAnimator { target: glow; from: 0; to: 1; duration: 160 }
        PauseAnimation { duration: 380 }
        OpacityAnimator { target: glow; to: 0; duration: 650 }
    }
    property string _lastStatus: status
    onStatusChanged: {
        if (_lastStatus === "checking" && (status === "connected" || status === "error"))
            flash.restart()
        _lastStatus = status
    }

    RowLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: 7
        anchors.leftMargin: 10
        spacing: 10

        FCheck {
            checked: row.selected
            onToggled: backend.setSelected(row.key, checked)
            Layout.alignment: Qt.AlignVCenter
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2

            RowLayout {
                Layout.fillWidth: true
                spacing: 7
                Rectangle {
                    width: 8; height: 8; radius: 4
                    color: row.statusColor
                    Behavior on color { ColorAnimation { duration: 260 } }
                    Layout.alignment: Qt.AlignVCenter
                }
                Text {
                    text: row.name
                    color: theme.fg
                    font.pixelSize: 12
                    font.bold: true
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
                Rectangle {   // quality score badge
                    objectName: "scoreBadge"
                    visible: row.score.length > 0
                    implicitWidth: scoreText.implicitWidth + 12
                    implicitHeight: 17
                    radius: 8.5
                    // Estimates (no published benchmark) read quieter than measured scores.
                    color: row.scoreEstimated ? "transparent" : Qt.rgba(0.77, 0.71, 0.6, 0.14)
                    border.width: 1
                    border.color: Qt.rgba(0.77, 0.71, 0.6, row.scoreEstimated ? 0.25 : 0.35)
                    Layout.alignment: Qt.AlignVCenter
                    Text {
                        id: scoreText
                        anchors.centerIn: parent
                        text: "\u2605 " + row.score
                        color: row.scoreEstimated ? theme.fg_faint : theme.warn
                        font.pixelSize: 10
                        font.bold: !row.scoreEstimated
                    }
                    HoverHandler { id: scoreHover }
                    ToolTip.visible: scoreHover.hovered
                    ToolTip.delay: 350
                    ToolTip.text: (row.scoreEstimated ? "Estimated quality score (" : "Quality score: ")
                                  + row.scoreSource + (row.scoreEstimated ? ")" : "")
                                  + ". Higher is better; the list is ranked by it."
                }
                Item {
                    implicitWidth: 12; implicitHeight: 12
                    visible: row.status === "checking"
                    Layout.alignment: Qt.AlignVCenter
                    // Runs on the render thread: keeps spinning even while Python is busy.
                    Rectangle {
                        id: spinner
                        anchors.fill: parent
                        radius: 6
                        color: "transparent"
                        border.width: 2
                        border.color: Qt.rgba(0.77, 0.71, 0.6, 0.25)
                        Rectangle {
                            width: 4; height: 4; radius: 2
                            color: theme.warn
                            x: parent.width / 2 - 2; y: -1
                        }
                        RotationAnimator on rotation {
                            from: 0; to: 360; duration: 900
                            loops: Animation.Infinite
                            running: row.status === "checking"
                        }
                    }
                }
                Text {
                    text: row.statusText
                    color: row.statusColor
                    font.pixelSize: 11
                    font.bold: true
                    Behavior on color { ColorAnimation { duration: 260 } }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    text: row.modelName
                    color: theme.fg_dim
                    font.pixelSize: 11
                    elide: Text.ElideMiddle
                    Layout.fillWidth: true
                }
                Text {
                    objectName: "rowTokens"
                    visible: row.tokens.length > 0
                    text: "\u03a3 " + row.tokens + " tokens"
                    color: theme.fg_dim
                    font.pixelSize: 10
                }
                Text {
                    text: row.maskedKey
                    color: theme.fg_faint
                    font.pixelSize: 10
                    font.family: "Consolas"
                }
            }

            Text {
                id: detailText
                text: row.detail.length > 150 ? row.detail.slice(0, 147) + "..." : row.detail
                color: row.status === "connected" ? theme.success
                     : row.status === "error" ? theme.danger : theme.fg_faint
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.preferredHeight: text.length ? implicitHeight : 0
                opacity: text.length ? 1 : 0
                clip: true
                Behavior on opacity { NumberAnimation { duration: 220 } }
            }
        }

        RowLayout {
            spacing: 4
            Layout.alignment: Qt.AlignVCenter
            FButton {
                text: "Check"
                compact: true
                enabled: row.status !== "checking"
                onClicked: backend.check(row.key)
            }
            FButton {
                text: "Edit"
                compact: true
                onClicked: row.editRequested(row.key)
            }
            FButton {
                text: "Remove"
                compact: true
                variant: "danger"
                onClicked: row.removeRequested(row.key)
            }
        }
    }
}
