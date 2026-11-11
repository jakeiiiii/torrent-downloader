# -*- mode: python ; coding: utf-8 -*-
import os
import sys

# Locate libtorrent's package folder wherever pip put it (system Python or a
# venv), without importing it: the import itself needs the OpenSSL DLLs.
import importlib.util
lt_dir = list(importlib.util.find_spec("libtorrent").submodule_search_locations)[0]

# Collect libtorrent .pyd and its OpenSSL DLL dependencies
lt_binaries = []
for f in os.listdir(lt_dir):
    if f.endswith(".pyd") or f.endswith(".dll"):
        lt_binaries.append((os.path.join(lt_dir, f), "libtorrent"))

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=lt_binaries,
    datas=[("resources/icons/app.ico", "resources/icons")],  # tray + window icon
    hiddenimports=["libtorrent"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

# Folder build (dist/TorrentDownloader/), not one-file: a one-file exe unpacks
# ~40 MB to a temp folder on every launch (~1.3 s), and some launcher
# tools delete each .torrent ~1 s after launching us. A folder build starts fast
# enough to read the file well before that.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TorrentDownloader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed mode, no console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="resources/icons/app.ico",  # exe icon in Explorer; made by tools/make_icon.py
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TorrentDownloader",
)
