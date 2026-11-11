import os
import sys
from PyQt6.QtCore import QSettings
from app.constants import (
    APP_NAME, ORG_NAME, DEFAULT_PORT_START, DEFAULT_PORT_END,
    DEFAULT_MAX_CONCURRENT,
)

INCOMPLETE_SUBDIR = "_incomplete"


def _bdecode(data: bytes, i: int = 0):
    """Minimal bencode decoder, enough to read BitTorrent's settings.dat."""
    c = data[i:i + 1]
    if c == b"i":
        end = data.index(b"e", i)
        return int(data[i + 1:end]), end + 1
    if c == b"l":
        i, out = i + 1, []
        while data[i:i + 1] != b"e":
            v, i = _bdecode(data, i)
            out.append(v)
        return out, i + 1
    if c == b"d":
        i, out = i + 1, {}
        while data[i:i + 1] != b"e":
            k, i = _bdecode(data, i)
            v, i = _bdecode(data, i)
            out[k] = v
        return out, i + 1
    colon = data.index(b":", i)
    n = int(data[i:colon])
    return data[colon + 1:colon + 1 + n], colon + 1 + n


def bittorrent_download_dir() -> str | None:
    """BitTorrent/uTorrent's "put new downloads in" folder, if it's set and exists."""
    appdata = os.environ.get("APPDATA", "")
    for client in ("bittorrent", "uTorrent"):
        path = os.path.join(appdata, client, "settings.dat")
        try:
            with open(path, "rb") as f:
                settings, _ = _bdecode(f.read())
            if not settings.get(b"dir_active_download_flag"):
                continue
            folder = settings.get(b"dir_active_download", b"").decode("utf-8")
            if folder and os.path.isdir(folder):
                return folder
        except (OSError, ValueError, IndexError, AttributeError, UnicodeDecodeError):
            continue
    return None


def default_settings_path() -> str:
    base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TorrentDownloader")
    return os.path.join(base, "settings.ini")


def _registry_key_exists(org: str, app: str) -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\{org}\{app}"))
        return True
    except OSError:
        return False


def _delete_registry_key(org: str, app: str):
    """Remove HKCU\\Software\\<org>\\<app>, then <org> if nothing else is under it."""
    if sys.platform != "win32":
        return
    import winreg
    for path in (rf"Software\{org}\{app}", rf"Software\{org}"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)  # fails if it has subkeys
        except OSError:
            pass


def migrate_from_registry(ini_path: str, org: str = ORG_NAME, app: str = APP_NAME) -> bool:
    """One-time move of settings that older versions kept in the registry.

    Runs only while ini_path doesn't exist yet. Copies every value into the
    ini file, and deletes the registry key only once the file is written.
    Returns True if anything was moved.
    """
    if os.path.exists(ini_path) or not _registry_key_exists(org, app):
        return False
    # Only open it through QSettings once we know it exists: QSettings creates
    # the key just by being opened.
    reg = QSettings(org, app)  # native format = registry on Windows
    keys = reg.allKeys()
    if not keys:
        del reg
        _delete_registry_key(org, app)
        return False
    os.makedirs(os.path.dirname(ini_path), exist_ok=True)
    ini = QSettings(ini_path, QSettings.Format.IniFormat)
    for key in keys:
        ini.setValue(key, reg.value(key))
    ini.sync()
    if ini.status() != QSettings.Status.NoError or not os.path.exists(ini_path):
        return False  # keep the registry copy; try again next start
    reg.clear()
    reg.sync()
    del reg
    _delete_registry_key(org, app)
    return True


