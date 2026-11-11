import os
import sys

# Ensure libtorrent can find its OpenSSL DLLs on Windows
if sys.platform == "win32":
    if getattr(sys, "frozen", False):
        # Running as PyInstaller bundle
        _candidates = [os.path.join(sys._MEIPASS, "libtorrent")]
    else:
        # Running from source: libtorrent's package folder wherever pip put it
        # (system Python or a venv), found without importing it.
        import importlib.util
        _spec = importlib.util.find_spec("libtorrent")
        _candidates = list(_spec.submodule_search_locations or []) if _spec else []
    for _candidate in _candidates:
        if os.path.isdir(_candidate):
            os.add_dll_directory(_candidate)
            break


def main():
    # 1. Read any .torrent files named on the command line before importing
    #    anything slow: some launcher tools delete them ~1 s later.
    from app.cli_args import collect_args, log
    items, arg_errors = collect_args(sys.argv[1:])

    # 2. If a copy is already running, hand everything to it and exit.
    from app.single_instance import acquire_primary, send_to_primary
    if not acquire_primary():
        reply = send_to_primary(items, show=not items and not arg_errors)
        if reply is None:
            log(f"ERROR could not reach the running copy; {len(items)} item(s) not added")
            sys.exit(2)
        # The running copy logs its own add errors; only log ours here.
        for e in arg_errors:
            log(f"ERROR {e}")
        sys.exit(1 if arg_errors or reply.get("errors") else 0)

    # 3. We're the primary: start listening right away so later launches
    #    queue up while the rest of the app loads.
    from PyQt6.QtWidgets import QApplication
    from app.constants import APP_NAME, APP_VERSION, ORG_NAME
    from app.cli_args import install_crash_logging
    exe = sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__)
    log(f"START {APP_NAME} {APP_VERSION} ({exe})"
        + (f" with {len(items)} item(s) from the command line" if items else ""))
    install_crash_logging()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setOrganizationName(ORG_NAME)
    app.setQuitOnLastWindowClosed(False)
    from PyQt6.QtGui import QIcon
    from ui.tray import app_icon_path
    app.setWindowIcon(QIcon(app_icon_path()))  # main window, dialogs, taskbar

    from app.single_instance import InstanceServer
    server = InstanceServer()
    if not server.listening:
        log(f"WARNING single-instance server failed to listen: {server.error_string()}")
    for e in arg_errors:
        log(f"ERROR {e}")

    from app.settings import SettingsManager
    from app.engine import TorrentEngine
    from app.models import TorrentListModel
    from ui.main_window import MainWindow
    from ui.tray import TrayApp

    settings = SettingsManager()
    from ui.appearance import Appearance
    appearance = Appearance(app, settings)
    appearance.apply("start")   # before any window exists, so nothing flashes
    engine = TorrentEngine(settings)
    model = TorrentListModel()

    window = MainWindow(engine, settings)
    window.set_model(model)
    window.set_appearance(appearance)

    tray = TrayApp(window, engine, app)
    tray.show()

    # Wire signals
    engine.stats_updated.connect(model.refresh)
    engine.stats_updated.connect(window.update_statusbar)
    engine.torrent_finished.connect(tray.notify_finished)
    engine.already_downloaded.connect(tray.notify_already_downloaded)

    # Wire toolbar actions
    window._act_add.triggered.connect(lambda: window.on_add_torrent())
    window._act_pause.triggered.connect(lambda: window.on_pause())
    window._act_resume.triggered.connect(lambda: window.on_resume())
    window._act_remove.triggered.connect(lambda: window.on_remove())
    window._act_settings.triggered.connect(lambda: window.on_settings())

    # Wire tray add action
    tray._act_add.triggered.connect(lambda: window.on_add_torrent())

    def show_window():
        window.show()
        window.raise_()
        window.activateWindow()

    def handle_request(request_items, show):
        """Add torrents silently (like BitTorrent with the add dialog off)."""
        added, errors = [], []
        for item in request_items:
            label = item.get("name") or item.get("uri", "")[:80]
            try:
                if item["kind"] == "magnet":
                    result = engine.add_torrent(item["uri"])
                else:
                    result = engine.add_torrent_bytes(item["data"])
                if result is None:   # already downloaded; engine logged it
                    continue
                added.append(item.get("name") or "Magnet link")
                log(f"ADDED {label}")
            except Exception as e:
                errors.append(f"{label}: {e}")
                log(f"ERROR {label}: {e}")
        if added:
            tray.notify_added(added)
        if show:
            show_window()
        return {"added": len(added), "errors": errors}

    server.handler = handle_request

    # --- Watch folder ---
    from app.folder_watcher import FolderWatcher
    watcher = FolderWatcher(engine, settings)
    watcher.state_changed.connect(window.set_watch_state)
    watcher.state_changed.connect(tray.set_watch_state)
    watcher.torrents_added.connect(tray.notify_added)
    window.settings_applied.connect(watcher.apply_settings)

    def toggle_watch(checked: bool):
        """Start/Stop from the tray menu or toolbar."""
        if checked and not os.path.isdir(settings.watch_folder):
            from PyQt6.QtWidgets import QFileDialog
            folder = QFileDialog.getExistingDirectory(window, "Select Folder to Watch")
            if not folder:
                watcher.state_changed.emit(False, settings.watch_folder)  # untick
                return
            settings.watch_folder = os.path.normpath(folder)
        watcher.set_enabled(checked)

    window._act_watch.toggled.connect(toggle_watch)
    tray._act_watch.toggled.connect(toggle_watch)

    # Cleanup on quit
    app.aboutToQuit.connect(watcher.stop)
    app.aboutToQuit.connect(engine.shutdown)

    engine.start()
    if items:
        handle_request(items, show=False)
    watcher.apply_settings()  # resumes watching if it was on last time

    if not settings.start_minimized:
        window.show()

    code = app.exec()
    log(f"QUIT (exit code {code})")
    sys.exit(code)


if __name__ == "__main__":
    main()
