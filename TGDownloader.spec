# -*- mode: python ; coding: utf-8 -*-
# TGDownloader.spec
# -----------------
# PyInstaller build spec for TGDownloader v6.
#
# One-directory bundle layout:
#   dist/TGDownloader/
#     TGDownloader.exe          ← launcher (no console window)
#     _internal/
#       gui.html                ← served to browser at runtime
#       TGDownloader.py         ← imported by --backend subprocess mode
#       TGDownloader_GUI.py     ← imported by the normal GUI startup
#       ... (all other bundled libs)
#
# Usage:
#   pyinstaller TGDownloader.spec
#   (or just run build.bat)

from pathlib import Path
import sys

HERE = Path(SPECPATH)   # directory containing this .spec file

# ── Analysis ──────────────────────────────────────────────────────────────────
a = Analysis(
    [str(HERE / 'TGDownloader_bundled.py')],
    pathex=[str(HERE)],
    binaries=[],
    datas=[
        # gui.html must be accessible at runtime via BUNDLE_DIR / 'gui.html'
        (str(HERE / 'gui.html'), '.'),
        (str(HERE / 'setup_wizard.html'), '.'),
    ],
    hiddenimports=[
        # ── Telethon ──────────────────────────────────────────
        'telethon',
        'telethon.sync',
        'telethon.sessions',
        'telethon.sessions.string',
        'telethon.tl',
        'telethon.tl.types',
        'telethon.tl.functions',
        'telethon.tl.functions.messages',
        'telethon.tl.functions.channels',
        'telethon.events',
        'telethon.events.newmessage',
        'telethon.events.messageedited',
        'telethon.crypto',
        'telethon.network',
        'telethon.network.connection',
        'telethon.network.connection.tcpfull',
        # ── Mutagen ───────────────────────────────────────────
        'mutagen',
        'mutagen._util',
        'mutagen._tags',
        'mutagen.id3',
        'mutagen.id3._tags',
        'mutagen.id3._frames',
        'mutagen.mp3',
        'mutagen.flac',
        'mutagen.mp4',
        'mutagen.ogg',
        'mutagen.oggvorbis',
        'mutagen.oggopus',
        'mutagen.asf',
        'mutagen.aiff',
        'mutagen.wave',
        'mutagen.apev2',
        'mutagen.monkeysaudio',
        'mutagen.musepack',
        # ── Optional fast crypto ──────────────────────────────
        'cryptg',
        # ── Tkinter (folder picker fallback) ──────────────────
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        # ── Our own modules ───────────────────────────────────
        'TGDownloader',
        'TGDownloader_GUI',
        # ── Stdlib that PyInstaller sometimes misses ──────────
        'asyncio',
        'asyncio.events',
        'asyncio.tasks',
        'asyncio.queues',
        'email.mime',
        'email.mime.text',
        'logging.handlers',
        'http.server',
        'socketserver',
        'urllib.request',
        'urllib.parse',
        'webbrowser',
        'struct',
        'hashlib',
        'base64',
        'json',
        'shutil',
        'dataclasses',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Things we definitely don't need — trim bundle size
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'PIL',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'wx',
        'gi',
        'IPython',
        'jupyter',
        'notebook',
        'sphinx',
        'pytest',
        'unittest',
    ],
    noarchive=False,
    optimize=1,
)

# ── PYZ archive ───────────────────────────────────────────────────────────────
pyz = PYZ(a.pure)

# ── Executable ────────────────────────────────────────────────────────────────
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,     # one-dir mode: binaries go into COLLECT
    name='TGDownloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,                  # compress with UPX if available (smaller bundle)
    console=False,             # ← windowed: no black console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # Optional: set a custom icon
    # icon=str(HERE / 'icon.ico'),
)

# ── Collect everything into dist/TGDownloader/ ────────────────────────────────
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TGDownloader',
)
