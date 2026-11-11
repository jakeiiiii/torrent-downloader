import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QSpinBox,
    QPushButton, QFileDialog, QDialogButtonBox, QHBoxLayout, QCheckBox,
    QGroupBox, QLabel, QMessageBox, QComboBox,
)


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)

        layout = QVBoxLayout(self)

        # ---- General section ----
        general_group = QGroupBox("General")
        form = QFormLayout()

        dir_layout = QHBoxLayout()
        self._dir_edit = QLineEdit(settings.download_dir)
        self._dir_browse = QPushButton("Browse...")
        self._dir_browse.clicked.connect(self._browse_dir)
        dir_layout.addWidget(self._dir_edit)
        dir_layout.addWidget(self._dir_browse)
        form.addRow("Download Directory:", dir_layout)

        self._use_inc = QCheckBox("Keep unfinished downloads in a separate folder")
        self._use_inc.setChecked(settings.use_incomplete_dir)
        self._use_inc.setToolTip(
            "On: downloads go to the folder below and move to the download folder when\n"
            "finished, so everything in the download folder is complete. If a name is\n"
            "already taken there, the new one is called \"Name (2)\".\n"
            "Off: downloads go straight into the download folder.\n"
            "Changing this affects new downloads; ones already in the folder below\n"
            "still move out when they finish.")
        form.addRow("Unfinished Downloads:", self._use_inc)

        inc_layout = QHBoxLayout()
        self._inc_edit = QLineEdit(settings.incomplete_dir)
        self._inc_edit.setPlaceholderText(r"Default: _incomplete inside the download folder")
        self._inc_edit.setToolTip(
            "Unfinished downloads are kept here and moved to the download folder\n"
            "when they finish, so everything in the download folder is complete.\n"
            "Leave empty to use an _incomplete folder inside the download folder.")
        inc_browse = QPushButton("Browse...")
        inc_browse.clicked.connect(self._browse_inc_dir)
        inc_layout.addWidget(self._inc_edit)
        inc_layout.addWidget(inc_browse)
        form.addRow("Incomplete Folder:", inc_layout)
        for w in (self._inc_edit, inc_browse):
            w.setEnabled(self._use_inc.isChecked())
            self._use_inc.toggled.connect(w.setEnabled)

        self._max_concurrent = QSpinBox()
        self._max_concurrent.setRange(1, 50)
        self._max_concurrent.setValue(settings.max_concurrent_downloads)
        form.addRow("Max Concurrent Downloads:", self._max_concurrent)

        self._port = QSpinBox()
        self._port.setRange(1024, 65535)
        self._port.setValue(settings.listen_port_start)
        form.addRow("Listen Port:", self._port)

        self._start_min = QCheckBox()
        self._start_min.setChecked(settings.start_minimized)
        form.addRow("Start Minimized:", self._start_min)

        self._remove_on_complete = QCheckBox("Remove from list when complete (keep files, don't seed)")
        self._remove_on_complete.setChecked(settings.remove_on_complete)
        self._remove_on_complete.setToolTip(
            "When a download reaches 100%, it's removed from the list as if you chose\n"
            "\"Remove (keep files)\". Turning this on also removes torrents already seeding.")
        form.addRow("When Complete:", self._remove_on_complete)

        general_group.setLayout(form)
        layout.addWidget(general_group)

        # ---- Appearance section ----
        from ui.appearance import THEMES, FONT_MIN, FONT_MAX
        look_group = QGroupBox("Appearance")
        look_form = QFormLayout()
        self._theme = QComboBox()
        for key, label in THEMES.items():
            self._theme.addItem(label, key)
        self._theme.setCurrentIndex(max(0, self._theme.findData(settings.theme)))
        look_form.addRow("Theme:", self._theme)
        self._font_size = QSpinBox()
        self._font_size.setRange(FONT_MIN - 1, FONT_MAX)   # lowest value = "Default"
        self._font_size.setSpecialValueText("Default")
        self._font_size.setSuffix(" pt")
        self._font_size.setValue(settings.font_size or FONT_MIN - 1)
        self._font_size.setToolTip("Also in the View menu: Ctrl + larger, Ctrl - smaller, Ctrl 0 default.")
        look_form.addRow("Text Size:", self._font_size)
        look_group.setLayout(look_form)
        layout.addWidget(look_group)

        # ---- Speed Limits section ----
        speed_group = QGroupBox("Speed Limits")
        speed_form = QFormLayout()

        self._max_dl = QSpinBox()
        self._max_dl.setRange(0, 999999)
        self._max_dl.setSuffix(" KB/s")
        self._max_dl.setSpecialValueText("Unlimited")
        self._max_dl.setValue(settings.max_download_speed)
        speed_form.addRow("Max Download Speed:", self._max_dl)

        self._max_ul = QSpinBox()
        self._max_ul.setRange(0, 999999)
        self._max_ul.setSuffix(" KB/s")
        self._max_ul.setSpecialValueText("Unlimited")
        self._max_ul.setValue(settings.max_upload_speed)
        speed_form.addRow("Max Upload Speed:", self._max_ul)

        speed_group.setLayout(speed_form)
        layout.addWidget(speed_group)

        # ---- Performance section ----
        perf_group = QGroupBox("Performance")
        perf_form = QFormLayout()

        self._conn_limit = QSpinBox()
        self._conn_limit.setRange(50, 2000)
        self._conn_limit.setValue(settings.connections_limit)
        self._conn_limit.setToolTip("Total connections across all torrents. Higher = more peers but more RAM/CPU.")
        perf_form.addRow("Global Connection Limit:", self._conn_limit)

        self._conn_per_torrent = QSpinBox()
        self._conn_per_torrent.setRange(20, 500)
        self._conn_per_torrent.setValue(settings.connections_per_torrent)
        self._conn_per_torrent.setToolTip("Max peers per individual torrent. 100-200 is good for most cases.")
        perf_form.addRow("Connections Per Torrent:", self._conn_per_torrent)

        perf_hint = QLabel(
            "Higher values use more RAM/CPU but may improve speed.\n"
            "If your connection is under 100 Mbps, lower values are fine."
        )
        perf_hint.setStyleSheet("color: gray;")
        perf_form.addRow(perf_hint)

        perf_group.setLayout(perf_form)
        layout.addWidget(perf_group)

        # ---- Watch Folder section ----
        watch_group = QGroupBox("Watch Folder")
        watch_form = QFormLayout()

        self._watch_enabled = QCheckBox("Add .torrent files placed in this folder, then delete them")
        self._watch_enabled.setChecked(settings.watch_folder_enabled)
        watch_form.addRow(self._watch_enabled)

        watch_dir_layout = QHBoxLayout()
        self._watch_edit = QLineEdit(settings.watch_folder)
        self._watch_edit.setPlaceholderText(r"e.g. D:\Torrents\watch")
        self._watch_browse = QPushButton("Browse...")
        self._watch_browse.clicked.connect(self._browse_watch_dir)
        watch_dir_layout.addWidget(self._watch_edit)
        watch_dir_layout.addWidget(self._watch_browse)
        watch_form.addRow("Folder:", watch_dir_layout)

        watch_hint = QLabel(
            "Files are deleted only after they're added. Files that can't be added\n"
            "stay in the folder. You can also start/stop watching from the tray menu."
        )
        watch_hint.setStyleSheet("color: gray;")
        watch_form.addRow(watch_hint)

        watch_group.setLayout(watch_form)
        layout.addWidget(watch_group)

        # ---- Buttons ----
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Download Directory")
        if path:
            self._dir_edit.setText(path)

    def _browse_inc_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Folder for Incomplete Downloads", self._inc_edit.text().strip())
        if path:
            self._inc_edit.setText(os.path.normpath(path))

    def _browse_watch_dir(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select Folder to Watch", self._watch_edit.text().strip())
        if path:
            self._watch_edit.setText(os.path.normpath(path))

    def _on_accept(self):
        watch_dir = self._watch_edit.text().strip()
        if self._watch_enabled.isChecked() and not os.path.isdir(watch_dir):
            QMessageBox.warning(self, "Watch Folder",
                                "Choose an existing folder to watch, or turn watching off.")
            return
        self._settings.watch_folder = watch_dir
        self._settings.watch_folder_enabled = self._watch_enabled.isChecked()
        self._settings.download_dir = self._dir_edit.text().strip()
        self._settings.incomplete_dir = self._inc_edit.text().strip()
        self._settings.use_incomplete_dir = self._use_inc.isChecked()
        self._settings.theme = self._theme.currentData()
        size = self._font_size.value()
        self._settings.font_size = 0 if size < self._font_size.minimum() + 1 else size
        self._settings.max_download_speed = self._max_dl.value()
        self._settings.max_upload_speed = self._max_ul.value()
        self._settings.max_concurrent_downloads = self._max_concurrent.value()
        self._settings.listen_port_start = self._port.value()
        self._settings.start_minimized = self._start_min.isChecked()
        self._settings.remove_on_complete = self._remove_on_complete.isChecked()
        self._settings.connections_limit = self._conn_limit.value()
        self._settings.connections_per_torrent = self._conn_per_torrent.value()
        self.accept()
