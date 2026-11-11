import json
import os
import time
import libtorrent as lt
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from app.constants import POLL_INTERVAL_MS, RESUME_DATA_DIR, RESUME_SAVE_INTERVAL_MS
from app.models import TorrentItem
from app import safe_delete
from app.cli_args import log
from app.settings import INCOMPLETE_SUBDIR

# save_info_dict: without it libtorrent 2.0 omits the metadata, and restored
# torrents have to re-fetch it from peers (or stall if there are none).
_RESUME_FLAGS = (
    lt.save_resume_flags_t.flush_disk_cache
    | lt.save_resume_flags_t.save_info_dict
)
SHUTDOWN_SAVE_TIMEOUT_S = 10
DELETE_RETRY_MS = 500
DELETE_MAX_ATTEMPTS = 40   # ~20 s

# Map libtorrent states to readable strings
_STATE_MAP = {
    lt.torrent_status.states.checking_files: "checking",
    lt.torrent_status.states.downloading_metadata: "metadata",
    lt.torrent_status.states.downloading: "downloading",
    lt.torrent_status.states.finished: "seeding",
    lt.torrent_status.states.seeding: "seeding",
    lt.torrent_status.states.checking_resume_data: "checking",
}


MOVE_MAX_ATTEMPTS = 3
PROGRESS_LOG_MS = 5 * 60 * 1000   # PROGRESS lines in cli.log
STALL_MINUTES = 10


def _tag(handle) -> str:
    """'Name [id]' for log lines; the id matches the _incomplete\\<id> folder."""
    try:
        name = handle.status().name or "(no name yet)"
    except Exception:
        name = "?"
    return f"{name} [{_hash_key(handle)[:8]}]"


def _mb(n: int) -> str:
    return f"{n / 1024 ** 3:.2f} GB" if n >= 1024 ** 3 else f"{n / 1024 ** 2:.1f} MB"


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"
COMPLETED_FILE = "completed.json"   # beside resume_data\


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _hash_key(handle) -> str:
    """Same id for a torrent whether it came from a .torrent file or a magnet
    (v1 or v2), used for remembering finished torrents."""
    return str(handle.info_hashes().get_best())


def _remove_incomplete_if_empty(folder: str):
    """Delete an "_incomplete" folder the app made, once no download is left
    in it (it's made again when the next download starts). A folder chosen in
    Settings → Incomplete Folder is the user's, so it's never deleted."""
    if os.path.basename(os.path.normpath(folder)) != INCOMPLETE_SUBDIR:
        return
    try:
        os.rmdir(folder)   # only succeeds if it's empty
    except OSError:
        return
    log(f"CLEANUP removed empty {folder}")


def _is_own_subfolder(path: str, handle) -> bool:
    """True for the torrent's own _incomplete\\<first 8 of its id> folder."""
    # v1 or v2: a hybrid torrent added as a v1-only magnet was named by v1.
    ih = handle.info_hashes()
    ids = {str(h)[:8] for h in (ih.v1, ih.v2) if not h.is_all_zeros()}
    return os.path.basename(os.path.normpath(path)) in ids


def _real_files(handle) -> list[tuple[int, str]]:
    """(index, path inside the save folder) of each file, skipping pad files
    (never written to disk)."""
    fs = handle.torrent_file().files()
    return [(i, fs.file_path(i)) for i in range(fs.num_files())
            if not fs.file_flags(i) & lt.file_storage.flag_pad_file]


def _top(path: str) -> str:
    return path.replace("/", "\\").split("\\", 1)[0]


def _key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _free_names(files, taken) -> list[tuple[int, str]]:
    """New paths for every file with the top-level name changed to
    "Name (n)" / "name (n).ext", lowest n >= 2 for which taken(name) is False."""
    old_top = _top(files[0][1])
    single = len(files) == 1 and files[0][1] == old_top
    stem, ext = os.path.splitext(old_top) if single else (old_top, "")
    n = 2
    while taken(f"{stem} ({n}){ext}"):
        n += 1
    new_top = f"{stem} ({n}){ext}"
    return [(i, new_top + p.replace("/", "\\")[len(old_top):]) for i, p in files]


