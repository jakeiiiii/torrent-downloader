from dataclasses import dataclass
from PyQt6.QtCore import Qt, QAbstractTableModel, QModelIndex


@dataclass
class TorrentItem:
    info_hash: str
    name: str
    progress: float          # 0.0 to 1.0
    download_rate: int       # bytes/sec
    upload_rate: int         # bytes/sec
    num_seeds: int
    num_peers: int
    total_size: int          # bytes
    downloaded: int          # bytes
    state: str               # "downloading", "seeding", "paused", "checking", "queued", "metadata"
    eta_seconds: int         # -1 if unknown
    save_path: str


def format_size(b: int) -> str:
    if b < 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}" if unit != "B" else f"{b} B"
        b /= 1024
    return f"{b:.1f} PB"


def format_speed(bps: int) -> str:
    if bps <= 0:
        return ""
    return format_size(bps) + "/s"


def format_eta(seconds: int) -> str:
    if seconds < 0:
        return "\u221e"
    if seconds == 0:
        return ""
    h, remainder = divmod(seconds, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _status_text(item: TorrentItem) -> str:
    """BitTorrent-style status text: 'Downloading 82.6%', 'Seeding', 'Paused 45.2%', etc."""
    pct = f"{item.progress * 100:.1f} %"
    state = item.state
    if state == "downloading":
        return f"Downloading {pct}"
    if state == "seeding":
        return "Seeding"
    if state == "paused":
        return f"Paused {pct}" if item.progress < 1.0 else "Paused"
    if state == "queued":
        return f"Queued {pct}" if item.progress < 1.0 else "Queued"
    if state == "checking":
        return f"Checking {pct}"
    if state == "metadata":
        return "Downloading metadata..."
    return state.capitalize()


# Column layout matching BitTorrent:
#   #  |  Name  |  Size  |  Status  |  Down Speed  |  Up Speed  |  ETA  |  Seeds/Peers
COLUMNS = [
    "#", "Name", "Size", "Status",
    "Down Speed", "Up Speed", "ETA", "Seeds/Peers",
]

COL_NUM = 0
COL_NAME = 1
COL_SIZE = 2
COL_STATUS = 3
COL_DOWN = 4
COL_UP = 5
COL_ETA = 6
COL_SEEDS_PEERS = 7

SORT_ROLE = Qt.ItemDataRole.UserRole + 1


class TorrentListModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._items: list[TorrentItem] = []

    def refresh(self, items: list[TorrentItem]):
        """Update rows in place so the view keeps its selection and scroll
        position. A full reset (which clears selection) only happens if the
        existing rows were reordered, which the engine never does."""
        old_ids = [i.info_hash for i in self._items]
        new_ids = [i.info_hash for i in items]
        new_set = set(new_ids)

        # 1. Remove rows that disappeared (bottom-up so indexes stay valid).
        for row in range(len(old_ids) - 1, -1, -1):
            if old_ids[row] not in new_set:
                self.beginRemoveRows(QModelIndex(), row, row)
                del self._items[row]
                del old_ids[row]
                self.endRemoveRows()

        # 2. Surviving rows must be a prefix of the new list, in order.
        if new_ids[:len(old_ids)] != old_ids:
            self.beginResetModel()
            self._items = list(items)
            self.endResetModel()
            return

        # 3. Update surviving rows' values.
        if old_ids:
            self._items[:len(old_ids)] = items[:len(old_ids)]
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(old_ids) - 1, len(COLUMNS) - 1),
            )

        # 4. Append new rows.
        if len(items) > len(old_ids):
            self.beginInsertRows(QModelIndex(), len(old_ids), len(items) - 1)
            self._items.extend(items[len(old_ids):])
            self.endInsertRows()

    def info_hash_at(self, row: int) -> str | None:
        if 0 <= row < len(self._items):
            return self._items[row].info_hash
        return None

    def item_by_hash(self, info_hash: str) -> TorrentItem | None:
        for item in self._items:
            if item.info_hash == info_hash:
                return item
        return None

    def rowCount(self, parent=QModelIndex()):
        return len(self._items)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._items):
            return None

        item = self._items[index.row()]
        col = index.column()

        if role == Qt.ItemDataRole.DisplayRole:
            if col == COL_NUM:
                return str(index.row() + 1)
            if col == COL_NAME:
                return item.name
            if col == COL_SIZE:
                return format_size(item.total_size)
            if col == COL_STATUS:
                return _status_text(item)
            if col == COL_DOWN:
                return format_speed(item.download_rate)
            if col == COL_UP:
                return format_speed(item.upload_rate)
            if col == COL_ETA:
                return format_eta(item.eta_seconds)
            if col == COL_SEEDS_PEERS:
                return f"{item.num_seeds}.{item.num_peers}"

        # UserRole on Status column: (progress float, state string) for the delegate
        if role == Qt.ItemDataRole.UserRole and col == COL_STATUS:
            return (item.progress, item.state)

        # Raw values for sorting (the display strings don't sort correctly)
        if role == SORT_ROLE:
            if col == COL_NUM:
                return index.row()
            if col == COL_NAME:
                return item.name.lower()
            if col == COL_SIZE:
                return item.total_size
            if col == COL_STATUS:
                return item.progress
            if col == COL_DOWN:
                return item.download_rate
            if col == COL_UP:
                return item.upload_rate
            if col == COL_ETA:
                # Unknown ETA sorts last
                return item.eta_seconds if item.eta_seconds >= 0 else 2**62
            if col == COL_SEEDS_PEERS:
                return item.num_seeds * 100000 + item.num_peers

        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == COL_NAME:
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

        return None
