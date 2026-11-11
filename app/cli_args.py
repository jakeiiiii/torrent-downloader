"""Command-line handling that must run before anything slow is imported.

Some launcher tools start us with a .torrent path and delete the
file about a second later, so the file's bytes are read here, first thing,
before Qt or libtorrent are loaded. Standard library only.
"""
import os
import time

MAX_TORRENT_BYTES = 32 * 1024 * 1024


def collect_args(args: list[str]) -> tuple[list[dict], list[str]]:
    """Turn argv into add requests.

    Returns (items, errors). Each item is
    {"kind": "torrent", "name": str, "data": bytes} or
    {"kind": "magnet", "uri": str}.
    """
    items, errors = [], []
    for arg in args:
        if arg.lower().startswith("magnet:"):
            items.append({"kind": "magnet", "uri": arg})
            continue
        try:
            if os.path.getsize(arg) > MAX_TORRENT_BYTES:
                errors.append(f"{arg}: file too large to be a .torrent")
                continue
            with open(arg, "rb") as f:
                data = f.read()
            items.append({"kind": "torrent", "name": os.path.basename(arg), "data": data})
        except OSError as e:
            errors.append(f"{arg}: {e.strerror or e}")
    return items, errors


def log_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TorrentDownloader")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "cli.log")


LOG_MAX_BYTES = 10 * 1024 * 1024   # then cli.log -> cli.log.1 (one old copy kept)


def log(message: str):
    """Everything the app does goes to cli.log (the exe has no console):
    adds, state changes, progress, moves, removes, errors and crashes."""
    try:
        path = log_path()
        try:
            if os.path.getsize(path) > LOG_MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{os.getpid()}] {message}\n")
    except OSError:
        pass


def install_crash_logging():
    """Uncaught exceptions (including ones inside Qt slots, which PyQt6
    would otherwise turn into a silent abort) go to cli.log with a traceback."""
    import sys
    import threading
    import traceback

    def hook(exc_type, exc, tb):
        log("CRASH (uncaught error):\n" + "".join(traceback.format_exception(exc_type, exc, tb)).rstrip())

    sys.excepthook = hook
    threading.excepthook = lambda args: hook(args.exc_type, args.exc_value, args.exc_traceback)