class TorrentEngine(QObject):
    stats_updated = pyqtSignal(list)
    torrent_finished = pyqtSignal(str)  # emits torrent name
    already_downloaded = pyqtSignal(str, str)  # name, folder its files are in

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._handles: dict[str, lt.torrent_handle] = {}
        self._finished_set: set[str] = set()
        # Unfinished torrents download into an "incomplete" folder and move to
        # their destination when done, so anything in the download folder is
        # finished. info_hash -> destination; persisted as <hash>.dest beside
        # the resume data. Torrents without an entry are already in place.
        self._destinations: dict[str, str] = {}
        self._moving: set[str] = set()        # renaming or moving right now
        self._renaming: dict[str, int] = {}   # info_hash -> renames still to finish
        self._move_attempts: dict[str, int] = {}
        # Destination paths of moves in progress. Two torrents finishing at
        # once would otherwise both see a name as free and collide.
        self._claimed: dict[str, set[str]] = {}
        self._move_flags: dict[str, object] = {}
        # Logging: stall detection, and tracker errors already logged.
        self._last_progress: dict[str, tuple[int, float]] = {}
        self._stalled: set[str] = set()
        self._tracker_errors_logged: set[tuple] = set()
        self._added_at: dict[str, float] = {}
        self._move_failed: set[str] = set()   # gave up; don't retry every second

        self._session = lt.session()
        self._configure_session()

        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)

        self._resume_timer = QTimer(self)
        self._resume_timer.timeout.connect(self._save_all_resume_data)

        self._progress_timer = QTimer(self)
        self._progress_timer.timeout.connect(self._log_progress)

        # Deletes for removed torrents; retried while libtorrent still has
        # a file open (Windows won't delete open files).
        self._pending_deletes: list[dict] = []
        self._delete_timer = QTimer(self)
        self._delete_timer.setSingleShot(True)
        self._delete_timer.setInterval(DELETE_RETRY_MS)
        self._delete_timer.timeout.connect(self._run_pending_deletes)

        self._resume_dir = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")),
            "TorrentDownloader", RESUME_DATA_DIR,
        )
        os.makedirs(self._resume_dir, exist_ok=True)

        # Every torrent that finished, kept after it leaves the list, so the
        # same torrent added again isn't downloaded a second time while its
        # files are still there. info hash -> {name, path, files: [[rel, size]]}
        self._completed_path = os.path.join(os.path.dirname(self._resume_dir), COMPLETED_FILE)
        try:
            with open(self._completed_path, encoding="utf-8") as f:
                self._completed: dict[str, dict] = json.load(f)
        except (OSError, ValueError):
            self._completed = {}

    def _configure_session(self):
        settings = {
            "listen_interfaces": f"0.0.0.0:{self._settings.listen_port_start}",

            # --- Peer discovery: find as many sources as possible ---
            "enable_dht": True,
            "enable_lsd": True,                  # local service discovery (LAN peers)
            "announce_to_all_tiers": True,        # query all tracker tiers, not just tier 0
            "announce_to_all_trackers": True,      # query every tracker in every tier

            # --- Connection tuning ---
            "connections_limit": self._settings.connections_limit,  # global max connections (default 200)
            "connection_speed": 50,                # half-open outgoing attempts per second (default 30)
            "peer_connect_timeout": 7,             # fail fast on unresponsive peers (default 15)
            "request_timeout": 30,                 # drop slow peers sooner (default 60)
            "peer_timeout": 60,                    # inactivity timeout (default 120)
            "min_reconnect_time": 5,               # retry peers faster (default 60)
            "max_peerlist_size": 5000,             # remember more peers (default 3000)
            "torrent_connect_boost": 50,           # burst connections when a new torrent starts (default 30)

            # --- Request pipeline: keep peers busy ---
            "max_allowed_in_request_queue": 2500,  # inbound request depth (default 2000)
            "max_out_request_queue": 750,          # outbound request depth (default 500)
            "request_queue_time": 5,               # seconds of data to keep queued (default 3)

            # --- Disk ---
            # No cache settings: libtorrent 2.0 has no disk cache of its own
            # (it maps files into memory and Windows caches them), so
            # cache_size / use_read_cache / *_cache_line_size do nothing.
            "max_queued_disk_bytes": 4 * 1024 * 1024,  # 4 MB disk queue (default 1 MB)

            # --- Choking / unchoke: reward fast peers ---
            "choking_algorithm": 0,                # 0 = fixed-slots (default)
            "unchoke_slots_limit": 12,             # upload to more peers = more reciprocation (default 8)
            "unchoke_interval": 10,                # re-evaluate faster (default 15)
            "num_optimistic_unchoke_slots": 2,     # try more unknown peers (default 0 = auto)

            # --- uTP: dual-stack TCP + uTP ---
            "enable_incoming_utp": True,
            "enable_outgoing_utp": True,
            # Share bandwidth between TCP and uTP by peer count (libtorrent's
            # default). prefer_tcp throttles uTP whenever any TCP peer is
            # connected, and most BitTorrent/uTorrent peers use uTP.
            "mixed_mode_algorithm": int(lt.bandwidth_mixed_algo_t.peer_proportional),

            # --- DHT tuning ---
            "dht_announce_interval": 600,          # announce more often (default 900)
            "dht_max_peers_reply": 200,            # get more peers per DHT query (default 100)
            "dht_search_branching": 10,            # wider DHT search (default 5)
            "dht_upload_rate_limit": 16000,        # allow more DHT traffic (default 8000)

            # Default is errors only. Storage: storage_moved(_failed)_alert;
            # status: metadata_received_alert (both handled in _process_alerts).
            "alert_mask": int(lt.alert.category_t.error_notification
                              | lt.alert.category_t.storage_notification
                              | lt.alert.category_t.status_notification),
        }

        # User speed limits
        max_dl = self._settings.max_download_speed
        max_ul = self._settings.max_upload_speed
        settings["download_rate_limit"] = max_dl * 1024 if max_dl > 0 else 0
        settings["upload_rate_limit"] = max_ul * 1024 if max_ul > 0 else 0

        self._session.apply_settings(settings)

        # Encryption: prefer encrypted connections to avoid ISP throttling,
        # but allow plaintext so we don't lose peers that don't support it
        pe = lt.pe_settings()
        pe.in_enc_policy = lt.enc_policy.pe_enabled
        pe.out_enc_policy = lt.enc_policy.pe_enabled
        pe.allowed_enc_level = lt.enc_level.pe_both
        pe.prefer_rc4 = True
        self._session.set_pe_settings(pe)

    def start(self):
        log(f"SETTINGS: {self._settings_summary()}")
        self._restore_resume_data()
        # Left empty by an older version, or by a crash before cleanup.
        _remove_incomplete_if_empty(os.path.join(self._settings.download_dir, INCOMPLETE_SUBDIR))
        self._poll_timer.start(POLL_INTERVAL_MS)
        self._resume_timer.start(RESUME_SAVE_INTERVAL_MS)
        self._progress_timer.start(PROGRESS_LOG_MS)

    def shutdown(self):
        self._log_progress()
        log(f"SHUTDOWN: saving {len(self._handles)} torrent(s)")
        self._progress_timer.stop()
        self._poll_timer.stop()
        self._resume_timer.stop()
        # Stop transfers first so the resume data we write is final, then
        # block until libtorrent has delivered every requested save.
        self._session.pause()
        outstanding = self._save_all_resume_data(force=True)
        deadline = time.monotonic() + SHUTDOWN_SAVE_TIMEOUT_S
        while outstanding > 0:
            remaining_ms = int((deadline - time.monotonic()) * 1000)
            if remaining_ms <= 0:
                break
            if self._session.wait_for_alert(remaining_ms) is None:
                break
            outstanding -= self._process_alerts()

        # Finish any "Remove (delete files)" still waiting on a locked file.
        self._delete_timer.stop()
        for _ in range(10):
            if not self._pending_deletes:
                break
            self._run_pending_deletes()
            self._delete_timer.stop()
            time.sleep(0.3)
        for job in self._pending_deletes:
            safe_delete.give_up(job, log)
        self._pending_deletes = []

    def add_torrent(self, source: str, save_path: str = "") -> str | None:
        if source.startswith("magnet:"):
            params = lt.parse_magnet_uri(source)
        else:
            params = lt.add_torrent_params()
            params.ti = lt.torrent_info(source)
        return self._add(params, save_path)

    def add_torrent_bytes(self, data: bytes, save_path: str = "") -> str | None:
        """Add from the contents of a .torrent file (the file itself may
        already be gone, e.g. deleted by the tool that launched us)."""
        decoded = lt.bdecode(data)
        if not isinstance(decoded, dict):
            raise ValueError("not a valid .torrent file")
        params = lt.add_torrent_params()
        params.ti = lt.torrent_info(decoded)
        return self._add(params, save_path)

    def _add(self, params, save_path: str) -> str | None:
        """Returns the torrent's id, or None if it wasn't added because it was
        downloaded before and its files are still there."""
        ih = params.ti.info_hashes() if params.ti is not None else params.info_hashes
        key = str(ih.get_best())
        if not any(_hash_key(h) == key for h in self._handles.values()):
            done = self._already_downloaded(key)
            if done:
                log(f"ALREADY DOWNLOADED (skipped): {done['name']} [{key[:8]}] in {done['path']}, "
                    f"finished {done.get('finished', '?')}")
                self.already_downloaded.emit(done["name"], done["path"])
                return None

        destination = save_path or self._settings.download_dir
        incomplete = self._settings.incomplete_dir_for(destination)
        # Always the incomplete folder, even if something of the same name is
        # already in the destination: never write unfinished files there.
        use_incomplete = self._settings.use_incomplete_dir and not _same_path(incomplete, destination)
        if use_incomplete:
            # Each torrent in its own subfolder (start of its info hash), so
            # two torrents with the same names can't write into each other.
            params.save_path = os.path.join(incomplete, key[:8])
        else:
            params.save_path = destination
        params.flags |= lt.torrent_flags.auto_managed

        handle = self._session.add_torrent(params)
        info_hash = str(handle.info_hash())

        if info_hash in self._handles:
            log(f"ADD ignored (already in the list): {_tag(handle)}")
            return info_hash

        if use_incomplete:
            self._set_destination(info_hash, destination)
        size = f", {_mb(params.ti.total_size())}" if params.ti is not None else " (magnet, fetching metadata)"
        log(f"ADD {_tag(handle)}{size} -> downloading in {params.save_path}, "
            f"finished files go to {destination}")

        # Per-torrent connection limit: allow more peers per torrent
        handle.set_max_connections(self._settings.connections_per_torrent)
        handle.set_max_uploads(max(4, self._settings.connections_per_torrent // 10))

        self._handles[info_hash] = handle
        self._added_at[info_hash] = time.monotonic()
        self._enforce_queue_limit()
        # Persist right away so a crash or quit before the next periodic
        # save doesn't lose the torrent. Magnets are saved again once
        # their metadata arrives (see _process_alerts).
        handle.save_resume_data(_RESUME_FLAGS)
        return info_hash

    def remove_torrent(self, info_hash: str, delete_files: bool = False, why: str = "by you"):
        handle = self._handles.pop(info_hash, None)
        if handle:
            st = handle.status()
            log(f"REMOVE ({why}, {'deleting files' if delete_files else 'files kept'}): "
                f"{_tag(handle)} at {st.progress * 100:.1f}%, in {st.save_path}")
            self._last_progress.pop(info_hash, None)
            self._stalled.discard(info_hash)
            self._added_at.pop(info_hash, None)
            # Not lt.options_t.delete_files: that would also delete files and
            # folders other torrents use. See app/safe_delete.py.
            job = safe_delete.plan(handle, info_hash) if delete_files else None
            save_path = handle.status().save_path
            if delete_files and _is_own_subfolder(save_path, handle):
                # Its own subfolder of the incomplete folder; remove it once
                # the files are gone.
                self._rmdir_when_empty(save_path)
            self._session.remove_torrent(handle)
            if job:
                self._pending_deletes.append(job)
                self._run_pending_deletes()
            self._finished_set.discard(info_hash)
            self._set_destination(info_hash, None)
            self._moving.discard(info_hash)
            self._move_failed.discard(info_hash)
            self._renaming.pop(info_hash, None)
            self._move_attempts.pop(info_hash, None)
            self._claimed.pop(info_hash, None)
            resume_path = os.path.join(self._resume_dir, f"{info_hash}.fastresume")
            if os.path.exists(resume_path):
                os.remove(resume_path)

    # --- incomplete folder -> destination ---
    def _dest_path(self, info_hash: str) -> str:
        return os.path.join(self._resume_dir, f"{info_hash}.dest")

    def _set_destination(self, info_hash: str, destination: str | None):
        path = self._dest_path(info_hash)
        if destination is None:
            self._destinations.pop(info_hash, None)
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            return
        self._destinations[info_hash] = destination
        with open(path, "w", encoding="utf-8") as f:
            f.write(destination)

    def _start_move(self, info_hash: str, handle):
        """Move a finished torrent to its destination. If any of its files'
        names are taken there, first rename it "Name (2)" (or (3), ...).
        Files merge into an existing folder of the same name otherwise."""
        self._moving.add(info_hash)
        self._move_attempts[info_hash] = self._move_attempts.get(info_hash, 0) + 1
        destination = self._destinations[info_hash]
        files = _real_files(handle)
        claimed = set().union(*self._claimed.values())
        # Files this same torrent put there before (some deleted since, so it
        # was downloaded again): not a clash, they're replaced by the new copy.
        done = self._completed.get(_hash_key(handle))
        own = {_key(os.path.join(done["path"], rel)) for rel, _ in done["files"]} if done else set()

        def taken(rel):
            path = os.path.join(destination, rel)
            if _key(path) in claimed:
                return True
            return os.path.exists(path) and _key(path) not in own

        flags = lt.move_flags_t.fail_if_exist
        if any(taken(p) for _, p in files):
            renames = _free_names(files, lambda top: taken(top) or os.path.exists(
                os.path.join(handle.status().save_path, top)))
            log(f"RENAMING (name taken in {destination}): "
                f"{_top(files[0][1])} -> {_top(renames[0][1])}")
            files = renames
            self._renaming[info_hash] = len(renames)
            for index, new_path in renames:
                handle.rename_file(index, new_path)
        elif any(os.path.exists(os.path.join(destination, p)) for _, p in files):
            flags = lt.move_flags_t.always_replace_files   # only its own files (see taken)
        self._move_flags[info_hash] = flags
        self._claimed[info_hash] = {_key(os.path.join(destination, p)) for _, p in files}
        # Includes the top-level name, so "Name (2)" is reserved as a whole.
        self._claimed[info_hash].add(_key(os.path.join(destination, _top(files[0][1]))))
        if info_hash not in self._renaming:
            self._move_now(info_hash, handle)
        # else: moved once the renames are done (file_renamed_alert)

    # --- finished torrents, remembered after they leave the list ---
    def _record_completed(self, handle, name: str, save_path: str):
        try:
            fs = handle.torrent_file().files()
            wanted = handle.get_file_priorities()
            files = [[p, fs.file_size(i)] for i, p in _real_files(handle)
                     if i >= len(wanted) or wanted[i] > 0]
        except Exception as e:
            log(f"couldn't record finished torrent {name}: {e}")
            return
        self._completed[_hash_key(handle)] = {
            "name": name, "path": save_path, "files": files,
            "finished": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        tmp = self._completed_path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._completed, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._completed_path)
        except OSError as e:
            log(f"couldn't save {COMPLETED_FILE}: {e}")

    def _already_downloaded(self, key: str) -> dict | None:
        """The record for a torrent finished before, if every one of its files
        is still where it went, at its full size."""
        done = self._completed.get(key)
        if not done or not done["files"]:
            return None
        for rel, size in done["files"]:
            try:
                if os.path.getsize(os.path.join(done["path"], rel)) != size:
                    return None
            except OSError:
                return None
        return done

    def _rmdir_when_empty(self, path: str, tries: int = DELETE_MAX_ATTEMPTS):
        """Remove a torrent's own incomplete subfolder (_incomplete\\<id>)
        once nothing but empty folders is left in it. Renaming to "Name (2)"
        leaves the old, now empty "Name" folder behind in there. rmdir never
        removes a folder that still has anything in it, so files are safe.

        Only for the torrent's own subfolder: torrents added by older builds
        used _incomplete itself, where other torrents' folders live."""
        for folder, _, _ in os.walk(path, topdown=False):
            if folder != path:
                try:
                    os.rmdir(folder)
                except OSError:
                    pass
        try:
            os.rmdir(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            if tries > 1:
                QTimer.singleShot(DELETE_RETRY_MS, lambda: self._rmdir_when_empty(path, tries - 1))
            else:
                left = sum(len(fs) for _, _, fs in os.walk(path))
                log(f"CLEANUP couldn't remove {path} ({left} file(s) left in it): {e}")
        else:
            log(f"CLEANUP removed empty {path}")
            _remove_incomplete_if_empty(os.path.dirname(path))

    def _move_now(self, info_hash: str, handle):
        destination = self._destinations[info_hash]
        log(f"MOVING (complete): {_tag(handle)} -> {destination}")
        handle.move_storage(destination, self._move_flags.pop(info_hash, lt.move_flags_t.fail_if_exist))

    def _move_failed_or_retry(self, info_hash: str, name: str, why: str):
        """Usually a race (another torrent took the name a moment ago), so the
        next poll checks names again; gives up after a few tries."""
        self._moving.discard(info_hash)
        self._renaming.pop(info_hash, None)
        self._claimed.pop(info_hash, None)
        if self._move_attempts.get(info_hash, 0) >= MOVE_MAX_ATTEMPTS:
            self._move_failed.add(info_hash)
            log(f"MOVE FAILED, left in incomplete folder: {name}: {why}")
        else:
            log(f"MOVE RETRY: {name}: {why}")

    def _run_pending_deletes(self):
        others = list(self._handles.values())
        remaining = []
        for job in self._pending_deletes:
            if safe_delete.run(job, others, log):
                continue
            if job["attempts"] >= DELETE_MAX_ATTEMPTS:
                safe_delete.give_up(job, log)
                continue
            remaining.append(job)
        self._pending_deletes = remaining
        if remaining:
            self._delete_timer.start()

    def pause_torrent(self, info_hash: str):
        handle = self._handles.get(info_hash)
        if handle:
            log(f"PAUSE (by you): {_tag(handle)}")
            # Leave auto-management first, otherwise the queue can resume it.
            handle.unset_flags(lt.torrent_flags.auto_managed)
            handle.pause()
            handle.save_resume_data(_RESUME_FLAGS)

    def resume_torrent(self, info_hash: str):
        handle = self._handles.get(info_hash)
        if handle:
            log(f"RESUME (by you): {_tag(handle)}")
            handle.set_flags(lt.torrent_flags.auto_managed)
            handle.resume()
            self._enforce_queue_limit()
            handle.save_resume_data(_RESUME_FLAGS)

    def apply_settings(self):
        log(f"SETTINGS applied: {self._settings_summary()}")
        self._configure_session()
        self._enforce_queue_limit()

    def _settings_summary(self) -> str:
        s = self._settings
        speed = lambda v: f"{v} KB/s" if v > 0 else "unlimited"
        incomplete = (s.incomplete_dir or "_incomplete inside it") if s.use_incomplete_dir \
            else "off (straight into the download folder)"
        return (f"download folder {s.download_dir}, incomplete folder "
                f"{incomplete}, max active {s.max_concurrent_downloads}, "
                f"down {speed(s.max_download_speed)}, up {speed(s.max_upload_speed)}, "
                f"port {s.listen_port_start}, remove when complete {'on' if s.remove_on_complete else 'off'}, "
                f"watch {'on ' + s.watch_folder if s.watch_folder_enabled else 'off'}")

    def _log_progress(self):
        """Every few minutes, one line per unfinished torrent, so the log
        shows how a download went (or why it didn't)."""
        lines = []
        for handle in list(self._handles.values()):
            if not handle.is_valid():
                continue
            st = handle.status()
            if st.is_finished:
                continue
            eta = ""
            if st.download_rate > 0 and st.total_wanted > 0:
                eta = f", about {_duration((st.total_wanted - st.total_wanted_done) / st.download_rate)} left"
            lines.append(
                f"  {_tag(handle)}: {self._state_name(handle, st)} {st.progress * 100:.1f}% "
                f"of {_mb(st.total_wanted)}, {st.download_rate / 1024:.0f} KB/s, "
                f"{st.num_seeds} seeds / {st.num_peers} peers connected "
                f"({st.list_seeds} seeds / {st.list_peers} peers known){eta}")
        if lines:
            log(f"PROGRESS ({len(lines)} unfinished):\n" + "\n".join(lines))

    def _check_stalled(self, handle, info_hash: str, st):
        """Log once when a downloading torrent hasn't gained a byte for
        STALL_MINUTES, and again when it starts moving."""
        now = time.monotonic()
        done = st.total_wanted_done
        last = self._last_progress.get(info_hash)
        active = st.state == lt.torrent_status.states.downloading and not st.paused
        if last is None or done != last[0] or not active:
            if info_hash in self._stalled and done != (last or (None,))[0]:
                self._stalled.discard(info_hash)
                log(f"UNSTALLED (downloading again): {_tag(handle)} {st.progress * 100:.1f}%")
            self._last_progress[info_hash] = (done, now)
            return
        if info_hash not in self._stalled and now - last[1] >= STALL_MINUTES * 60:
            self._stalled.add(info_hash)
            log(f"STALLED (nothing received for {STALL_MINUTES} min): {_tag(handle)} "
                f"{st.progress * 100:.1f}%, {st.num_seeds} seeds / {st.num_peers} peers connected, "
                f"{st.list_seeds} seeds / {st.list_peers} peers known")

    @staticmethod
    def _state_name(handle, st) -> str:
        if not handle.flags() & lt.torrent_flags.auto_managed and handle.flags() & lt.torrent_flags.paused:
            return "paused"
        if st.paused and st.state == lt.torrent_status.states.downloading:
            return "queued"
        return _STATE_MAP.get(st.state, "unknown")

    def _enforce_queue_limit(self):
        max_active = self._settings.max_concurrent_downloads
        self._session.apply_settings({
            "active_downloads": max_active,
            "active_seeds": max_active + 2,
            "active_limit": max_active + 5,
        })

    def _poll(self):
        self._process_alerts()
        items = []
        to_remove = []
        for info_hash, handle in list(self._handles.items()):
            if not handle.is_valid():
                continue
            status = handle.status()
            name = status.name or "Fetching metadata..."
            state_str = _STATE_MAP.get(status.state, "unknown")

            if handle.flags() & lt.torrent_flags.paused and not (
                handle.flags() & lt.torrent_flags.auto_managed
            ):
                state_str = "paused"

            # Check if queued by auto_managed
            if status.paused and (handle.flags() & lt.torrent_flags.auto_managed):
                if status.state == lt.torrent_status.states.downloading:
                    state_str = "queued"

            if info_hash in self._moving:
                state_str = "moving"

            self._check_stalled(handle, info_hash, status)

            total_size = status.total_wanted
            downloaded = status.total_wanted_done
            dl_rate = status.download_rate
            eta = -1
            if dl_rate > 0 and total_size > 0:
                remaining = total_size - downloaded
                eta = int(remaining / dl_rate)

            items.append(TorrentItem(
                info_hash=info_hash,
                name=name,
                progress=status.progress,
                download_rate=dl_rate,
                upload_rate=status.upload_rate,
                num_seeds=status.num_seeds,
                num_peers=status.num_peers,
                total_size=total_size,
                downloaded=downloaded,
                state=state_str,
                eta_seconds=eta,
                save_path=status.save_path,
            ))

            complete = (
                status.state in (lt.torrent_status.states.seeding, lt.torrent_status.states.finished)
                and status.has_metadata
                and status.total_wanted > 0
                and status.total_wanted_done == status.total_wanted
            )

            # Finished in the incomplete folder: move it to its destination
            # first. "Finished" is announced (and the torrent removed, if set)
            # once it's there.
            if complete and info_hash in self._destinations and info_hash not in self._move_failed:
                if info_hash not in self._moving:
                    self._start_move(info_hash, handle)
                continue

            # Notify on completion (once per torrent)
            if complete and info_hash not in self._finished_set:
                self._finished_set.add(info_hash)
                self._record_completed(handle, name, status.save_path)
                took = self._added_at.get(info_hash)
                took = f" after {_duration(time.monotonic() - took)}" if took else ""
                log(f"FINISHED: {_tag(handle)}, {_mb(status.total_wanted)}{took}, "
                    f"files in {status.save_path}")
                self.torrent_finished.emit(name)

            # "Remove when complete": skip seeding. Also applies to torrents
            # already seeding when the option is turned on.
            if complete and getattr(self._settings, "remove_on_complete", False):
                to_remove.append((info_hash, name))

        for info_hash, name in to_remove:
            self.remove_torrent(info_hash, delete_files=False, why="complete")
        if to_remove:
            removed = {ih for ih, _ in to_remove}
            items = [i for i in items if i.info_hash not in removed]

        self.stats_updated.emit(items)

    def _process_alerts(self) -> int:
        """Handle pending alerts. Returns how many resume-data requests
        completed (written or failed), so shutdown can count them down."""
        completed = 0
        for alert in self._session.pop_alerts():
            if isinstance(alert, lt.save_resume_data_alert):
                completed += 1
                info_hash = str(alert.handle.info_hash())
                # A save can land after the torrent was removed; don't resurrect it.
                if info_hash not in self._handles:
                    continue
                data = lt.write_resume_data_buf(alert.params)
                path = os.path.join(self._resume_dir, f"{info_hash}.fastresume")
                # Write-then-rename so a crash mid-write can't corrupt the file.
                tmp_path = path + ".tmp"
                with open(tmp_path, "wb") as f:
                    f.write(data)
                os.replace(tmp_path, path)
            elif isinstance(alert, lt.save_resume_data_failed_alert):
                completed += 1
                log(f"ERROR saving resume data: {_tag(alert.handle)}: {alert.message()}")
            elif isinstance(alert, lt.metadata_received_alert):
                # Magnet now has its info dict; persist it.
                alert.handle.save_resume_data(_RESUME_FLAGS)
                ti = alert.handle.torrent_file()
                log(f"METADATA received: {_tag(alert.handle)}, "
                    f"{_mb(ti.total_size()) if ti else '?'}, {ti.num_files() if ti else '?'} file(s)")
            elif isinstance(alert, lt.metadata_failed_alert):
                log(f"ERROR metadata: {_tag(alert.handle)}: {alert.message()}")
            elif isinstance(alert, lt.state_changed_alert):
                if _STATE_MAP.get(alert.prev_state) == _STATE_MAP.get(alert.state):
                    continue   # e.g. finished -> seeding: both "seeding"
                log(f"STATE {_STATE_MAP.get(alert.prev_state, alert.prev_state)} -> "
                    f"{_STATE_MAP.get(alert.state, alert.state)}: {_tag(alert.handle)}")
            elif isinstance(alert, lt.file_error_alert):
                log(f"ERROR file: {_tag(alert.handle)}: {alert.filename()}: {alert.message()}")
            elif isinstance(alert, lt.torrent_error_alert):
                log(f"ERROR torrent: {_tag(alert.handle)}: {alert.message()}")
            elif isinstance(alert, lt.tracker_error_alert):
                # Same tracker, same error: logged once, not every announce.
                key = (str(alert.handle.info_hash()), alert.tracker_url(), alert.error_message())
                if key not in self._tracker_errors_logged:
                    self._tracker_errors_logged.add(key)
                    log(f"TRACKER error (logged once): {_tag(alert.handle)}: {alert.message()}")
            elif isinstance(alert, lt.listen_failed_alert):
                log(f"ERROR can't listen for peers: {alert.message()}")
            elif isinstance(alert, lt.file_renamed_alert):
                info_hash = str(alert.handle.info_hash())
                if info_hash in self._renaming:
                    self._renaming[info_hash] -= 1
                    if self._renaming[info_hash] <= 0:
                        del self._renaming[info_hash]
                        self._move_now(info_hash, alert.handle)
            elif isinstance(alert, lt.file_rename_failed_alert):
                info_hash = str(alert.handle.info_hash())
                if info_hash in self._renaming:
                    self._move_failed_or_retry(
                        info_hash, alert.handle.status().name, alert.message())
            elif isinstance(alert, lt.storage_moved_alert):
                info_hash = str(alert.handle.info_hash())
                if info_hash in self._moving:
                    self._moving.discard(info_hash)
                    self._move_attempts.pop(info_hash, None)
                    self._claimed.pop(info_hash, None)
                    self._set_destination(info_hash, None)
                    log(f"MOVED: {_tag(alert.handle)} from {alert.old_path()} -> {alert.storage_path()}")
                    if _is_own_subfolder(alert.old_path(), alert.handle):
                        self._rmdir_when_empty(alert.old_path())
                    alert.handle.save_resume_data(_RESUME_FLAGS)
            elif isinstance(alert, lt.storage_moved_failed_alert):
                info_hash = str(alert.handle.info_hash())
                if info_hash in self._moving:
                    self._move_failed_or_retry(
                        info_hash, alert.handle.status().name, alert.message())
        return completed

    def _save_all_resume_data(self, force: bool = False) -> int:
        """Request resume data; returns the number of requests issued."""
        requested = 0
        for handle in self._handles.values():
            if handle.is_valid() and (force or handle.need_save_resume_data()):
                handle.save_resume_data(_RESUME_FLAGS)
                requested += 1
        return requested

    def _restore_resume_data(self):
        if not os.path.isdir(self._resume_dir):
            return
        for filename in os.listdir(self._resume_dir):
            if not filename.endswith(".fastresume"):
                continue
            filepath = os.path.join(self._resume_dir, filename)
            try:
                with open(filepath, "rb") as f:
                    data = f.read()
                # Flags (paused / auto_managed) come from the resume data,
                # so torrents the user paused stay paused.
                params = lt.read_resume_data(data)
                handle = self._session.add_torrent(params)
                info_hash = str(handle.info_hash())
                self._handles[info_hash] = handle
            except Exception as e:
                log(f"ERROR restoring {filename} (torrent not loaded): {e}")
                continue
            try:
                with open(self._dest_path(info_hash), encoding="utf-8") as f:
                    self._destinations[info_hash] = f.read().strip()
            except OSError:
                pass
            st = handle.status()
            log(f"RESTORED: {_tag(handle)} {self._state_name(handle, st)} "
                f"{st.progress * 100:.1f}% in {st.save_path}")
        log(f"RESTORED {len(self._handles)} torrent(s)")
        self._enforce_queue_limit()
