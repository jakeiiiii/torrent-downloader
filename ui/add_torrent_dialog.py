from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFileDialog, QGroupBox, QDialogButtonBox,
)


class AddTorrentDialog(QDialog):
    def __init__(self, default_save_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Torrent")
        self.setMinimumWidth(500)

        self._source = ""
        self._save_path = default_save_path

        layout = QVBoxLayout(self)

        # --- Torrent file section ---
        file_group = QGroupBox("Torrent File")
        file_layout = QHBoxLayout()
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText("Path to .torrent file")
        self._file_edit.setReadOnly(True)
        self._file_browse = QPushButton("Browse...")
        self._file_browse.clicked.connect(self._browse_file)
        file_layout.addWidget(self._file_edit)
        file_layout.addWidget(self._file_browse)
        file_group.setLayout(file_layout)
        layout.addWidget(file_group)

        # --- Magnet link section ---
        magnet_group = QGroupBox("Magnet Link")
        magnet_layout = QHBoxLayout()
        self._magnet_edit = QLineEdit()
        self._magnet_edit.setPlaceholderText("Paste magnet link here...")
        magnet_layout.addWidget(self._magnet_edit)
        magnet_group.setLayout(magnet_layout)
        layout.addWidget(magnet_group)

        # --- Save path ---
        save_group = QGroupBox("Save To")
        save_layout = QHBoxLayout()
        self._save_edit = QLineEdit(default_save_path)
        self._save_browse = QPushButton("Browse...")
        self._save_browse.clicked.connect(self._browse_save)
        save_layout.addWidget(self._save_edit)
        save_layout.addWidget(self._save_browse)
        save_group.setLayout(save_layout)
        layout.addWidget(save_group)

        # --- Buttons ---
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Torrent File", "", "Torrent Files (*.torrent);;All Files (*)"
        )
        if path:
            self._file_edit.setText(path)
            self._magnet_edit.clear()

    def _browse_save(self):
        path = QFileDialog.getExistingDirectory(self, "Select Download Directory")
        if path:
            self._save_edit.setText(path)

    def _on_accept(self):
        magnet = self._magnet_edit.text().strip()
        file_path = self._file_edit.text().strip()

        if magnet:
            self._source = magnet
        elif file_path:
            self._source = file_path
        else:
            return  # nothing entered

        self._save_path = self._save_edit.text().strip()
        self.accept()

    def result_data(self) -> tuple[str, str]:
        return self._source, self._save_path
