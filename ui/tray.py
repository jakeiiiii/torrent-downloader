from PyQt6.QtWidgets import QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtCore import QSize
import sys
import os


def app_icon_path() -> str:
    """resources/icons/app.ico, from source or inside the PyInstaller bundle."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "resources", "icons", "app.ico")


class TrayApp(QSystemTrayIcon):
    def __init__(self, main_window, engine, app):
        super().__init__()
        self._main_window = main_window
        self._engine = engine
        self._app = app

        self._setup_icon()
        self._build_menu()
        self.activated.connect(self._on_activated)

    def _setup_icon(self):
        icon_path = app_icon_path()
        if os.path.exists(icon_path):
            self.setIcon(QIcon(icon_path))
        else:
            from PyQt6.QtWidgets import QApplication
            self.setIcon(QApplication.style().standardIcon(
                QApplication.style().StandardPixmap.SP_ArrowDown
            ))
        self.setToolTip("Torrent Downloader")

    def _build_menu(self):
        menu = QMenu()

        self._act_open = QAction("Open", self)
        self._act_open.triggered.connect(self._show_window)
        menu.addAction(self._act_open)

        self._act_add = QAction("Add Torrent...", self)
        menu.addAction(self._act_add)

        self._act_watch = QAction("Watch Folder", self)
        self._act_watch.setCheckable(True)
        menu.addAction(self._act_watch)

        menu.addSeparator()

        self._act_quit = QAction("Quit", self)
        self._act_quit.triggered.connect(self._app.quit)
        menu.addAction(self._act_quit)

        self.setContextMenu(menu)

    def _on_activated(self, reason):
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_window()

    def _show_window(self):
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def set_watch_state(self, watching: bool, folder: str):
        self._act_watch.blockSignals(True)
        self._act_watch.setChecked(watching)
        self._act_watch.blockSignals(False)
        self.setToolTip("Torrent Downloader" + (f"\nWatching: {folder}" if watching else ""))

    def notify_added(self, names: list[str]):
        """Batch launches (e.g. 40 files from a launcher tool) arrive one
        process at a time; collect them and show one notification."""
        if not hasattr(self, "_added_names"):
            from PyQt6.QtCore import QTimer
            self._added_names = []
            self._added_timer = QTimer(self)
            self._added_timer.setSingleShot(True)
            self._added_timer.timeout.connect(self._show_added)
        self._added_names.extend(names)
        self._added_timer.start(2000)

    def _show_added(self):
        names, self._added_names = self._added_names, []
        if not names:
            return
        text = names[0] if len(names) == 1 else f"{len(names)} torrents added"
        self.showMessage("Torrent added", text, QSystemTrayIcon.MessageIcon.Information, 4000)

    def notify_already_downloaded(self, name: str, folder: str):
        self.showMessage(
            "Already downloaded",
            f"{name} wasn't downloaded again: it's already in {folder}.",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def notify_finished(self, name: str):
        self.showMessage(
            "Download Complete",
            f"{name} has finished downloading.",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )
