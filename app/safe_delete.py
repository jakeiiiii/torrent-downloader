"""Delete a removed torrent's files without touching anything another torrent uses.

libtorrent's own delete_files removes every file in the torrent, even one that
another torrent in the list maps to the same path, and removes any folder that
happens to be empty, even if another torrent (paused, not started yet) will
download into it. So we remove the torrent normally and delete here instead:

- a file is deleted only if no other torrent uses that same path;
- a folder is deleted only if it's empty and no other torrent has files under it;
- the torrent's save folder (e.g. D:\\Downloads) and anything above it is never deleted;
- files no torrent owns (your own files) are never deleted, so their folder stays.
"""
import os

import libtorrent as lt


def _key(path: str) -> str:
    """Windows paths are case-insensitive."""
    return os.path.normcase(os.path.normpath(path))


def _torrent_paths(handle) -> tuple[str, list[str]]:
    """(save_path, absolute file paths) for a torrent; files empty if no metadata."""
    status = handle.status()
    save_path = os.path.normpath(status.save_path)
    ti = handle.torrent_file()
    files = []
    if ti is not None:
        fs = ti.files()
        for i in range(fs.num_files()):
            if fs.file_flags(i) & lt.file_storage.flag_pad_file:
                continue  # padding files are never written to disk
            files.append(os.path.normpath(os.path.join(save_path, fs.file_path(i))))
    return save_path, files


def _folders_between(path: str, save_path: str) -> list[str]:
    """Folders containing `path`, from its parent up to (not including) save_path."""
    out = []
    stop = _key(save_path)
    parent = os.path.dirname(path)
    while _key(parent) != stop and _key(parent).startswith(stop + os.sep):
        out.append(parent)
        parent = os.path.dirname(parent)
    return out


def paths_in_use(handles) -> tuple[set[str], set[str]]:
    """(files, folders) used by the given torrents, as normalized keys."""
    files, folders = set(), set()
    for h in handles:
        try:
            if not h.is_valid():
                continue
            save_path, paths = _torrent_paths(h)
            folders.add(_key(save_path))
            if not paths:
                # Magnet still fetching metadata: its file list is unknown, but
                # it will most likely use a folder named after the torrent.
                name = h.status().name
                if name:
                    folders.add(_key(os.path.join(save_path, name)))
            for p in paths:
                files.add(_key(p))
                for folder in _folders_between(p, save_path):
                    folders.add(_key(folder))
        except RuntimeError:
            continue  # handle went invalid while we looked
    return files, folders


def plan(handle, info_hash: str) -> dict:
    """Collect what to delete for `handle`. Call before removing it."""
    save_path, files = _torrent_paths(handle)
    folders = set()
    for p in files:
        folders.update(_folders_between(p, save_path))
    # libtorrent's part file (holds pieces of files you chose not to download)
    hashes = {info_hash}
    try:
        hashes.add(str(handle.info_hashes().get_best()))
    except Exception:
        pass
    for h in hashes:
        files.append(os.path.join(save_path, f".{h}.parts"))
    return {
        "name": handle.status().name,
        "files": files,
        # deepest first so children go before their parents
        "folders": sorted(folders, key=lambda f: f.count(os.sep), reverse=True),
        "attempts": 0,
    }


def run(job: dict, other_handles, log) -> bool:
    """Try to delete what's in `job`. Returns True when finished; False if some
    files are still locked (libtorrent can hold them briefly after removal) and
    it should be retried."""
    in_use_files, in_use_folders = paths_in_use(other_handles)
    job["attempts"] += 1

    still_locked = []
    for path in job["files"]:
        if _key(path) in in_use_files:
            log(f"DELETE kept (used by another torrent): {path}")
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except PermissionError:
            still_locked.append(path)
        except OSError as e:
            log(f"DELETE failed: {path}: {e}")
    job["files"] = still_locked
    if still_locked:
        return False

    for folder in job["folders"]:
        if _key(folder) in in_use_folders:
            log(f"DELETE kept folder (used by another torrent): {folder}")
            continue
        try:
            os.rmdir(folder)  # only succeeds if empty, so your own files keep it
        except FileNotFoundError:
            pass
        except OSError:
            if os.path.isdir(folder) and os.listdir(folder):
                log(f"DELETE kept folder (has files not from this torrent): {folder}")
    log(f"DELETE done: {job['name']}")
    return True


def give_up(job: dict, log):
    for path in job["files"]:
        log(f"DELETE failed (file still in use): {path}")