class SettingsManager:
    def __init__(self):
        # Settings live in %APPDATA%\TorrentDownloader\settings.ini, next to
        # resume_data\ and cli.log. TORRENTDOWNLOADER_SETTINGS=<path.ini> uses
        # another file instead (tests).
        ini_path = os.environ.get("TORRENTDOWNLOADER_SETTINGS")
        if not ini_path:
            ini_path = default_settings_path()
            migrate_from_registry(ini_path)
            os.makedirs(os.path.dirname(ini_path), exist_ok=True)
        self._settings = QSettings(ini_path, QSettings.Format.IniFormat)
        # Removed setting: libtorrent 2.0 has no disk cache, so it did nothing.
        self._settings.remove("cache_size_mb")

    # --- download directory ---
    @property
    def download_dir(self) -> str:
        if not self._settings.contains("download_dir"):
            # First run: use BitTorrent's folder so downloads land where they
            # always have, and remember it so later BitTorrent changes don't move it.
            default = bittorrent_download_dir() or os.path.join(os.path.expanduser("~"), "Downloads")
            self._settings.setValue("download_dir", default)
        return self._settings.value("download_dir", type=str)

    @download_dir.setter
    def download_dir(self, value: str):
        self._settings.setValue("download_dir", value)

    # --- speed limits (KB/s, 0 = unlimited) ---
    @property
    def max_download_speed(self) -> int:
        return self._settings.value("max_download_speed", 0, type=int)

    @max_download_speed.setter
    def max_download_speed(self, value: int):
        self._settings.setValue("max_download_speed", value)

    @property
    def max_upload_speed(self) -> int:
        return self._settings.value("max_upload_speed", 0, type=int)

    @max_upload_speed.setter
    def max_upload_speed(self, value: int):
        self._settings.setValue("max_upload_speed", value)

    # --- listen ports ---
    @property
    def listen_port_start(self) -> int:
        return self._settings.value("listen_port_start", DEFAULT_PORT_START, type=int)

    @listen_port_start.setter
    def listen_port_start(self, value: int):
        self._settings.setValue("listen_port_start", value)

    @property
    def listen_port_end(self) -> int:
        return self._settings.value("listen_port_end", DEFAULT_PORT_END, type=int)

    @listen_port_end.setter
    def listen_port_end(self, value: int):
        self._settings.setValue("listen_port_end", value)

    # --- max concurrent downloads ---
    @property
    def max_concurrent_downloads(self) -> int:
        return self._settings.value("max_concurrent_downloads", DEFAULT_MAX_CONCURRENT, type=int)

    @max_concurrent_downloads.setter
    def max_concurrent_downloads(self, value: int):
        self._settings.setValue("max_concurrent_downloads", value)

    # --- performance ---
    @property
    def connections_limit(self) -> int:
        return self._settings.value("connections_limit", 500, type=int)

    @connections_limit.setter
    def connections_limit(self, value: int):
        self._settings.setValue("connections_limit", value)

    @property
    def connections_per_torrent(self) -> int:
        return self._settings.value("connections_per_torrent", 200, type=int)

    @connections_per_torrent.setter
    def connections_per_torrent(self, value: int):
        self._settings.setValue("connections_per_torrent", value)

    # --- watch folder (auto-add .torrent files dropped into a folder) ---
    @property
    def watch_folder(self) -> str:
        return self._settings.value("watch_folder", "", type=str)

    @watch_folder.setter
    def watch_folder(self, value: str):
        self._settings.setValue("watch_folder", value)

    @property
    def watch_folder_enabled(self) -> bool:
        return self._settings.value("watch_folder_enabled", False, type=bool)

    @watch_folder_enabled.setter
    def watch_folder_enabled(self, value: bool):
        self._settings.setValue("watch_folder_enabled", value)

    # --- incomplete downloads folder ---
    @property
    def incomplete_dir(self) -> str:
        """Where unfinished downloads go. Empty = "_incomplete" inside the
        torrent's destination folder (see incomplete_dir_for)."""
        return self._settings.value("incomplete_dir", "", type=str)

    @incomplete_dir.setter
    def incomplete_dir(self, value: str):
        self._settings.setValue("incomplete_dir", value)

    @property
    def use_incomplete_dir(self) -> bool:
        """Off = download straight into the download folder (no _incomplete,
        no move when done, no "Name (2)" renames)."""
        return self._settings.value("use_incomplete_dir", True, type=bool)

    @use_incomplete_dir.setter
    def use_incomplete_dir(self, value: bool):
        self._settings.setValue("use_incomplete_dir", value)

    def incomplete_dir_for(self, destination: str) -> str:
        return self.incomplete_dir or os.path.join(destination, INCOMPLETE_SUBDIR)

    # --- when a download completes ---
    @property
    def remove_on_complete(self) -> bool:
        """Remove finished torrents from the list (files kept) instead of seeding."""
        return self._settings.value("remove_on_complete", False, type=bool)

    @remove_on_complete.setter
    def remove_on_complete(self, value: bool):
        self._settings.setValue("remove_on_complete", value)

    # --- appearance ---
    @property
    def theme(self) -> str:
        """"system" (follow Windows), "light" or "dark"."""
        value = self._settings.value("theme", "system", type=str)
        return value if value in ("system", "light", "dark") else "system"

    @theme.setter
    def theme(self, value: str):
        self._settings.setValue("theme", value)

    @property
    def font_size(self) -> int:
        """Text size in points; 0 = Windows' default."""
        return self._settings.value("font_size", 0, type=int)

    @font_size.setter
    def font_size(self, value: int):
        self._settings.setValue("font_size", value)

    # --- misc ---
    @property
    def start_minimized(self) -> bool:
        return self._settings.value("start_minimized", False, type=bool)

    @start_minimized.setter
    def start_minimized(self, value: bool):
        self._settings.setValue("start_minimized", value)
