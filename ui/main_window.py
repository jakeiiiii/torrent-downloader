import os
import subprocess
from PyQt6.QtWidgets import (
    QMainWindow, QTableView, QToolBar, QStatusBar, QHeaderView, QMessageBox, QMenu,
    QLabel,
)
from PyQt6.QtCore import Qt, QPoint, QSortFilterProxyModel, pyqtSignal
from PyQt6.QtGui import QAction
from app.models import TorrentItem, format_speed, COL_NAME, COL_STATUS, COL_NUM, SORT_ROLE
from ui.delegates import StatusBarDelegate


class MainWindow(QMainWindow):
    settings_applied = pyqtSignal()

    def __init__(self, engine, settings):
        super().__init__()
        self._engine = engine
        self._settings = settings
        self._appearance = None   # set_appearance()

        self.setWindowTitle("Torrent Downloader")
        self.setMinimumSize(900, 500)

        self._build_toolbar()
        self._build_table()
        self._build_statusbar()

        self.setAcceptDrops(True)

    def _build_toolbar(self):
        toolbar = QToolBar("Main Toolbar")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self._act_add = QAction("Add Torrent", self)
        self._act_pause = QAction("Pause", self)
        self._act_resume = QAction("Resume", self)
        self._act_remove = QAction("Remove", self)
        self._act_settings = QAction("Settings", self)
        self._act_watch = QAction("Watch Folder", self)
        self._act_watch.setCheckable(True)
        self._act_watch.setToolTip("Start/stop adding .torrent files from the watch folder")

        toolbar.addAction(self._act_add)
        toolbar.addSeparator()
        toolbar.addAction(self._act_pause)
        toolbar.addAction(self._act_resume)
        toolbar.addAction(self._act_remove)
        toolbar.addSeparator()
        toolbar.addAction(self._act_watch)
        toolbar.addAction(self._act_settings)
        toolbar.addSeparator()
        self._build_view_menu(toolbar)

    def _build_view_menu(self, toolbar):
        """"View" button: theme, and text size (also Ctrl + / Ctrl - / Ctrl 0)."""
        from PyQt6.QtGui import QActionGroup, QKeySequence
        from ui.appearance import THEMES
        menu = QMenu("View", self)
        self._theme_actions = {}
        group = QActionGroup(self)
        for key, label in THEMES.items():
            act = menu.addAction(label)
            act.setCheckable(True)
            group.addAction(act)
            act.triggered.connect(lambda _=False, k=key: self._appearance and self._appearance.set_theme(k))
            self._theme_actions[key] = act
        menu.addSeparator()
        for label, keys, fn in (
            ("Larger Text", [QKeySequence.StandardKey.ZoomIn, "Ctrl+="], lambda: self._step_font(1)),
            ("Smaller Text", [QKeySequence.StandardKey.ZoomOut], lambda: self._step_font(-1)),
            ("Default Text Size", ["Ctrl+0"], lambda: self._appearance and self._appearance.reset_font()),
        ):
            act = menu.addAction(label)
            act.setShortcuts([QKeySequence(k) for k in keys])
            act.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
            act.triggered.connect(fn)
            self.addAction(act)   # shortcuts work without opening the menu
        self._font_label = menu.addAction("")
        self._font_label.setEnabled(False)

        # A plain toolbar button that pops the menu up below itself. (A menu
        # button gets a drop-down arrow from the windows11 style: a low "v"
        # that looks out of place next to the other buttons.)
        self._view_menu = menu
        act_view = QAction("View", self)
        act_view.setToolTip("Theme and text size")
        act_view.triggered.connect(lambda: self._view_menu.exec(
            self._toolbar.widgetForAction(act_view).mapToGlobal(
                self._toolbar.widgetForAction(act_view).rect().bottomLeft())))
        toolbar.addAction(act_view)
        self._toolbar = toolbar

    def set_appearance(self, appearance):
        self._appearance = appearance
        appearance.changed.connect(self._appearance_changed)
        self._appearance_changed()

    def _step_font(self, delta: int):
        if self._appearance:
            self._appearance.step_font(delta)

    def _appearance_changed(self):
        self._theme_actions[self._settings.theme].setChecked(True)
        size = self._appearance.font_size
        self._font_label.setText(f"Text size: {size} pt")
        # Rows and the progress-bar column grow and shrink with the text.
        # The app's new font: widgets get it a moment later, via an event.
        from PyQt6.QtGui import QFontMetrics
        from PyQt6.QtWidgets import QApplication
        fm = QFontMetrics(QApplication.font())
        self._table.verticalHeader().setDefaultSectionSize(fm.height() + 10)
        self._table.setColumnWidth(COL_STATUS, max(200, fm.horizontalAdvance("Downloading 100.0%") + 40))
        self._table.setColumnWidth(COL_NUM, max(30, fm.horizontalAdvance("0000") + 12))

    def _build_table(self):
        self._table = QTableView()
        self._table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSortingEnabled(True)
        self._table.sortByColumn(COL_NUM, Qt.SortOrder.AscendingOrder)

        self._status_delegate = StatusBarDelegate()
        self._table.setItemDelegateForColumn(COL_STATUS, self._status_delegate)

        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        self.setCentralWidget(self._table)

    def _build_statusbar(self):
        self._statusbar = QStatusBar()
        self.setStatusBar(self._statusbar)
        self._statusbar.showMessage("Ready")
        self._watch_label = QLabel("Watch folder: off")
        self._statusbar.addPermanentWidget(self._watch_label)

    def set_watch_state(self, watching: bool, folder: str):
        """Keep the toolbar toggle and status label in sync with the watcher."""
        self._act_watch.blockSignals(True)
        self._act_watch.setChecked(watching)
        self._act_watch.blockSignals(False)
        if watching:
            self._watch_label.setText(f"Watching: {folder}")
        elif folder and not os.path.isdir(folder):
            self._watch_label.setText(f"Watch folder not found: {folder}")
        else:
            self._watch_label.setText("Watch folder: off")

    def set_model(self, model):
        self._source_model = model
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(model)
        self._proxy.setSortRole(SORT_ROLE)
        self._proxy.setDynamicSortFilter(True)
        self._table.setModel(self._proxy)
        header = self._table.horizontalHeader()
        # # column: narrow fixed
        header.setSectionResizeMode(COL_NUM, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_NUM, 30)
        # Name column: stretch to fill
        header.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
        # Status column: fixed width for progress bar
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(COL_STATUS, 200)
        # All other columns: fit content
        for col in range(model.columnCount()):
            if col not in (COL_NUM, COL_NAME, COL_STATUS):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)

    def selected_info_hashes(self) -> list[str]:
        hashes = []
        for proxy_index in self._table.selectionModel().selectedRows():
            row = self._proxy.mapToSource(proxy_index).row()
            ih = self._source_model.info_hash_at(row)
            if ih:
                hashes.append(ih)
        return hashes

    def update_statusbar(self, items: list[TorrentItem]):
        total_dl = sum(i.download_rate for i in items)
        total_ul = sum(i.upload_rate for i in items)
        active = sum(1 for i in items if i.state == "downloading")
        self._statusbar.showMessage(
            f"D: {format_speed(total_dl)}  |  U: {format_speed(total_ul)}  |  "
            f"Active: {active}  |  Total: {len(items)}"
        )

    def on_add_torrent(self):
        from ui.add_torrent_dialog import AddTorrentDialog
        dialog = AddTorrentDialog(self._settings.download_dir, self)
        if dialog.exec():
            source, save_path = dialog.result_data()
            if source:
                try:
                    self._engine.add_torrent(source, save_path)
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to add torrent:\n{e}")

    def on_pause(self):
        self._pause(self.selected_info_hashes())

    def on_resume(self):
        self._resume(self.selected_info_hashes())

    def _pause(self, hashes):
        for ih in hashes:
            self._engine.pause_torrent(ih)

    def _resume(self, hashes):
        for ih in hashes:
            self._engine.resume_torrent(ih)

    def on_remove(self):
        hashes = self.selected_info_hashes()
        if not hashes:
            return
        what = "this torrent" if len(hashes) == 1 else f"these {len(hashes)} torrents"
        reply = QMessageBox.question(
            self, "Remove Torrent",
            f"Remove {what}? Downloaded files are kept.\n"
            "Use the right-click menu to remove with files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._remove(hashes, False)

    def on_settings(self):
        from ui.settings_dialog import SettingsDialog
        dialog = SettingsDialog(self._settings, self)
        if dialog.exec():
            self._engine.apply_settings()
            if self._appearance:
                self._appearance.apply("settings")
            self.settings_applied.emit()

    # --- Right-click context menu ---
    def _show_context_menu(self, pos: QPoint):
        hashes = self.selected_info_hashes()
        if not hashes:
            return

        menu = QMenu(self)
        menu.addAction("Pause", lambda: self._pause(hashes))
        menu.addAction("Resume", lambda: self._resume(hashes))
        menu.addSeparator()
        open_act = menu.addAction("Open Folder", lambda: self._open_folder(hashes[0]))
        open_act.setEnabled(len(hashes) == 1)
        menu.addSeparator()
        menu.addAction("Remove (keep files)", lambda: self._remove(hashes, False))
        menu.addAction("Remove (delete files)", lambda: self._remove_with_files(hashes))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _open_folder(self, info_hash: str):
        item = self._source_model.item_by_hash(info_hash)
        if item and os.path.isdir(item.save_path):
            subprocess.Popen(["explorer", os.path.normpath(item.save_path)])

    def _remove(self, hashes, delete_files: bool):
        for ih in hashes:
            self._engine.remove_torrent(ih, delete_files=delete_files)

    def _remove_with_files(self, hashes):
        what = "this torrent" if len(hashes) == 1 else f"these {len(hashes)} torrents"
        reply = QMessageBox.warning(
            self, "Remove Torrent",
            f"Are you sure you want to remove {what} AND delete the downloaded files?\n\n"
            "Files and folders that other torrents also use are kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._remove(hashes, True)

    # --- Drag and drop .torrent files ---
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().endswith(".torrent"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.endswith(".torrent"):
                try:
                    self._engine.add_torrent(path)
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to add torrent:\n{e}")

    def closeEvent(self, event):
        self.hide()
        event.ignore()
