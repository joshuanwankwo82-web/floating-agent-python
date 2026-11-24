import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Dialogs

// "Profile": who the user is. Same fields and encrypted store as before.
Item {
    id: page
    required property ConfirmDialog confirm
    property var profile: ({})

    readonly property int contentWidth: Math.min(width - 36, 1280)

    function load() {
        profile = backend.loadProfile()
        nameF.text = profile.name || ""
        devF.text = profile.dev_names || ""
        bioF.text = profile.bio || ""
        interestsF.text = profile.interests || ""
        goalsF.text = profile.goals || ""
        photoF.text = profile.photo || ""
        const links = profile.links || {}
        for (let i = 0; i < linkRepeater.count; ++i) {
            const item = linkRepeater.itemAt(i)
            item.field.text = links[item.linkKey] || ""
        }
        appsF.text = (profile.connected_apps || []).join(", ")
        emails.clear(); (profile.emails || []).forEach(r => emails.append({address: r.address || "", password: r.password || ""}))
        authors.clear(); (profile.github_authors || []).forEach(r => authors.append({username: r.username || "", email: r.email || "", token: r.token || ""}))
        creds.clear(); (profile.credentials || []).forEach(r => creds.append({site: r.site || "", email: r.email || "", username: r.username || "", password: r.password || ""}))
        refreshPhoto()
    }

    function refreshPhoto() { avatar.source = backend.photoUrl(photoF.text) }

    function collect() {
        const links = {}
        for (let i = 0; i < linkRepeater.count; ++i) {
            const item = linkRepeater.itemAt(i)
            links[item.linkKey] = item.field.text
        }
        const listOf = (m, keys) => {
            const out = []
            for (let i = 0; i < m.count; ++i) {
                const r = m.get(i), o = {}
                keys.forEach(k => o[k] = r[k])
                out.push(o)
            }
            return out
        }
        return {
            name: nameF.text, dev_names: devF.text, bio: bioF.text,
            interests: interestsF.text, goals: goalsF.text, photo: photoF.text,
            links: links, connected_apps: appsF.text,
            emails: listOf(emails, ["address", "password"]),
            github_authors: listOf(authors, ["username", "email", "token"]),
            credentials: listOf(creds, ["site", "email", "username", "password"])
        }
    }

    Component.onCompleted: load()

    ListModel { id: emails }
    ListModel { id: authors }
    ListModel { id: creds }

    FileDialog {
        id: photoPicker
        title: "Pick your profile photo"
        nameFilters: ["Images (*.png *.jpg *.jpeg *.webp *.gif)"]
        onAccepted: { photoF.text = backend.localPath(selectedFile.toString()); page.refreshPhoto() }
    }

    Flickable {
        id: flick
        anchors { top: parent.top; bottom: parent.bottom; horizontalCenter: parent.horizontalCenter }
        width: page.contentWidth
        contentHeight: col.implicitHeight + 70
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        SmoothWheel { id: wheel; flick: flick }
        EdgeAutoScroll { objectName: "profileEdgeScroll"; flick: flick }
        ScrollIndicator.vertical: ScrollIndicator {}

        ColumnLayout {
            id: col
            width: flick.width
            spacing: 4

            Heading { text: "Who you are"; Layout.topMargin: 12 }
            Label2 { text: "Real name" }
            FField { id: nameF; placeholderText: "e.g. Ada Lovelace"; Layout.fillWidth: true }
            Label2 { text: "Dev names / handles (comma-separated)" }
            FField { id: devF; placeholderText: "e.g. alovelace, ada-dev"; Layout.fillWidth: true }
            Label2 { text: "Bio \u2014 describe yourself (this text is what Friday reads to know you)" }
            TextArea {
                id: bioF
                Layout.fillWidth: true
                Layout.preferredHeight: Math.max(84, contentHeight + 20)
                Behavior on Layout.preferredHeight { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                wrapMode: TextEdit.Wrap
                color: theme.fg
                font.pixelSize: 12
                selectByMouse: true
                selectionColor: theme.hover
                padding: 10
                background: Rectangle {
                    radius: 8; color: theme.input; border.width: 1
                    border.color: bioF.activeFocus ? theme.accent : theme.border
                    Behavior on border.color { ColorAnimation { duration: 160 } }
                }
            }
            Label2 { text: "Interests \u2014 what you care about" }
            FField { id: interestsF; placeholderText: "e.g. AI agents, music production, fintech"; Layout.fillWidth: true }
            Label2 { text: "Goals \u2014 what you're working toward" }
            FField { id: goalsF; placeholderText: "e.g. ship a SaaS, pass finals, learn Rust"; Layout.fillWidth: true }

            Heading { text: "Photo" }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                FField { id: photoF; placeholderText: "path to an image file, or an https:// URL"; Layout.fillWidth: true; onEditingFinished: page.refreshPhoto() }
                FButton { text: "Browse\u2026"; variant: "ghost"; onClicked: photoPicker.open() }
                FButton { text: "Preview"; variant: "ghost"; onClicked: page.refreshPhoto() }
                FButton { text: "\u2715"; variant: "danger"; onClicked: { photoF.text = ""; page.refreshPhoto() } }
            }
            Item {
                Layout.preferredHeight: 64
                Layout.fillWidth: true
                Rectangle {
                    id: avatarFrame
                    width: 56; height: 56; radius: 28
                    y: 4
                    color: theme.input
                    border.color: theme.border
                    clip: true
                    Image {
                        id: avatar
                        anchors.fill: parent
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        sourceSize: Qt.size(112, 112)
                        opacity: status === Image.Ready ? 1 : 0
                        scale: status === Image.Ready ? 1 : 0.85
                        Behavior on opacity { NumberAnimation { duration: 260 } }
                        Behavior on scale { NumberAnimation { duration: 320; easing.type: Easing.OutBack } }
                    }
                }
                Text {
                    anchors { left: avatarFrame.right; leftMargin: 12; verticalCenter: avatarFrame.verticalCenter }
                    text: avatar.status === Image.Error ? "(couldn't load that photo \u2014 check the path/URL)"
                        : avatar.status === Image.Loading ? "loading\u2026"
                        : avatar.source.toString() === "" ? "(no photo yet)" : ""
                    color: theme.fg_dim
                    font.pixelSize: 11
                }
            }

            Heading { text: "Links" }
            Repeater {
                id: linkRepeater
                model: [["website", "Website"], ["portfolio", "Portfolio"], ["github", "GitHub"],
                        ["twitter", "Twitter / X"], ["linkedin", "LinkedIn"], ["telegram", "Telegram"]]
                delegate: ColumnLayout {
                    required property var modelData
                    readonly property string linkKey: modelData[0]
                    property alias field: linkField
                    Layout.fillWidth: true
                    spacing: 2
                    Label2 { text: modelData[1] }
                    FField { id: linkField; placeholderText: "https://"; Layout.fillWidth: true }
                }
            }

            Heading { text: "Emails" }
            Note { text: "Mailboxes Friday can work for you (read / send / sign-ups). With 2 or more, Friday asks which one to use. Passwords are encrypted, masked, and never reach any prompt. Setup only for now: no mail action happens yet." }
            AnimatedList {
                model: emails
                delegate: SecretRow {
                    required property int index
                    required property string address
                    required property string password
                    title: address
                    secret: password
                    onRemove: page.confirm.ask("Remove email", "Are you sure you want to remove " + address + "?", () => emails.remove(index))
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                FField { id: newAddr; placeholderText: "email address"; Layout.fillWidth: true }
                FField { id: newEpass; placeholderText: "password"; secret: true; Layout.fillWidth: true }
                FButton {
                    text: "+ Add email"
                    onClicked: {
                        if (!newAddr.text.trim().length) return
                        emails.append({address: newAddr.text.trim(), password: newEpass.text})
                        newAddr.text = ""; newEpass.text = ""
                    }
                }
            }

            Heading { text: "Connect apps" }
            Label2 { text: "Apps you use (comma-separated) \u2014 context so Friday knows your toolbox" }
            FField { id: appsF; placeholderText: "e.g. VS Code, GitHub, Figma, Slack"; Layout.fillWidth: true }

            Heading { text: "GitHub authors" }
            Note { text: "Commit identities for YOUR GitHub accounts (username + email). A clone carrying one of these usernames commits as that author automatically; other repos let you pick. The token is that account's own access token for GitHub actions (masked, never prompted)." }
            AnimatedList {
                model: authors
                delegate: Item {
                    id: authorRow
                    required property int index
                    required property string username
                    required property string email
                    required property string token
                    width: ListView.view.width
                    height: 36
                    RowLayout {
                        anchors.fill: parent
                        spacing: 8
                        Text { text: authorRow.username; color: theme.fg; font.pixelSize: 12; font.bold: true }
                        Text { text: authorRow.email; color: theme.fg_dim; font.pixelSize: 11 }
                        FField {
                            id: tokField
                            Layout.fillWidth: true
                            secret: true
                            placeholderText: "token (optional)"
                            text: authorRow.token
                            onTextEdited: authors.setProperty(authorRow.index, "token", text)
                        }
                        FCheck { text: "Show"; checked: tokField.reveal; onToggled: tokField.reveal = checked }
                        FButton {
                            text: "\u2715"; compact: true; variant: "danger"
                            onClicked: page.confirm.ask("Remove GitHub author",
                                "Are you sure you want to remove the GitHub author " + authorRow.username + "?",
                                () => authors.remove(authorRow.index))
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                FField { id: newGhUser; placeholderText: "github username"; Layout.fillWidth: true }
                FField { id: newGhMail; placeholderText: "author email"; Layout.fillWidth: true }
                FField { id: newGhTok; placeholderText: "token (optional)"; secret: true; Layout.fillWidth: true }
                FButton {
                    text: "+ Add author"
                    onClicked: {
                        if (!newGhUser.text.trim().length) return
                        authors.append({username: newGhUser.text.trim(), email: newGhMail.text.trim(), token: newGhTok.text.trim()})
                        newGhUser.text = ""; newGhMail.text = ""; newGhTok.text = ""
                    }
                }
            }

            Heading { text: "Web passwords" }
            Note { text: "Stored encrypted inside Friday's local store and shown only as masks \u2014 Friday never reads them into prompts or speaks them." }
            AnimatedList {
                model: creds
                delegate: SecretRow {
                    required property int index
                    required property string site
                    required property string email
                    required property string username
                    required property string password
                    title: site
                    subtitle: [email, username].filter(s => s.length).join("  \u00b7  ")
                    secret: password
                    onRemove: page.confirm.ask("Remove web password",
                        "Are you sure you want to remove the web password for " + site + "?",
                        () => creds.remove(index))
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 6
                FField { id: newSite; placeholderText: "site url"; Layout.fillWidth: true }
                FField { id: newMail; placeholderText: "email (optional)"; Layout.fillWidth: true }
                FField { id: newUser; placeholderText: "username"; Layout.fillWidth: true }
                FField { id: newPass; placeholderText: "password"; secret: true; Layout.fillWidth: true }
                FButton {
                    text: "Add"
                    variant: "ghost"
                    onClicked: {
                        if (!newSite.text.trim().length) return
                        creds.append({site: newSite.text.trim(), email: newMail.text, username: newUser.text, password: newPass.text})
                        newSite.text = ""; newMail.text = ""; newUser.text = ""; newPass.text = ""
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: 18
                Text {
                    id: saveStatus
                    color: text.indexOf("FAILED") >= 0 ? theme.danger : theme.success
                    font.pixelSize: 12
                    opacity: 0
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                }
                FButton {
                    text: "Save profile"
                    variant: "primary"
                    onClicked: {
                        saveStatus.text = backend.saveProfile(page.collect())
                        statusFade.restart()
                    }
                }
            }
        }
    }

    SequentialAnimation {
        id: statusFade
        OpacityAnimator { target: saveStatus; from: 0; to: 1; duration: 200 }
        PauseAnimation { duration: 3200 }
        OpacityAnimator { target: saveStatus; to: 0; duration: 600 }
    }

    JumpPills {
        anchors { right: parent.right; bottom: parent.bottom; margins: 14 }
        flick: flick
        wheel: wheel
    }

    // -- small local components -------------------------------------------
    component Heading: Text {
        color: theme.fg_bright
        font.pixelSize: 14
        font.bold: true
        Layout.topMargin: 18
        Layout.bottomMargin: 2
    }
    component Label2: Text {
        color: theme.fg_dim
        font.pixelSize: 11
        Layout.topMargin: 6
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    component Note: Text {
        color: theme.fg_faint
        font.pixelSize: 11
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }
    // A non-scrolling list whose rows animate in and out.
    component AnimatedList: ListView {
        Layout.fillWidth: true
        Layout.preferredHeight: contentHeight
        Behavior on Layout.preferredHeight { NumberAnimation { duration: 240; easing.type: Easing.OutCubic } }
        interactive: false
        spacing: 2
        add: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 240 }
                NumberAnimation { property: "x"; from: -16; to: 0; duration: 280; easing.type: Easing.OutCubic }
            }
        }
        remove: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; to: 0; duration: 180 }
                NumberAnimation { property: "x"; to: 24; duration: 200; easing.type: Easing.InCubic }
            }
        }
        displaced: Transition { NumberAnimation { properties: "y"; duration: 240; easing.type: Easing.OutCubic } }
    }
    component SecretRow: Item {
        id: secretRow
        property string title
        property string subtitle
        property string secret
        property bool shown: false
        signal remove()
        width: ListView.view ? ListView.view.width : 300
        height: 32
        RowLayout {
            anchors.fill: parent
            spacing: 8
            Text { text: secretRow.title; color: theme.fg; font.pixelSize: 12; font.bold: true }
            Text { text: secretRow.subtitle; color: theme.fg_dim; font.pixelSize: 11; visible: text.length > 0 }
            Text {
                text: secretRow.shown ? secretRow.secret : backend.maskSecret(secretRow.secret)
                color: theme.fg_dim
                font.pixelSize: secretRow.shown ? 11 : 12
                font.family: secretRow.shown ? "Consolas" : Qt.application.font.family
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            FButton { text: secretRow.shown ? "Hide" : "Show"; compact: true; variant: "ghost"; onClicked: secretRow.shown = !secretRow.shown }
            FButton { text: "\u2715"; compact: true; variant: "danger"; onClicked: secretRow.remove() }
        }
    }
}
