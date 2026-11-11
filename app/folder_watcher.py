"""Watch a folder and add every .torrent file dropped into it.

A file is deleted only after it has been added to the engine. Files that
can't be added (corrupt, unreadable) are left where they are and logged once.
"""
import os
import time

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal

from app.cli_args import MAX_TORRENT_BYTES, log

# Let a file sit this long unchanged before reading it, so we never grab a
# half-written copy.
SETTLE_SECONDS = 1.5
# Backup scan in case a change notification is missed (e.g. network drives).
RESCAN_MS = 10_000
DEBOUNCE_MS = 500


class FolderWatcher(QObject):
    state_changed = pyqtSignal(bool, str)      # watching?, folder
    torrents_added = pyqtSignal(list)          # names added in one scan

    def __init__(self, engine, settings):
        super().__init__()
        self._engine = engine
        self._settings = settings
        self._folder = ""
        self._watching = False
        self._failed: dict[str, tuple] = {}    # path -> (mtime, size) when it failed

        self._fs = QFileSystemWatcher(self)
        self._fs.directoryChanged.connect(lambda _: self._debounce.start())

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self.scan)

        self._rescan = QTimer(self)
        self._rescan.setInterval(RESCAN_MS)
        self._rescan.timeout.connect(self.scan)

        # Retry soon for files that weren't settled yet.
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self.scan)

    # --- control ---
    @property
    def watching(self) -> bool:
        return self._watching

    @property
    def folder(self) -> str:
        return self._folder

    def apply_settings(self):
        """Start or stop to match the saved settings."""
        if self._settings.watch_folder_enabled and self._settings.watch_folder:
            self.start(self._settings.watch_folder)
        else:
            self.stop()

    def set_enabled(self, enabled: bool):
        """Turn watching on/off and remember the choice."""
        self._settings.watch_folder_enabled = enabled
        self.apply_settings()

    def start(self, folder: str):
        folder = os.path.normpath(folder)
        if self._watching and folder == self._folder:
            return
        self.stop(emit=False)
        if not os.path.isdir(folder):
            log(f"WATCH folder not found, not watching: {folder}")
            self.state_changed.emit(False, folder)
            return
        self._folder = folder
        self._failed.clear()
        self._fs.addPath(folder)
        self._rescan.start()
        self._watching = True
        log(f"WATCH started: {folder}")
        self.state_changed.emit(True, folder)
        self.scan()  # pick up anything that arrived while we weren't watching

    def stop(self, emit: bool = True):
        was_watching = self._watching
        if self._fs.directories():
            self._fs.removePaths(self._fs.directories())
        self._rescan.stop()
        self._debounce.stop()
        self._settle.stop()
        self._watching = False
        if was_watching:
            log(f"WATCH stopped: {self._folder}")
        if emit:
            self.state_changed.emit(False, self._folder)

    # --- work ---
    def scan(self):
        if not self._watching:
            return
        try:
            entries = list(os.scandir(self._folder))
        except OSError as e:
            log(f"WATCH can't read {self._folder}: {e}")
            return

        now = time.time()
        added, waiting = [], False
        for entry in sorted(entries, key=lambda e: e.name):
            if not entry.name.lower().endswith(".torrent") or not entry.is_file():
                continue
            try:
                st = entry.stat()
            except OSError:
                continue  # removed meanwhile (e.g. by another program)
            if now - st.st_mtime < SETTLE_SECONDS:
                waiting = True
                continue
            state = (st.st_mtime, st.st_size)
            if self._failed.get(entry.path) == state:
                continue  # already failed in this exact state; don't spam the log
            if st.st_size > MAX_TORRENT_BYTES:
                self._fail(entry.path, state, "file too large to be a .torrent")
                continue

            try:
                with open(entry.path, "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                continue
            except OSError as e:
                self._fail(entry.path, state, e.strerror or str(e))
                continue

            try:
                skipped = self._engine.add_torrent_bytes(data) is None
            except Exception as e:
                self._fail(entry.path, state, str(e))
                continue

            # Added (or already in the list) — now it's safe to remove.
            try:
                os.remove(entry.path)
            except FileNotFoundError:
                pass
            except OSError as e:
                log(f"WATCH added but couldn't delete {entry.name}: {e}")
            self._failed.pop(entry.path, None)
            if skipped:   # downloaded before and still there; engine logged it
                log(f"WATCH SKIPPED (already downloaded) {entry.name}")
                continue
            log(f"WATCH ADDED {entry.name}")
            added.append(entry.name)

        if added:
            self.torrents_added.emit(added)
        if waiting:
            self._settle.start(int(SETTLE_SECONDS * 1000) + 200)

    def _fail(self, path: str, state: tuple, reason: str):
        # Retried automatically if the file changes (e.g. a copy finishes).
        self._failed[path] = state
        log(f"WATCH ERROR {os.path.basename(path)} (left in folder): {reason}")
