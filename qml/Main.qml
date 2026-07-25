import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Window

ApplicationWindow {
    id: window
    objectName: "mainWindow"
    width: Screen.desktopAvailableWidth > 0 ? Math.min(1180, Screen.desktopAvailableWidth - 24) : 1180
    height: Screen.desktopAvailableHeight > 0 ? Math.min(760, Screen.desktopAvailableHeight - 24) : 760
    minimumWidth: Screen.desktopAvailableWidth > 0 ? Math.min(900, Screen.desktopAvailableWidth - 24) : 900
    minimumHeight: Screen.desktopAvailableHeight > 0 ? Math.min(620, Screen.desktopAvailableHeight - 24) : 620
    visible: true; title: t("app_name")
    flags: Qt.Window | Qt.FramelessWindowHint
    color: "transparent"
    property bool dark: backend.theme === "dark"
    property color ink: dark ? "#f7f8ff" : "#10131a"
    property color muted: dark ? "#aeb4c3" : "#69707d"
    property color glass: dark ? "#991c1d25" : "#99ffffff"
    property color glassStrong: dark ? "#d9262731" : "#d9ffffff"
    property color accent: "#0a84ff"
    property color separator: dark ? "#33ffffff" : "#22000000"
    property int page: 0
    property var incomingLocalRequest: ({})
    property string localReceiveDestination: ""
    property var previewSelection: ({})
    property var previewActions: ({})
    function t(key) { var languageDependency = backend.language; return backend.text(key) }
    function tf(key, values) { var languageDependency = backend.language; return backend.textWith(key, values) }
    palette.window: dark ? "#ff20212a" : "#fff7f9fc"
    palette.windowText: ink
    palette.base: dark ? "#ff2b2c35" : "#ffffffff"
    palette.alternateBase: dark ? "#ff34353f" : "#fff1f4f8"
    palette.text: ink
    palette.button: dark ? "#ff353640" : "#fff4f7fb"
    palette.buttonText: ink
    palette.highlight: accent
    palette.highlightedText: "#ffffff"
    palette.placeholderText: muted
    palette.mid: dark ? "#ff555762" : "#ffd5dae2"
    LayoutMirroring.enabled: backend.rtl
    LayoutMirroring.childrenInherit: true

    Rectangle {
        anchors.fill: parent; radius: 28
        gradient: Gradient {
            GradientStop { position: 0; color: dark ? "#090a0f" : "#e9f3ff" }
            GradientStop { position: .46; color: dark ? "#171620" : "#f7efff" }
            GradientStop { position: 1; color: dark ? "#0c1115" : "#e8fff8" }
        }
        border.color: dark ? "#55ffffff" : "#ccffffff"; border.width: 1

        Rectangle { width: 520; height: 520; radius: 260; x: 180; y: -300; color: dark ? "#33364fff" : "#88a8d8ff"; rotation: 8 }
        Rectangle { width: 420; height: 420; radius: 210; x: 760; y: 470; color: dark ? "#3324a68a" : "#77b7ffe5" }
        Rectangle { width: 280; height: 280; radius: 140; x: 610; y: 160; color: dark ? "#221b79ff" : "#44ffb9dc" }

        ColumnLayout {
            anchors.fill: parent; anchors.margins: 12; spacing: 12
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 52; radius: 18; color: glass
                border.color: dark ? "#33ffffff" : "#bbffffff"
                MouseArea { anchors.fill: parent; onPressed: window.startSystemMove(); acceptedButtons: Qt.LeftButton }
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 16; spacing: 10
                    Label { text: "AutoSD"; color: ink; font.pixelSize: 17; font.weight: Font.DemiBold }
                    Label { text: t("app_title"); color: muted; font.pixelSize: 13; font.letterSpacing: .2 }
                    Item { Layout.fillWidth: true }
                    ToolButton { text: "−"; implicitWidth: 36; implicitHeight: 36; onClicked: window.showMinimized(); background: Item { } contentItem: Text { text: parent.text; color: parent.hovered ? accent : ink; font.pixelSize: 18; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter } }
                    ToolButton { text: window.visibility === Window.Maximized ? "❐" : "□"; implicitWidth: 36; implicitHeight: 36; onClicked: window.visibility === Window.Maximized ? window.showNormal() : window.showMaximized(); background: Item { } contentItem: Text { text: parent.text; color: parent.hovered ? accent : ink; font.pixelSize: 16; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter } }
                    ToolButton { text: "×"; implicitWidth: 36; implicitHeight: 36; onClicked: window.close(); background: Item { } contentItem: Text { text: parent.text; color: parent.hovered ? "#ff453a" : ink; font.pixelSize: 21; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter } }
                }
            }
            RowLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                Rectangle {
                    Layout.preferredWidth: window.width < 1040 ? 200 : 220; Layout.fillHeight: true; radius: 24; color: glass
                    border.color: dark ? "#33ffffff" : "#bbffffff"
                    ColumnLayout {
                        anchors.fill: parent; anchors.margins: 14; spacing: 8
                        Label { text: "◉  AutoSD"; color: ink; font.pixelSize: 21; font.weight: Font.DemiBold; Layout.bottomMargin: 16 }
                        Repeater {
                            model: ["⇄  " + t("tab_transfer"), "⌘  " + t("tab_duplicates"), "◷  " + t("tab_history"), "⌁  " + t("nav_p2p"), "?  " + t("tab_help")]
                            delegate: Button {
                                required property string modelData; required property int index
                                text: modelData; Layout.fillWidth: true; height: 48; flat: true
                                onClicked: page = index
                                background: Rectangle { radius: 16; color: page === index ? (dark ? "#664b9fff" : "#b8ffffff") : parent.hovered ? "#44ffffff" : "transparent"; border.color: page === index ? (dark ? "#55ffffff" : "#ddffffff") : "transparent" }
                                contentItem: Text { text: parent.text; color: page === index ? accent : ink; font.pixelSize: 14; font.weight: page === index ? Font.DemiBold : Font.Normal; verticalAlignment: Text.AlignVCenter; leftPadding: 12 }
                            }
                        }
                        Item { Layout.fillHeight: true }
                        ComboBox { id: language; Layout.fillWidth: true; model: ["Français", "English", "עברית"]; onActivated: backend.language = ["fr","en","he"][currentIndex] }
                        Switch { text: dark ? t("theme_dark") : t("theme_light"); checked: dark; onToggled: backend.theme = checked ? "dark" : "light"; palette.windowText: ink }
                    }
                }
                Rectangle {
                    Layout.fillWidth: true; Layout.fillHeight: true; radius: 28; color: glassStrong
                    border.color: dark ? "#44ffffff" : "#ddffffff"
                    StackLayout {
                        anchors.fill: parent; anchors.margins: window.width < 1040 ? 18 : 28; currentIndex: page
                        ScrollView {
                            id: transferScroll
                            objectName: "transferScroll"
                            clip: true
                            contentWidth: availableWidth
                            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                            TransferPage { width: transferScroll.availableWidth }
                        }
                        DuplicatePage { }
                        HistoryPage { }
                        ScrollView {
                            id: p2pScroll
                            objectName: "p2pScroll"
                            clip: true
                            contentWidth: availableWidth
                            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                            P2PPage { width: p2pScroll.availableWidth }
                        }
                        HelpPage { }
                    }
                }
            }
            Rectangle {
                Layout.fillWidth: true; Layout.preferredHeight: 46; radius: 17; color: glass
                RowLayout { anchors.fill: parent; anchors.margins: 10
                    Label { text: backend.status; color: muted; elide: Text.ElideRight; Layout.fillWidth: true }
                    PillButton { visible: backend.lastError.length > 0; text: t("show_error"); onClicked: errorDialog.open() }
                    ProgressBar { visible: backend.busy; value: backend.progress; Layout.preferredWidth: 220 }
                    PillButton { visible: backend.busy; text: t("cancel"); onClicked: backend.cancel() }
                }
            }
        }
    }

    MouseArea { anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 6; cursorShape: Qt.SizeHorCursor; onPressed: window.startSystemResize(Qt.RightEdge) }
    MouseArea { anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: 6; cursorShape: Qt.SizeVerCursor; onPressed: window.startSystemResize(Qt.BottomEdge) }
    MouseArea { anchors.right: parent.right; anchors.bottom: parent.bottom; width: 12; height: 12; cursorShape: Qt.SizeFDiagCursor; onPressed: window.startSystemResize(Qt.RightEdge | Qt.BottomEdge) }

    component GlassField: TextField {
        color: ink; placeholderTextColor: muted; leftPadding: 14; rightPadding: 14
        selectionColor: accent
        background: Rectangle {
            radius: 14
            color: dark ? "#662d2e38" : "#aaffffff"
            border.width: parent.activeFocus ? 2 : 1
            border.color: parent.activeFocus ? accent : (dark ? "#44ffffff" : "#99ffffff")
        }
    }
    component GlassTextArea: TextArea {
        color: ink; placeholderTextColor: muted; padding: 12
        selectionColor: accent; selectByMouse: true
        textFormat: TextEdit.PlainText; wrapMode: TextEdit.WrapAnywhere
        background: Rectangle {
            radius: 14
            color: dark ? "#662d2e38" : "#aaffffff"
            border.width: parent.activeFocus ? 2 : 1
            border.color: parent.activeFocus ? accent : (dark ? "#44ffffff" : "#99ffffff")
        }
    }
    component PillButton: Button {
        id: pill
        implicitHeight: 42; leftPadding: 18; rightPadding: 18
        contentItem: Text {
            text: pill.text; color: pill.enabled ? (pill.highlighted ? "#ffffff" : ink) : muted
            font.pixelSize: 13; font.weight: Font.Medium
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle {
            radius: height / 2
            color: !pill.enabled ? (dark ? "#332f3038" : "#66e7eaf0") : pill.highlighted ? accent : pill.down ? (dark ? "#aa474952" : "#ffeeeff4") : pill.hovered ? (dark ? "#8840434e" : "#ffffffff") : (dark ? "#66363842" : "#ccf7f9fc")
            border.width: 1; border.color: pill.highlighted ? accent : (dark ? "#44ffffff" : "#99ffffff")
        }
    }
    component ExtensionField: ColumnLayout {
        id: extensionControl
        property alias text: extensionInput.text
        property string completion: ""
        spacing: 3
        function refreshCompletion() {
            var values = ["jpg", "jpeg", "png", "heic", "raw", "dng", "gif", "webp", "mov", "mp4", "m4v", "avi", "mkv", "pdf", "doc", "docx", "xls", "xlsx"]
            var parts = extensionInput.text.split(",")
            var fragment = parts[parts.length - 1].trim().toLowerCase()
            completion = ""
            if (fragment.length === 0) return
            for (var i = 0; i < values.length; ++i) {
                if (values[i].indexOf(fragment) === 0 && values[i] !== fragment) { completion = values[i]; return }
            }
        }
        function acceptCompletion() {
            if (!completion.length) return
            var parts = extensionInput.text.split(",")
            parts[parts.length - 1] = (parts.length > 1 ? " " : "") + completion
            extensionInput.text = parts.join(",")
            extensionInput.cursorPosition = extensionInput.text.length
            completion = ""
        }
        GlassField {
            id: extensionInput; Layout.fillWidth: true; placeholderText: t("extension_placeholder")
            onTextChanged: extensionControl.refreshCompletion()
            Keys.onTabPressed: function(event) { extensionControl.acceptCompletion(); event.accepted = true }
        }
        Label { visible: extensionControl.completion.length > 0; text: tf("completion_hint", {value: extensionControl.completion}); color: accent; font.pixelSize: 11 }
    }
    component Heading: Label { color: ink; font.pixelSize: 30; font.weight: Font.DemiBold; font.letterSpacing: -.4 }

    component TransferPage: ColumnLayout {
        spacing: 14
        Heading { text: t("tab_transfer") }
        Label { text: t("transfer_qt_subtitle"); color: muted }
        GridLayout { columns: 2; columnSpacing: 12; rowSpacing: 12; Layout.fillWidth: true
            Label { text: t("source"); color: ink } RowLayout { Layout.fillWidth: true; GlassField { id: source; Layout.fillWidth: true; placeholderText: t("source_placeholder") } PillButton { text: t("browse"); onClicked: sourceDialog.open() } }
            Label { text: t("destination"); color: ink } RowLayout { Layout.fillWidth: true; GlassField { id: destination; Layout.fillWidth: true; placeholderText: t("destination_placeholder") } PillButton { text: t("browse"); onClicked: destinationDialog.open() } }
            Label { text: t("extensions"); color: ink }
            ExtensionField { id: extensions; Layout.fillWidth: true }
            Label { text: t("profile"); color: ink; Layout.alignment: Qt.AlignTop; Layout.topMargin: 12 } GridLayout {
                id: profileActions
                Layout.fillWidth: true
                columns: window.width < 1050 ? 2 : 4
                ComboBox { id: profile; Layout.fillWidth: true; Layout.columnSpan: profileActions.columns === 2 ? 2 : 1; model: backend.profiles; editable: true }
                PillButton { text: t("load"); onClicked: { var p = backend.loadProfile(profile.editText); source.text = p.source || ""; destination.text = p.destination || ""; extensions.text = p.extensions || "" } }
                PillButton { text: t("save"); onClicked: backend.saveProfile(profile.editText, settings()) }
                PillButton { text: t("delete"); onClicked: backend.deleteProfile(profile.editText) }
            }
            Label { text: t("conflicts"); color: ink }
            ComboBox { id: conflictPolicy; Layout.fillWidth: true; model: [t("policy_rename"), t("policy_skip"), t("policy_replace"), t("policy_newer"), t("policy_ask")] }
            Label { text: t("automatic_organization"); color: ink }
            ComboBox { id: organization; Layout.fillWidth: true; model: [t("organization_none"), t("organization_date"), t("organization_year_month"), t("organization_type")] }
            Label { text: t("period"); color: ink }
            RowLayout {
                Layout.fillWidth: true
                ComboBox { id: dateMode; model: [t("all_dates"), t("most_recent_day"), t("specific_date"), t("date_range")] }
                GlassField { id: dateStart; Layout.fillWidth: true; visible: dateMode.currentIndex >= 2; placeholderText: t("date_format") }
                GlassField { id: dateEnd; Layout.fillWidth: true; visible: dateMode.currentIndex === 3; placeholderText: t("date_format") }
            }
            Label { text: t("subfolder"); color: ink }
            RowLayout {
                Layout.fillWidth: true
                CheckBox { id: createFolder; text: t("create"); checked: true; palette.windowText: ink }
                ComboBox { id: folderMode; enabled: createFolder.checked; model: [t("automatic_name"), t("custom_name")] }
                GlassField { id: folderName; Layout.fillWidth: true; visible: createFolder.checked && folderMode.currentIndex === 1; text: "File_Backup" }
            }
        }
        FolderDialog { id: sourceDialog; title: t("choose_source_title"); onAccepted: source.text = localPath(selectedFolder) }
        FolderDialog { id: destinationDialog; title: t("choose_destination_title"); onAccepted: destination.text = localPath(selectedFolder) }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 2
            CheckBox { id: verify; text: t("verify_sha"); checked: true; palette.windowText: ink }
            CheckBox { id: preserve; text: t("preserve_tree"); palette.windowText: ink }
            CheckBox { id: remove; text: t("delete_verified_source"); palette.windowText: ink }
        }
        RowLayout { Layout.alignment: Qt.AlignRight; Layout.topMargin: 6
            PillButton { text: t("eject_source"); enabled: !backend.busy && source.text.length > 0; onClicked: backend.eject(source.text) }
            PillButton { text: t("preview"); enabled: !backend.busy; onClicked: backend.previewTransfer(settings()) }
            PillButton { text: t("start"); enabled: !backend.busy; highlighted: true; onClicked: remove.checked ? sourceDeleteConfirmation.open() : backend.previewTransfer(settings()) }
        }
        Dialog {
            id: sourceDeleteConfirmation
            modal: true; width: 480; anchors.centerIn: parent; title: t("delete_sources_title")
            standardButtons: Dialog.Cancel | Dialog.Ok
            onAccepted: backend.previewTransfer(settings())
            contentItem: ColumnLayout {
                spacing: 10
                Label { text: t("delete_sources_detail"); color: ink; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Label { text: t("irreversible"); color: "#ff453a"; font.weight: Font.DemiBold }
            }
        }
        function settings() {
            return {
                source: source.text,
                destination: destination.text,
                extensions: extensions.text,
                dateMode: ["all", "latest", "specific", "range"][dateMode.currentIndex],
                dateStart: dateStart.text,
                dateEnd: dateEnd.text,
                createFolder: createFolder.checked,
                folderNameMode: folderMode.currentIndex === 1 ? "custom" : "auto",
                folderName: folderName.text,
                verify: verify.checked,
                preserveTree: preserve.checked,
                deleteSource: remove.checked,
                conflictPolicy: ["rename", "skip", "replace", "newer", "ask"][conflictPolicy.currentIndex],
                organization: ["none", "date", "year_month", "type"][organization.currentIndex]
            }
        }
        function localPath(url) { return decodeURIComponent(url.toString().replace(/^file:\/\/\//, "")) }
    }
    component DuplicatePage: ColumnLayout {
        Heading { text: t("verified_duplicates") }
        RowLayout { Layout.fillWidth: true; GlassField { id: folder; Layout.fillWidth: true; placeholderText: t("scan_folder_placeholder") } PillButton { text: t("browse"); onClicked: duplicateDialog.open() } PillButton { text: t("scan"); enabled: !backend.busy; highlighted: true; onClicked: backend.scanDuplicates(folder.text) } }
        FolderDialog { id: duplicateDialog; title: t("choose_scan_title"); onAccepted: folder.text = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\/\//, "")) }
        ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; model: backend.duplicates; spacing: 8
            delegate: Rectangle { required property var modelData; width: ListView.view.width; height: 70; radius: 11; color: dark ? "#55313d58" : "#99ffffff"
                Column { anchors.fill: parent; anchors.margins: 10; Text { text: modelData.original; color: ink; elide: Text.ElideMiddle; width: parent.width } Text { text: tf("duplicate_count", {count: modelData.duplicates.length, size: modelData.size}); color: muted } }
            }
        }
        RowLayout { Layout.alignment: Qt.AlignRight
            PillButton { text: t("move_all_copies"); enabled: backend.duplicates.length > 0 && !backend.busy; onClicked: backend.applyDuplicateAction(allCopies(), "move") }
            PillButton { text: t("delete_all_copies"); enabled: backend.duplicates.length > 0 && !backend.busy; onClicked: duplicateDeleteConfirmation.open() }
        }
        Dialog {
            id: duplicateDeleteConfirmation
            modal: true; width: 480; anchors.centerIn: parent; title: t("delete_duplicates_title")
            standardButtons: Dialog.Cancel | Dialog.Ok
            onAccepted: backend.applyDuplicateAction(allCopies(), "delete")
            contentItem: ColumnLayout {
                spacing: 10
                Label { text: tf("delete_duplicates_confirm", {count: allCopies().length}); color: ink; wrapMode: Text.Wrap; Layout.fillWidth: true }
                Label { text: t("irreversible"); color: "#ff453a"; font.weight: Font.DemiBold }
            }
        }
        function allCopies() { var out = []; for (var i = 0; i < backend.duplicates.length; ++i) out = out.concat(backend.duplicates[i].duplicates); return out }
    }
    component HistoryPage: ColumnLayout {
        RowLayout {
            Layout.fillWidth: true
            Heading { text: t("tab_history") }
            Item { Layout.fillWidth: true }
            PillButton { text: t("export"); enabled: backend.history.length > 0 && !backend.busy; onClicked: historyExportDialog.open() }
        }
        FileDialog {
            id: historyExportDialog
            title: t("export_history_title")
            fileMode: FileDialog.SaveFile
            nameFilters: [t("text_report") + " (*.txt)", t("all_files") + " (*)"]
            onAccepted: {
                var path = decodeURIComponent(selectedFile.toString().replace(/^file:\/\/\//, ""))
                if (backend.exportHistory(path)) {
                    toast.text = tf("export_success", {path: path})
                    toast.open()
                }
            }
        }
        ListView { Layout.fillWidth: true; Layout.fillHeight: true; model: backend.history; clip: true; spacing: 7
            delegate: Rectangle { required property var modelData; width: ListView.view.width; height: 62; radius: 10; color: dark ? "#4434425e" : "#88ffffff"
                RowLayout { anchors.fill: parent; anchors.margins: 10; Label { text: modelData.timestamp || ""; color: ink; Layout.preferredWidth: 170 } Label { text: modelData.source + "  →  " + modelData.destination; color: muted; elide: Text.ElideMiddle; Layout.fillWidth: true } Label { text: tf("file_count", {count: modelData.copied || 0}); color: accent } }
            }
        }
    }
    component P2PPage: ColumnLayout {
        Heading { text: t("p2p_title") }
        Label { text: t("p2p_subtitle"); color: muted }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 42; radius: 21
            color: dark ? "#552f3039" : "#b3ffffff"; border.color: p2pStateColor()
            TapHandler { enabled: backend.lastError.length > 0; onTapped: errorDialog.open() }
            RowLayout { anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 9
                Rectangle { width: 9; height: 9; radius: 5; color: p2pStateColor() }
                Label { text: p2pStateText(); color: ink; font.weight: Font.Medium; Layout.fillWidth: true }
                BusyIndicator { visible: ["generating", "waiting", "manual_generating", "manual_waiting", "connecting", "transferring"].indexOf(backend.p2pState) >= 0; running: visible; implicitWidth: 24; implicitHeight: 24 }
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 74; radius: 16
            color: dark ? "#55313d58" : "#ccffffff"; border.color: separator
            ColumnLayout { anchors.fill: parent; anchors.margins: 12; spacing: 6
                RowLayout { Layout.fillWidth: true
                    Label { text: backend.p2pStats.summary || t("p2p_stats_idle"); color: ink; elide: Text.ElideRight; Layout.fillWidth: true }
                    Label { text: backend.p2pStats.speedText || ""; color: muted }
                }
                ProgressBar { Layout.fillWidth: true; value: Math.max(0, Math.min(1, (backend.p2pStats.percent || 0) / 100)) }
            }
        }
        Label { text: t("socket_direct"); color: ink; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.topMargin: 6 }
        Label { text: t("socket_direct_help"); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
        GridLayout {
            Layout.fillWidth: true
            columns: width < 760 ? 1 : 3
            GlassField { id: socketDestination; Layout.fillWidth: true; placeholderText: t("socket_receive_folder") }
            PillButton { text: t("choose_folder"); onClicked: socketDestinationDialog.open() }
            PillButton {
                text: t("socket_start_receive")
                enabled: !backend.busy
                highlighted: true
                onClicked: socketDestination.text.length > 0 ? backend.startSocketReceive(socketDestination.text) : socketDestinationDialog.open()
            }
        }
        FolderDialog {
            id: socketDestinationDialog
            title: t("choose_receive_folder")
            onAccepted: {
                socketDestination.text = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\/\//, ""))
                backend.startSocketReceive(socketDestination.text)
            }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 70; radius: 16
            color: dark ? "#66313d58" : "#ccffffff"; border.color: backend.socketCode ? accent : separator
            RowLayout { anchors.fill: parent; anchors.margins: 12
                ColumnLayout { Layout.fillWidth: true; spacing: 4
                    Label { text: t("socket_code_label"); color: muted; font.pixelSize: 10; font.letterSpacing: 1.1 }
                    Label { text: backend.socketCode || tf("socket_waiting", {code: backend.socketPairingCode}); color: backend.socketCode ? accent : muted; font.pixelSize: 18; font.weight: Font.DemiBold; elide: Text.ElideRight; Layout.fillWidth: true }
                }
                PillButton { text: t("copy_code"); visible: backend.socketCode.length > 0; onClicked: backend.copyToClipboard(backend.socketCode) }
            }
        }
        GridLayout {
            Layout.fillWidth: true
            columns: width < 820 ? 1 : 4
            GlassField { id: socketCodeInput; Layout.fillWidth: true; placeholderText: t("socket_code_placeholder") }
            GlassField { id: socketFiles; Layout.fillWidth: true; placeholderText: t("socket_files_placeholder") }
            PillButton { text: t("choose_files"); onClicked: socketFilesDialog.open() }
            PillButton { text: t("choose_send_folder"); onClicked: socketFolderDialog.open() }
            PillButton {
                text: t("socket_send")
                enabled: !backend.busy && socketCodeInput.text.length > 0 && socketFiles.text.length > 0
                highlighted: true
                Layout.columnSpan: width < 820 ? 1 : 4
                onClicked: backend.sendSocket(
                    socketCodeInput.text,
                    socketFiles.text.split(";").filter(function(v) { return v.trim().length > 0 })
                )
            }
        }
        FileDialog {
            id: socketFilesDialog
            title: t("choose_send_files")
            fileMode: FileDialog.OpenFiles
            onAccepted: {
                var paths = []
                for (var i = 0; i < selectedFiles.length; ++i)
                    paths.push(decodeURIComponent(selectedFiles[i].toString().replace(/^file:\/\/\//, "")))
                socketFiles.text = paths.join(";")
            }
        }
        FolderDialog {
            id: socketFolderDialog
            title: t("choose_local_folder")
            onAccepted: {
                var path = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\/\//, ""))
                socketFiles.text = socketFiles.text.length > 0 ? socketFiles.text + ";" + path : path
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: separator; Layout.topMargin: 4 }
        Label { text: t("manual_connection"); color: ink; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.topMargin: 6 }
        Label { text: t("manual_connection_help"); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
        GridLayout {
            id: manualReceiveActions
            Layout.fillWidth: true
            columns: width < 620 ? 1 : 2
            GlassField { id: manualDestination; Layout.fillWidth: true; placeholderText: t("manual_receive_folder") }
            PillButton { text: t("choose_folder"); onClicked: manualDestinationDialog.open() }
        }
        FolderDialog {
            id: manualDestinationDialog
            title: t("choose_receive_folder")
            onAccepted: manualDestination.text = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\/\//, ""))
        }
        GridLayout {
            id: manualSendActions
            Layout.fillWidth: true
            columns: width < 620 ? 1 : 2
            GlassField { id: manualFiles; Layout.fillWidth: true; placeholderText: t("manual_files_placeholder") }
            PillButton { text: t("choose_files"); onClicked: manualFilesDialog.open() }
        }
        FileDialog {
            id: manualFilesDialog
            title: t("choose_send_files")
            fileMode: FileDialog.OpenFiles
            onAccepted: {
                var paths = []
                for (var i = 0; i < selectedFiles.length; ++i)
                    paths.push(decodeURIComponent(selectedFiles[i].toString().replace(/^file:\/\/\//, "")))
                manualFiles.text = paths.join(";")
            }
        }
        Label { text: t("manual_received_data"); color: muted; font.weight: Font.Medium }
        GlassTextArea {
            id: manualInput
            objectName: "manualInput"
            Layout.fillWidth: true
            Layout.preferredHeight: 105
            placeholderText: t("manual_incoming_placeholder")
        }
        Flow {
            Layout.fillWidth: true; spacing: 8
            PillButton {
                text: t("manual_create_offer")
                enabled: !backend.busy && manualFiles.text.length > 0
                highlighted: true
                onClicked: backend.startManualSend(manualFiles.text.split(";").filter(function(v) { return v.trim().length > 0 }))
            }
            PillButton {
                text: t("manual_create_answer")
                enabled: !backend.busy && manualDestination.text.length > 0 && manualInput.text.trim().length > 0
                onClicked: backend.startManualReceive(manualDestination.text, manualInput.text)
            }
            PillButton {
                text: t("manual_apply_answer")
                enabled: backend.busy && backend.manualPayloadKind === "offer" && manualInput.text.trim().length > 0
                onClicked: backend.submitManualAnswer(manualInput.text)
            }
            PillButton {
                text: t("manual_clear")
                onClicked: {
                    manualInput.clear()
                    manualFiles.clear()
                    manualDestination.clear()
                    backend.clearManualP2P()
                }
            }
        }
        Label {
            text: backend.manualPayloadKind === "offer" ? t("manual_output_offer") : backend.manualPayloadKind === "answer" ? t("manual_output_answer") : t("manual_output_waiting")
            color: muted; font.weight: Font.Medium
        }
        GlassTextArea {
            id: manualOutput
            objectName: "manualOutput"
            Layout.fillWidth: true
            Layout.preferredHeight: 120
            readOnly: true
            text: backend.manualPayload
            placeholderText: t("manual_outgoing_placeholder")
        }
        PillButton { text: t("manual_copy"); enabled: backend.manualPayload.length > 0; onClicked: backend.copyManualPayload() }
        Rectangle { Layout.fillWidth: true; Layout.preferredHeight: 1; color: separator; Layout.topMargin: 4 }
        GlassField { id: rendezvous; Layout.fillWidth: true; text: backend.rendezvousUrl; placeholderText: t("rendezvous_placeholder") }
        Label { text: t("receive_files"); color: ink; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.topMargin: 6 }
        Label { text: t("receive_steps"); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
        GridLayout {
            id: internetReceiveActions
            Layout.fillWidth: true
            columns: width < 700 ? 1 : 3
            GlassField { id: destination; Layout.fillWidth: true; placeholderText: t("receive_folder") }
            PillButton { text: t("browse"); onClicked: receiveFolderDialog.open() }
            PillButton { text: t("generate_code"); enabled: !backend.busy; highlighted: true; onClicked: destination.text.length > 0 ? backend.receiveP2P(destination.text, rendezvous.text) : receiveFolderDialog.open() }
        }
        FolderDialog { id: receiveFolderDialog; title: t("choose_receive_folder"); onAccepted: { destination.text = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\/\//, "")); backend.receiveP2P(destination.text, rendezvous.text) } }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 76; radius: 18
            color: dark ? "#66313d58" : "#ccffffff"; border.color: backend.p2pCode ? accent : separator
            RowLayout { anchors.fill: parent; anchors.margins: 12
                Item { Layout.fillWidth: true }
                Column { spacing: 4
                    Label { anchors.horizontalCenter: parent.horizontalCenter; text: t("your_receive_code"); color: muted; font.pixelSize: 10; font.letterSpacing: 1.2 }
                    Label { anchors.horizontalCenter: parent.horizontalCenter; text: backend.p2pCode || t("waiting_generation"); color: backend.p2pCode ? accent : muted; font.pixelSize: 20; font.weight: Font.DemiBold }
                }
                PillButton { text: t("copy_code"); visible: backend.p2pCode.length > 0; onClicked: backend.copyToClipboard(backend.p2pCode) }
                Item { Layout.fillWidth: true }
            }
        }
        Label { text: t("send_files"); color: ink; font.pixelSize: 16; font.weight: Font.DemiBold; Layout.topMargin: 6 }
        GridLayout {
            id: internetSendActions
            Layout.fillWidth: true
            columns: width < 760 ? 1 : 4
            GlassField { id: code; Layout.fillWidth: true; placeholderText: t("received_code_placeholder") }
            GlassField { id: files; Layout.fillWidth: true; placeholderText: t("files_semicolon_placeholder") }
            PillButton { text: t("choose_files"); onClicked: sendFilesDialog.open() }
            PillButton { text: t("send"); enabled: !backend.busy && code.text.length > 0 && files.text.length > 0; onClicked: backend.sendP2P(code.text, rendezvous.text, files.text.split(";").filter(function(v) { return v.trim().length > 0 })) }
        }
        FileDialog {
            id: sendFilesDialog
            title: t("choose_send_files")
            fileMode: FileDialog.OpenFiles
            onAccepted: {
                var paths = []
                for (var i = 0; i < selectedFiles.length; ++i)
                    paths.push(decodeURIComponent(selectedFiles[i].toString().replace(/^file:\/\/\//, "")))
                files.text = paths.join(";")
            }
        }
        Label { text: t("local_transfer"); color: ink; font.pixelSize: 16; font.weight: Font.DemiBold }
        GridLayout {
            id: localDestinationActions
            Layout.fillWidth: true
            columns: width < 620 ? 1 : 2
            GlassField {
                id: localDestination
                Layout.fillWidth: true
                text: window.localReceiveDestination
                placeholderText: t("local_receive_folder")
                onTextChanged: window.localReceiveDestination = text
            }
            PillButton { text: t("choose_folder"); onClicked: localDestinationDialog.open() }
        }
        FolderDialog {
            id: localDestinationDialog
            title: t("choose_local_receive_folder")
            onAccepted: window.localReceiveDestination = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\/\//, ""))
        }
        GridLayout {
            id: localDiscoveryActions
            Layout.fillWidth: true
            columns: width < 620 ? 1 : 2
            ComboBox {
                id: localDevice
                Layout.fillWidth: true
                model: backend.devices
                textRole: "label"
                valueRole: "id"
                displayText: count > 0 ? currentText : t("no_device")
            }
            PillButton { text: t("search"); enabled: !backend.busy; onClicked: backend.scanDevices() }
        }
        GridLayout {
            id: localSendActions
            Layout.fillWidth: true
            columns: width < 820 ? 1 : 4
            GlassField { id: localFiles; Layout.fillWidth: true; placeholderText: t("local_files_placeholder") }
            PillButton { text: t("choose_files"); onClicked: localFilesDialog.open() }
            PillButton { text: t("choose_send_folder"); onClicked: localFolderDialog.open() }
            PillButton {
                text: t("send_local")
                enabled: !backend.busy && localDevice.count > 0 && localFiles.text.length > 0
                highlighted: true
                onClicked: backend.sendLocal(
                    String(localDevice.currentValue),
                    localFiles.text.split(";").filter(function(value) { return value.trim().length > 0 })
                )
            }
        }
        FileDialog {
            id: localFilesDialog
            title: t("choose_local_files")
            fileMode: FileDialog.OpenFiles
            onAccepted: {
                var paths = []
                for (var i = 0; i < selectedFiles.length; ++i)
                    paths.push(decodeURIComponent(selectedFiles[i].toString().replace(/^file:\/\/\//, "")))
                localFiles.text = paths.join(";")
            }
        }
        FolderDialog {
            id: localFolderDialog
            objectName: "localFolderDialog"
            title: t("choose_local_folder")
            onAccepted: {
                var path = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\/\//, ""))
                localFiles.text = localFiles.text.length > 0 ? localFiles.text + ";" + path : path
            }
        }
        Item { Layout.fillHeight: true }
        function p2pStateText() {
            var labels = {
                "idle": t("p2p_idle"),
                "generating": t("p2p_generating"),
                "waiting": t("p2p_waiting"),
                "manual_generating": t("p2p_manual_generating"),
                "manual_waiting": t("p2p_manual_waiting"),
                "connecting": t("p2p_connecting"),
                "transferring": t("p2p_transferring"),
                "success": t("p2p_success"),
                "cancelled": t("p2p_cancelled"),
                "expired": t("p2p_expired"),
                "error": t("p2p_error")
            }
            return labels[backend.p2pState] || labels.idle
        }
        function p2pStateColor() {
            if (backend.p2pState === "success") return "#30d158"
            if (backend.p2pState === "error" || backend.p2pState === "expired") return "#ff453a"
            if (backend.p2pState === "cancelled") return "#ff9f0a"
            if (["generating", "waiting", "manual_generating", "manual_waiting", "connecting", "transferring"].indexOf(backend.p2pState) >= 0) return accent
            return muted
        }
    }

    component HelpPage: ColumnLayout {
        spacing: 14
        Heading { text: t("tab_help") }
        Label { text: t("help_subtitle"); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 96; radius: 16
            color: dark ? "#55313d58" : "#ccffffff"; border.color: separator
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 14
                Label { text: tf("help_version", {version: backend.appVersion}); color: ink; font.weight: Font.DemiBold }
                Label { text: tf("help_device_code", {code: backend.deviceCode}); color: muted }
            }
        }
        Label { text: t("help_p2p_title"); color: ink; font.pixelSize: 16; font.weight: Font.DemiBold }
        Label { text: t("help_p2p_body"); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
        Label { text: t("help_update_title"); color: ink; font.pixelSize: 16; font.weight: Font.DemiBold }
        Label { text: t("help_update_body"); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
        PillButton { text: t("check_updates"); highlighted: true; onClicked: backend.checkForUpdates() }
        Item { Layout.fillHeight: true }
    }

    Connections {
        target: backend
        function onNotification(level, message) {
            if (level === "error") {
                errorDialog.open()
            } else {
                toast.text = message
                toast.open()
            }
        }
        function onIncomingLocalTransfer(request) {
            window.incomingLocalRequest = request
            incomingLocalDialog.open()
        }
        function onPreviewReady() {
            var selected = ({})
            var actions = ({})
            for (var i = 0; i < backend.previewItems.length; ++i) {
                var item = backend.previewItems[i]
                selected[item.source] = true
                actions[item.source] = item.action === "ask_conflict" ? "rename" : item.action
            }
            window.previewSelection = selected
            window.previewActions = actions
            transferPreviewDialog.open()
        }
    }
    Dialog {
        id: errorDialog
        objectName: "errorDialog"
        modal: true
        width: Math.min(window.width - 80, 680)
        anchors.centerIn: parent
        title: t("error_details_title")
        standardButtons: Dialog.Close
        contentItem: ColumnLayout {
            spacing: 14
            TextArea {
                Layout.fillWidth: true
                text: backend.lastError || t("unknown_error")
                color: ink
                readOnly: true
                wrapMode: TextEdit.Wrap
                textFormat: Text.PlainText
                selectByMouse: true
                background: Item { }
            }
            PillButton {
                text: t("copy_error")
                onClicked: {
                    backend.copyToClipboard(backend.lastError || t("unknown_error"))
                    toast.text = t("error_copied")
                    toast.open()
                }
            }
        }
    }
    Dialog {
        id: transferPreviewDialog
        modal: true
        width: Math.min(window.width - 80, 920)
        height: Math.min(window.height - 80, 620)
        anchors.centerIn: parent
        title: t("preview_title")
        standardButtons: Dialog.Cancel | Dialog.Ok
        onAccepted: backend.startPreviewedTransfer(previewDecisions())
        contentItem: ColumnLayout {
            spacing: 10
            Label {
                Layout.fillWidth: true
                text: tf("preview_summary", {
                    count: backend.previewSummary.files,
                    required: backend.previewSummary.requiredText,
                    free: backend.previewSummary.freeText
                })
                color: backend.previewSummary.enoughSpace ? ink : "#ff453a"
                font.weight: Font.DemiBold
            }
            Label { Layout.fillWidth: true; text: tf("preview_destination", {path: backend.previewSummary.target || ""}); color: muted; elide: Text.ElideMiddle }
            ListView {
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true
                model: backend.previewItems; spacing: 6
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width; height: 72; radius: 10
                    color: dark ? "#55313d58" : "#99ffffff"
                    RowLayout {
                        anchors.fill: parent; anchors.margins: 9; spacing: 10
                        CheckBox {
                            checked: window.previewSelection[modelData.source] !== false
                            onToggled: window.previewSelection[modelData.source] = checked
                        }
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 2
                            Label { Layout.fillWidth: true; text: modelData.source; color: ink; elide: Text.ElideMiddle }
                            Label { Layout.fillWidth: true; text: "→ " + modelData.destination; color: muted; elide: Text.ElideMiddle; font.pixelSize: 11 }
                            Label { Layout.fillWidth: true; text: modelData.reason || modelData.action; color: accent; elide: Text.ElideRight; font.pixelSize: 11 }
                        }
                        Label { text: modelData.sizeText; color: muted }
                        ComboBox {
                            visible: modelData.action === "ask_conflict"
                            model: [t("policy_rename"), t("policy_skip"), t("policy_replace"), t("policy_newer")]
                            onActivated: window.previewActions[modelData.source] = ["rename", "skip", "replace", "newer"][currentIndex]
                        }
                    }
                }
            }
            Label { text: t("preview_qt_help"); color: muted; wrapMode: Text.Wrap; Layout.fillWidth: true }
        }
        function previewDecisions() {
            var decisions = []
            for (var i = 0; i < backend.previewItems.length; ++i) {
                var item = backend.previewItems[i]
                decisions.push({
                    source: item.source,
                    selected: window.previewSelection[item.source] !== false,
                    action: window.previewActions[item.source] || item.action
                })
            }
            return decisions
        }
    }
    Dialog {
        id: incomingLocalDialog
        modal: true
        width: 560
        anchors.centerIn: parent
        title: t("incoming_local_title")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: {
            var requestId = String(window.incomingLocalRequest.request_id || "")
            backend.respondLocalRequest(requestId, true, window.localReceiveDestination)
            window.incomingLocalRequest = ({})
        }
        onRejected: {
            var requestId = String(window.incomingLocalRequest.request_id || "")
            if (requestId.length > 0)
                backend.respondLocalRequest(requestId, false, "")
            window.incomingLocalRequest = ({})
        }
        contentItem: ColumnLayout {
            spacing: 12
            Label {
                text: tf("incoming_local_question", {
                    sender: window.incomingLocalRequest.sender_name || t("no_device"),
                    count: window.incomingLocalRequest.files || 0
                })
                color: ink; wrapMode: Text.Wrap; Layout.fillWidth: true
            }
            Label {
                text: tf("incoming_destination", {path: window.localReceiveDestination || t("no_folder_selected")})
                color: window.localReceiveDestination ? muted : "#ff453a"
                wrapMode: Text.Wrap; Layout.fillWidth: true
            }
            PillButton { text: t("choose_receive_folder"); onClicked: incomingDestinationDialog.open() }
        }
    }
    FolderDialog {
        id: incomingDestinationDialog
        title: t("choose_local_receive_folder")
        onAccepted: window.localReceiveDestination = decodeURIComponent(selectedFolder.toString().replace(/^file:\/\/\//, ""))
    }
    Popup { id: toast; property alias text: toastLabel.text; x: width; y: 70; width: 360; padding: 14; closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside; background: Rectangle { radius: 12; color: glassStrong; border.color: "#88ffffff" } contentItem: Label { id: toastLabel; color: ink; wrapMode: Text.Wrap } }
}
