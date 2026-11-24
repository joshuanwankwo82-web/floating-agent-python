import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// "+ Add model" / "Edit model" form: paste any key and the provider is
// recognised (provider_autodetect), its base URL filled in, and its real
// model list can be fetched.
Popup {
    id: dlg
    property string dialogTitle: "Add model"
    property string faculty: "Thinking"
    property string editKey: ""
    property string apiStyle: ""
    property bool fetching: false
    property int bulkCount: 0
    property var notify: null          // function(title, message)
    property bool _seeding: false

    function openAdd(fac, title) {
        _seeding = true
        editKey = ""; faculty = fac; dialogTitle = title
        apiStyle = ""; bulkCount = 0; fetching = false
        providerBox.currentIndex = 0
        nameField.text = ""; modelBox.model = []; modelBox.editText = ""
        baseField.text = ""; keyField.text = ""; keyField.reveal = false
        detectLabel.text = ""; hintLabel.text = ""; errorLabel.text = ""
        _seeding = false
        open()
        nameField.forceActiveFocus()
    }

    function openEdit(key) {
        const e = backend.entryForEdit(key)
        if (!e.faculty) return
        _seeding = true
        editKey = key; faculty = e.faculty; dialogTitle = "Edit model"
        apiStyle = e.api_style || ""; bulkCount = 0; fetching = false
        providerBox.currentIndex = 0
        nameField.text = e.name; modelBox.model = []; modelBox.editText = e.model
        baseField.text = e.base_url; keyField.text = e.api_key; keyField.reveal = false
        detectLabel.text = e.note; hintLabel.text = e.model_hint; errorLabel.text = ""
        _seeding = false
        open()
    }

    function applyHint(h) {
        if (h.api_style !== undefined) apiStyle = h.api_style
        if (h.base_url !== undefined && h.base_url !== null) baseField.text = h.base_url
        if (h.note !== undefined) detectLabel.text = h.note
        if (h.model_hint !== undefined && h.model_hint !== "") hintLabel.text = h.model_hint
        else if (h.base_url) hintLabel.text = backend.modelHintFor(baseField.text)
    }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(500, parent ? parent.width - 40 : 500)
    height: Math.min(content.implicitHeight + topPadding + bottomPadding,
                     parent ? parent.height - 40 : 640)
    modal: true
    dim: true
    padding: 18
    closePolicy: Popup.CloseOnEscape
    transformOrigin: Item.Center

    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.45) }

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 170 }
            NumberAnimation { property: "scale"; from: 0.93; to: 1; duration: 260; easing.type: Easing.OutBack; easing.overshoot: 1.1 }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; to: 0; duration: 140 }
            NumberAnimation { property: "scale"; to: 0.96; duration: 140 }
        }
    }

    background: Rectangle { radius: 12; color: theme.panel; border.color: theme.border }

    Connections {
        target: backend
        function onFetchFinished(result) {
            if (!dlg.visible) return
            dlg.fetching = false
            const snap = result.snapshot
            if (snap.base_url !== baseField.text.trim() || snap.api_key !== keyField.text.trim()
                    || snap.api_style !== (dlg.apiStyle || "")) {
                dlg.bulkCount = 0
                hintLabel.text = "Connection settings changed while the catalog was loading. Fetch models again before adding them."
                return
            }
            hintLabel.text = result.note
            dlg.bulkCount = result.bulkCount
            if (result.ok && result.values.length) {
                const keep = modelBox.editText
                modelBox.model = result.values
                modelBox.editText = keep.trim().length ? keep : result.values[0]
            }
        }
    }

    contentItem: Flickable {
        id: scroller
        clip: true
        contentHeight: content.implicitHeight
        boundsBehavior: Flickable.StopAtBounds
        SmoothWheel { id: dlgWheel; flick: scroller }

        ColumnLayout {
            id: content
            width: scroller.width
            spacing: 4

            Text {
                text: dlg.dialogTitle
                color: theme.fg_bright
                font.pixelSize: 15
                font.bold: true
                Layout.bottomMargin: 6
            }

            Text { text: "Provider (pick one, or paste any key below and let it be detected)"; color: theme.fg_dim; font.pixelSize: 11; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            FCombo {
                id: providerBox
                Layout.fillWidth: true
                model: backend.providerNames
                onActivated: (i) => { if (i > 0) dlg.applyHint(backend.pickPreset(currentText)) }
            }
            Text {
                id: detectLabel
                color: theme.fg_dim
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.preferredHeight: text.length ? implicitHeight : 0
                opacity: text.length ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 200 } }
                Behavior on Layout.preferredHeight { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            }

            Text { text: "Display name (e.g. \"GLM @ Z.ai\")"; color: theme.fg_dim; font.pixelSize: 11; Layout.topMargin: 8 }
            FField { id: nameField; objectName: "nameField"; Layout.fillWidth: true }

            Text { text: "Model"; color: theme.fg_dim; font.pixelSize: 11; Layout.topMargin: 8 }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                FCombo {
                    id: modelBox
                    editable: true
                    Layout.fillWidth: true
                    model: []
                }
                FButton {
                    text: dlg.fetching ? "Fetching\u2026" : "Fetch models"
                    enabled: !dlg.fetching
                    onClicked: {
                        if (!baseField.text.trim().length) {
                            hintLabel.text = "Fill (or pick) the provider's base URL first, then fetch its model list."
                            return
                        }
                        dlg.fetching = true
                        dlg.bulkCount = 0
                        backend.fetchModels(baseField.text, keyField.text, dlg.apiStyle)
                    }
                }
            }
            Text {
                id: hintLabel
                color: theme.fg_dim
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.preferredHeight: text.length ? implicitHeight : 0
                opacity: text.length ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 200 } }
                Behavior on Layout.preferredHeight { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            }
            FButton {
                visible: dlg.faculty !== "Image Generation"
                text: dlg.bulkCount ? "Add all FREE + TRIAL models (" + dlg.bulkCount + ")" : "Add all FREE + TRIAL models"
                variant: "secondary"
                enabled: dlg.bulkCount > 0
                Layout.fillWidth: true
                Layout.topMargin: 6
                onClicked: {
                    const r = backend.addFreeTrial(dlg.faculty, nameField.text, keyField.text,
                                                   baseField.text, dlg.apiStyle)
                    if (!r.ok) { errorLabel.text = r.message; return }
                    dlg.close()
                    if (dlg.notify) dlg.notify("Models added", r.message)
                }
            }
            Text {
                visible: dlg.faculty !== "Image Generation"
                text: "Adds one unchecked row per provider-classified free or trial model. No generation checks are run."
                color: theme.fg_faint
                font.pixelSize: 10
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            Text { text: "API base URL (auto-filled for known providers)"; color: theme.fg_dim; font.pixelSize: 11; Layout.topMargin: 8 }
            FField {
                id: baseField
                Layout.fillWidth: true
                onTextChanged: {
                    if (dlg._seeding) return
                    backend.invalidateBulk()
                    dlg.bulkCount = 0
                }
            }

            Text { text: "API key (any format \u2014 free or paid; leave empty for a local server)"; color: theme.fg_dim; font.pixelSize: 11; Layout.topMargin: 8; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                FField {
                    id: keyField
                    objectName: "keyField"
                    secret: true
                    Layout.fillWidth: true
                    onTextChanged: {
                        if (dlg._seeding) return
                        dlg.bulkCount = 0
                        dlg.applyHint(backend.keyChanged(text, baseField.text))
                    }
                }
                FCheck {
                    text: "Show"
                    checked: keyField.reveal
                    onToggled: keyField.reveal = checked
                }
            }

            Text {
                id: errorLabel
                color: theme.danger
                font.pixelSize: 11
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.preferredHeight: text.length ? implicitHeight : 0
                opacity: text.length ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 200 } }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 12
                Item { Layout.fillWidth: true }
                FButton { text: "Cancel"; onClicked: dlg.close() }
                FButton {
                    text: "Save"
                    variant: "primary"
                    onClicked: {
                        const err = backend.saveEntry(dlg.editKey, dlg.faculty, nameField.text,
                                                      modelBox.editText, keyField.text,
                                                      baseField.text, dlg.apiStyle)
                        if (err) { errorLabel.text = err; return }
                        dlg.close()
                    }
                }
            }
        }
    }
}
