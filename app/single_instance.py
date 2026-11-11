"""Keep one running copy and hand later launches over to it.

The first process to start takes a named lock and becomes the primary; it
listens on a local socket (a named pipe on Windows). Any later launch, such as
`TorrentDownloader.exe file.torrent` from another program, sends its
torrents to the primary, waits for the reply, and exits.

Message framing: 4-byte big-endian length, then UTF-8 JSON.
  request: {"show": bool, "items": [{"kind": "torrent", "name", "data": base64}
                                    | {"kind": "magnet", "uri"}]}
  reply:   {"added": int, "errors": [str]}
"""
import base64
import ctypes
import getpass
import json
import os
import struct
import sys
import time

from PyQt6.QtCore import QObject
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

MAX_MESSAGE_BYTES = 256 * 1024 * 1024
_ERROR_ALREADY_EXISTS = 183


def _instance_key() -> str:
    try:
        user = os.environ.get("USERNAME") or getpass.getuser()
    except Exception:
        user = "user"
    # TORRENTDOWNLOADER_INSTANCE lets a test copy run beside the real one.
    suffix = os.environ.get("TORRENTDOWNLOADER_INSTANCE", "")
    return f"TorrentDownloader-{user}{'-' + suffix if suffix else ''}"


SERVER_NAME = _instance_key() + "-ipc"

# Kept alive for the life of the process; the OS releases them on exit/crash.
_mutex_handle = None
_lock_file = None


def acquire_primary() -> bool:
    """True if this process is the first copy running for this user."""
    global _mutex_handle, _lock_file
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.CreateMutexW(None, False, "Local\\" + _instance_key())
        if not handle:
            return True  # can't tell; behave as before rather than refuse to start
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _mutex_handle = handle
        return True

    from PyQt6.QtCore import QDir, QLockFile
    _lock_file = QLockFile(os.path.join(QDir.tempPath(), _instance_key() + ".lock"))
    _lock_file.setStaleLockTime(0)
    return _lock_file.tryLock(0)


def _frame(obj) -> bytes:
    payload = json.dumps(obj).encode("utf-8")
    return struct.pack(">I", len(payload)) + payload


def _encode_item(item: dict) -> dict:
    if item["kind"] == "torrent":
        return {"kind": "torrent", "name": item["name"],
                "data": base64.b64encode(item["data"]).decode("ascii")}
    return dict(item)


def _decode_item(item: dict) -> dict:
    if item.get("kind") == "torrent":
        return {"kind": "torrent", "name": str(item.get("name", "")),
                "data": base64.b64decode(item["data"])}
    if item.get("kind") == "magnet":
        return {"kind": "magnet", "uri": str(item["uri"])}
    raise ValueError(f"unknown item kind: {item.get('kind')!r}")


def send_to_primary(items: list[dict], show: bool,
                    connect_timeout_s: float = 30, reply_timeout_s: float = 60):
    """Send items to the running copy. Returns its reply dict, or None if it
    couldn't be reached. Retries while the primary is still starting up."""
    sock = QLocalSocket()
    deadline = time.monotonic() + connect_timeout_s
    while True:
        sock.connectToServer(SERVER_NAME)
        if sock.waitForConnected(1000):
            break
        sock.abort()
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.2)

    sock.write(_frame({"show": show, "items": [_encode_item(i) for i in items]}))
    while sock.bytesToWrite() > 0:
        if not sock.waitForBytesWritten(5000):
            return None

    buf = bytearray()
    deadline = time.monotonic() + reply_timeout_s
    while True:
        if len(buf) >= 4:
            size = struct.unpack(">I", buf[:4])[0]
            if len(buf) >= 4 + size:
                return json.loads(bytes(buf[4:4 + size]))
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0 or not sock.waitForReadyRead(remaining_ms):
            # The reply may have arrived together with the disconnect.
            buf += bytes(sock.readAll())
            if len(buf) >= 4:
                size = struct.unpack(">I", buf[:4])[0]
                if len(buf) >= 4 + size:
                    return json.loads(bytes(buf[4:4 + size]))
            return None
        buf += bytes(sock.readAll())


class InstanceServer(QObject):
    """Runs in the primary. `handler(items, show) -> {"added", "errors"}` must
    be set before the event loop starts; requests are handled on the GUI thread."""

    def __init__(self, handler=None):
        super().__init__()
        self.handler = handler
        self._buffers: dict[QLocalSocket, bytearray] = {}
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(SERVER_NAME)  # clears a stale socket file on Unix
        self.listening = self._server.listen(SERVER_NAME)
        self._server.newConnection.connect(self._on_new_connection)

    def error_string(self) -> str:
        return self._server.errorString()

    def _on_new_connection(self):
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            self._buffers[sock] = bytearray()
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._on_disconnected(s))
            self._on_ready_read(sock)

    def _on_ready_read(self, sock: QLocalSocket):
        buf = self._buffers.get(sock)
        if buf is None:
            return
        buf += bytes(sock.readAll())
        if len(buf) < 4:
            return
        size = struct.unpack(">I", buf[:4])[0]
        if size > MAX_MESSAGE_BYTES:
            self._buffers.pop(sock, None)
            sock.abort()
            return
        if len(buf) < 4 + size:
            return
        self._buffers.pop(sock, None)

        try:
            request = json.loads(bytes(buf[4:4 + size]))
            items = [_decode_item(i) for i in request.get("items", [])]
            result = self.handler(items, bool(request.get("show")))
        except Exception as e:
            result = {"added": 0, "errors": [f"request failed: {e}"]}

        sock.write(_frame(result))
        sock.flush()
        sock.disconnectFromServer()

    def _on_disconnected(self, sock: QLocalSocket):
        self._buffers.pop(sock, None)
        sock.deleteLater()
