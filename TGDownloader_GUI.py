# SPDX-License-Identifier: MIT
# Copyright (c) 2025 TGDownloader contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
TGDownloader GUI Server  v6.1
------------------------------
Changes in v6.1:
  • Single-instance guard via LOCK_PORT (7843) — new instances open the
    browser to the existing GUI instead of stacking processes.
  • Quit uses os._exit(0) to force-kill the process; no lingering threads
    or sockets that would block the next launch.
  • /browse-folder POST endpoint — opens the native Windows folder picker
    via tkinter.filedialog.askdirectory in a worker thread.
  • /telegram-auth POST + /telegram-status GET — step-by-step Telegram
    login flow driven from the browser so the first-launch EOFError is gone.
    Uses a dedicated asyncio event loop and asyncio.Future objects to pipe
    phone / code / password back into Telethon's client.start() callbacks.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import struct
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
import zipfile

from base64 import b64encode
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, quote as url_quote, unquote_plus

import tgd_common


# ── App-window launcher ───────────────────────────────────────────────────────
# Opens the GUI in a chromium "app" window (no address bar, no tabs, no toolbar)
# so TGDownloader feels like a native desktop application.
#
# Priority order on Windows: Edge → Chrome → Chromium → fallback to default browser.
# On macOS/Linux: Chrome → Chromium → Edge → fallback.
#
# The --app flag is supported by every Chromium-based browser since ~2018.

def _open_app_window(url: str) -> bool:
    """Launch *url* in a chromium app window.  Returns True on success."""
    import shutil

    if sys.platform == "win32":
        # Ordered list of (display-name, list-of-candidate-paths)
        _WIN_CANDIDATES = [
            ("msedge", [
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            ]),
            ("chrome", [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            ]),
            ("chromium", [
                r"C:\Program Files\Chromium\Application\chrome.exe",
                r"C:\Program Files (x86)\Chromium\Application\chrome.exe",
            ]),
        ]
        # Also check PATH (handles non-standard install locations)
        _WIN_CMD_NAMES = ["msedge", "chrome", "chromium"]

        exe = None
        for _, paths in _WIN_CANDIDATES:
            for p in paths:
                if Path(p).exists():
                    exe = p
                    break
            if exe:
                break
        if not exe:
            for name in _WIN_CMD_NAMES:
                found = shutil.which(name)
                if found:
                    exe = found
                    break

    elif sys.platform == "darwin":
        _MAC_APPS = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        ]
        exe = None
        for p in _MAC_APPS:
            if Path(p).exists():
                exe = p
                break
        if not exe:
            for name in ("google-chrome", "chromium"):
                found = shutil.which(name)
                if found:
                    exe = found
                    break

    else:  # Linux / other
        _LIN_NAMES = [
            "google-chrome", "google-chrome-stable",
            "chromium-browser", "chromium",
            "microsoft-edge", "microsoft-edge-stable",
            "brave-browser",
        ]
        exe = None
        for name in _LIN_NAMES:
            found = shutil.which(name)
            if found:
                exe = found
                break

    if not exe:
        logger.info("No Chromium-based browser found — falling back to default browser")
        return False

    try:
        subprocess.Popen(
            [exe, f"--app={url}",
             "--disable-extensions",        # cleaner appearance
             "--no-first-run",              # skip "welcome" screens
             "--no-default-browser-check",  # suppress nag dialogs
             "--start-maximized",           # launch maximized
             ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("Launched app window via %s", exe)
        return True
    except Exception as exc:
        logger.warning("App-window launch failed (%s): %s", exe, exc)
        return False


# ── Directory layout ──────────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(os.environ.get("TGD_BUNDLE_DIR", sys._MEIPASS))
    DATA_DIR   = Path(os.environ.get("TGD_DATA_DIR",   Path(sys.executable).parent))
else:
    _here      = Path(__file__).parent
    BUNDLE_DIR = Path(os.environ.get("TGD_BUNDLE_DIR", _here))
    DATA_DIR   = Path(os.environ.get("TGD_DATA_DIR",   _here))

LOG_FILE           = Path(os.environ.get("TGD_LOG_FILE", DATA_DIR / "tgdownloader_debug.log"))
GUI_HTML           = BUNDLE_DIR / "gui.html"
SETUP_WIZARD_HTML  = BUNDLE_DIR / "setup_wizard.html"
SESSIONS_FILE      = DATA_DIR   / "tg_sessions.json"
_ALBUM_ID_CACHE_FILE = DATA_DIR / "album_id_cache.json"  # persists album search results
LIKED_SONGS_FILE   = DATA_DIR   / "liked_songs.json"     # persists "Liked Songs" library
_TRANSCODE_DIR     = DATA_DIR   / "tg_transcode_cache"   # cached MP3 transcodes for in-app playback
_LIB_STATS_CACHE_FILE = DATA_DIR / "library_stats_cache.json"  # persisted /library-stats payload

# ── Auto-update (notify-only) ─────────────────────────────────────────────────
# The GUI polls GitHub Releases for a newer tag and shows a banner. It never
# downloads or replaces files — the user updates manually from the release page.
APP_VERSION  = tgd_common.__version__         # single-sourced in tgd_common.py
GITHUB_REPO  = "Thurlws/TGDownloader"         # owner/repo the update check targets

# Backend process command
if getattr(sys, "frozen", False):
    _BACKEND_CMD: list[str] = [sys.executable, "--backend"]
else:
    _BACKEND_PY  = BUNDLE_DIR / "TGDownloader.py"
    _BACKEND_CMD = [sys.executable, "-u", str(_BACKEND_PY)]

HTTP_PORT = int(os.environ.get("TGD_HTTP_PORT", "7842"))
LOCK_PORT = HTTP_PORT + 1   # single-instance sentinel — we bind this; nobody else does

logger = logging.getLogger("gui_server")


# ── Request-origin guard ──────────────────────────────────────────────────────
# The server only binds 127.0.0.1, but that alone does not stop the browser
# from being used as a proxy: any web page can fire cross-origin requests at
# http://127.0.0.1:7842 (CSRF — responses are unreadable, but state-changing
# endpoints like /delete-file still execute), WebSockets are exempt from the
# same-origin policy entirely, and DNS rebinding defeats IP-based trust while
# keeping the Host header attacker-controlled.  Rejecting foreign Host/Origin
# values closes all three.  Same-origin requests from our own GUI carry either
# no Origin header (plain GETs) or one of the allowed values.

_ALLOWED_HOSTS = {
    f"127.0.0.1:{HTTP_PORT}", f"localhost:{HTTP_PORT}", f"[::1]:{HTTP_PORT}",
    "127.0.0.1", "localhost", "[::1]",
}
_ALLOWED_ORIGINS = {
    f"http://127.0.0.1:{HTTP_PORT}", f"http://localhost:{HTTP_PORT}",
    f"http://[::1]:{HTTP_PORT}",
}


def _is_local_request(host: "str | None", origin: "str | None") -> bool:
    """Pure predicate: is this Host/Origin pair from our own local GUI?"""
    host = (host or "").strip().lower()
    if host and host not in _ALLOWED_HOSTS:
        return False
    origin = (origin or "").strip().lower()
    if origin and origin not in _ALLOWED_ORIGINS:
        return False
    return True


# ── Update check ──────────────────────────────────────────────────────────────

_UPDATE_CACHE: dict = {}            # {"ts": float, "data": dict} — short TTL cache
_UPDATE_TTL          = 1800         # seconds (30 min) between live GitHub queries


def _parse_version(tag: str) -> tuple:
    """Turn a version/tag string like 'v1.2.3' into a comparable tuple (1,2,3).

    Non-numeric junk is ignored; missing parts default to 0 so '1.2' < '1.2.1'.
    Returns () when no numbers are found (treated as the oldest possible)."""
    import re as _re
    nums = _re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums) if nums else ()


def _check_for_update(force: bool = False) -> dict:
    """Query GitHub Releases for the latest version and compare to APP_VERSION.

    Notify-only: returns metadata, never downloads anything. Results are cached
    for _UPDATE_TTL seconds so we stay well under GitHub's unauthenticated rate
    limit (60 req/hour). Pass force=True to bypass the cache (manual re-check).

    Shape: {current, latest, update_available, url, name, notes, published_at}
    or {current, error} on failure / when no releases exist yet."""
    now = time.time()
    if (not force and _UPDATE_CACHE.get("data")
            and now - _UPDATE_CACHE.get("ts", 0) < _UPDATE_TTL):
        return _UPDATE_CACHE["data"]

    api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        req = urllib.request.Request(api, headers={
            "User-Agent": f"TGDownloader/{APP_VERSION}",
            "Accept":     "application/vnd.github+json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            rel = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 404 = repo has no published releases yet — not an error worth alarming.
        msg = "No releases published yet" if exc.code == 404 else f"GitHub HTTP {exc.code}"
        data = {"current": APP_VERSION, "update_available": False, "error": msg}
        _UPDATE_CACHE.update(ts=now, data=data)
        return data
    except Exception as exc:
        logger.debug("Update check failed: %s", exc)
        # Don't cache transient network failures for the full TTL.
        return {"current": APP_VERSION, "update_available": False,
                "error": f"Update check failed: {exc}"}

    latest_tag = (rel.get("tag_name") or rel.get("name") or "").strip()
    available  = _parse_version(latest_tag) > _parse_version(APP_VERSION)
    # The built Windows zip: release.yml attaches exactly one .zip per release.
    # Grab its size too so the apply step can sanity-check the download (v1.15.0).
    zip_asset = next(
        (a for a in (rel.get("assets") or [])
         if str(a.get("name", "")).lower().endswith(".zip")), {})
    data = {
        "current":          APP_VERSION,
        "latest":           latest_tag.lstrip("vV") or latest_tag,
        "update_available": available,
        "url":              rel.get("html_url", f"https://github.com/{GITHUB_REPO}/releases"),
        "download_url":     zip_asset.get("browser_download_url", ""),
        "download_size":    int(zip_asset.get("size") or 0),
        "can_apply":        _update_supported(),
        "name":             rel.get("name") or latest_tag,
        "notes":            (rel.get("body") or "")[:4000],
        "published_at":     rel.get("published_at", ""),
    }
    _UPDATE_CACHE.update(ts=now, data=data)
    return data


# ══════════════════════════════════════════════
#  SELF-UPDATE APPLY  (frozen Windows builds only)
# ══════════════════════════════════════════════
#
# The app ships as a one-dir PyInstaller bundle: TGDownloader.exe beside an
# _internal/ folder, with user data (config, session, caches) in that same
# folder as the exe. Windows won't overwrite a running exe or its loaded DLLs,
# so applying an update is: download the release zip, stage it, then hand off to
# a small .bat that waits for us to exit, swaps _internal/ + the exe (keeping
# .old backups for rollback), and relaunches. User data is never touched — it
# sits beside the exe, not inside _internal/.

_UPDATE_STAGING   = DATA_DIR / "_update"        # extracted new bundle lives here
_UPDATE_MAX_BYTES = 500 * 1024 * 1024           # sanity ceiling on the download


def _update_supported() -> bool:
    return bool(getattr(sys, "frozen", False)) and os.name == "nt"


def _app_dir() -> Path:
    """Folder holding TGDownloader.exe (frozen) — same as DATA_DIR there."""
    return Path(sys.executable).parent


def _cleanup_update_leftovers() -> None:
    """We only reach this by running again after a successful swap, so the .old
    backups and staging folder from the last apply are safe to delete."""
    if not _update_supported():
        return
    app = _app_dir()
    try:
        shutil.rmtree(app / "_internal.old", ignore_errors=True)
        shutil.rmtree(_UPDATE_STAGING,       ignore_errors=True)
        (app / "TGDownloader.old.exe").unlink(missing_ok=True)
    except Exception:
        pass


def _download_zip(url: str, dest: Path, expected_size: int = 0) -> None:
    """Stream a release zip to `dest`, size-capped and validated as a real zip."""
    req = urllib.request.Request(url, headers={"User-Agent": f"TGDownloader/{APP_VERSION}"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(262144)
            if not chunk:
                break
            written += len(chunk)
            if written > _UPDATE_MAX_BYTES:
                raise ValueError("update download exceeded the size limit")
            fh.write(chunk)
    if written == 0:
        raise ValueError("update download was empty")
    if expected_size and abs(written - expected_size) > 4096:
        raise ValueError(f"update size mismatch: got {written}, expected {expected_size}")
    if not zipfile.is_zipfile(dest):
        raise ValueError("downloaded update is not a valid zip")


def _stage_update(zip_path: Path, staging: Path) -> Path:
    """Extract the release zip into `staging` (guarding against zip-slip) and
    return the folder holding the new TGDownloader.exe + _internal/."""
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    root = str(staging.resolve())
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            target = str((staging / name).resolve())
            if os.path.commonpath([root, target]) != root:
                raise ValueError(f"unsafe path in update zip: {name}")
        zf.extractall(staging)
    exe = next(iter(staging.rglob("TGDownloader.exe")), None)
    if exe is None:
        raise ValueError("update zip has no TGDownloader.exe")
    if not (exe.parent / "_internal").is_dir():
        raise ValueError("update zip has no _internal/ next to the exe")
    return exe.parent


def _build_update_script(pid: int, app_dir: Path, new_root: Path) -> str:
    """Windows .bat: wait for our PID to exit, back up + swap _internal/ and the
    exe from new_root into app_dir, relaunch, then delete staging and itself.
    On any move failure it rolls the .old backups back and relaunches."""
    return f"""@echo off
setlocal
set "PID={pid}"
set "APP={app_dir}"
set "NEW={new_root}"

rem 1. Wait (up to ~60s) for TGDownloader to exit so its files unlock.
set /a n=0
:wait
tasklist /FI "PID eq %PID%" 2>nul | find "%PID%" >nul
if not errorlevel 1 (
  set /a n+=1
  if %n% GEQ 60 goto giveup
  ping -n 2 127.0.0.1 >nul
  goto wait
)
ping -n 3 127.0.0.1 >nul

rem 2. Swap _internal/ (keep the old copy as a rollback backup).
if exist "%APP%\\_internal.old" rmdir /S /Q "%APP%\\_internal.old"
if exist "%APP%\\_internal" ren "%APP%\\_internal" "_internal.old"
move "%NEW%\\_internal" "%APP%\\_internal" >nul || goto rollback

rem 3. Swap the exe (keep the old one too).
if exist "%APP%\\TGDownloader.old.exe" del /Q "%APP%\\TGDownloader.old.exe"
if exist "%APP%\\TGDownloader.exe" ren "%APP%\\TGDownloader.exe" "TGDownloader.old.exe"
move "%NEW%\\TGDownloader.exe" "%APP%\\TGDownloader.exe" >nul || goto rollback

rem 4. Relaunch and clean up staging.
start "" "%APP%\\TGDownloader.exe"
rmdir /S /Q "%NEW%" 2>nul
goto done

:rollback
if exist "%APP%\\_internal.old" if not exist "%APP%\\_internal" ren "%APP%\\_internal.old" "_internal"
if exist "%APP%\\TGDownloader.old.exe" if not exist "%APP%\\TGDownloader.exe" ren "%APP%\\TGDownloader.old.exe" "TGDownloader.exe"
start "" "%APP%\\TGDownloader.exe"

:done
:giveup
(goto) 2>nul & del "%~f0"
"""


def _apply_update() -> dict:
    """Download + stage the latest release and launch the swap helper. On success
    the app schedules its own exit so the helper can replace the locked files."""
    if not _update_supported():
        return {"ok": False, "error": "In-place update is only available in the "
                "packaged Windows app. Use Download and replace the folder manually."}
    if MANAGER.is_running():
        return {"ok": False, "error": "A download is in progress — stop it first."}
    info = _check_for_update(force=True)
    if not info.get("update_available"):
        return {"ok": False, "error": "Already up to date."}
    if not info.get("download_url"):
        return {"ok": False, "error": "This release has no downloadable build attached."}
    try:
        zip_path = _UPDATE_STAGING.parent / "_update.zip"
        _download_zip(info["download_url"], zip_path, info.get("download_size", 0))
        new_root = _stage_update(zip_path, _UPDATE_STAGING)
        zip_path.unlink(missing_ok=True)
        bat = _app_dir() / "_apply_update.bat"
        bat.write_text(_build_update_script(os.getpid(), _app_dir(), new_root),
                       encoding="utf-8")
    except Exception as exc:
        logger.exception("Update staging failed")
        return {"ok": False, "error": f"Update failed: {exc}"}
    try:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP so it outlives our exit.
        subprocess.Popen(["cmd", "/c", str(bat)],
                         creationflags=0x00000008 | 0x00000200, close_fds=True)
    except Exception as exc:
        logger.exception("Failed to launch update helper")
        return {"ok": False, "error": f"Could not start the updater: {exc}"}
    logger.info("Update helper launched; exiting so it can swap the files")
    threading.Timer(1.5, lambda: os._exit(0)).start()
    return {"ok": True, "restarting": True, "latest": info.get("latest", "")}


def _load_api_credentials() -> "tuple[int | None, str | None]":
    """(api_id, api_hash) or (None, None) if not yet configured.

    Called at startup AND before each Telegram operation so credentials set
    via the wizard take effect without restarting the server.  Delegates to
    tgd_common so the keyring overlay (use_keyring) is honoured.
    """
    return tgd_common.get_api_credentials()


def _credentials_configured() -> bool:
    """True when valid-looking API credentials AND bot_username exist in config."""
    api_id, api_hash = _load_api_credentials()
    if not (api_id and api_hash and len(str(api_hash)) == 32):
        return False
    return bool(tgd_common.load_config().get("bot_username", "").strip())


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM AUTH
#  Runs entirely inside a private asyncio event loop (_tg_loop) on a daemon
#  thread.  HTTP handler threads communicate with it via asyncio.Future objects
#  using loop.call_soon_threadsafe().
# ══════════════════════════════════════════════════════════════════════════════

_tg_loop = asyncio.new_event_loop()
threading.Thread(target=_tg_loop.run_forever, daemon=True, name="tg-auth").start()

_auth_state: dict = {
    # step: idle | connecting | need_phone | sent_phone |
    #        need_code | need_password | done | error
    "step":         "idle",
    "phone_future": None,
    "code_future":  None,
    "pw_future":    None,
    "username":     None,
    "error":        None,
}
_auth_lock = threading.Lock()


_quality_session_string: "str | None" = None
_quality_session_lock   = threading.Lock()


def _read_session_string_from_file() -> "str | None":
    """StringSession from the plaintext SQLite session (shared reader)."""
    return tgd_common.read_session_file_string()


def _get_quality_session() -> "str | None":
    """Cached StringSession — from the OS keyring when enabled, else the file."""
    global _quality_session_string
    with _quality_session_lock:
        if not _quality_session_string:
            _quality_session_string = (tgd_common.load_session_string()
                                       or tgd_common.read_session_file_string())
        return _quality_session_string

async def _do_telegram_auth() -> None:
    """Full Telegram auth coroutine. Runs inside _tg_loop."""
    phone_fut: asyncio.Future = _tg_loop.create_future()
    code_fut:  asyncio.Future = _tg_loop.create_future()
    pw_fut:    asyncio.Future = _tg_loop.create_future()

    with _auth_lock:
        _auth_state.update({
            "step":         "connecting",
            "phone_future": phone_fut,
            "code_future":  code_fut,
            "pw_future":    pw_fut,
            "error":        None,
            "username":     None,
        })

    # These async callables are passed to client.start() as callbacks.
    # Telethon awaits them, so they can block on a Future without freezing
    # the event loop.

    async def _get_phone() -> str:
        with _auth_lock:
            _auth_state["step"] = "need_phone"
        phone = await phone_fut
        with _auth_lock:
            _auth_state["step"] = "sent_phone"
        return phone

    async def _get_code() -> str:
        with _auth_lock:
            _auth_state["step"] = "need_code"
        return await code_fut

    async def _get_pw() -> str:
        with _auth_lock:
            _auth_state["step"] = "need_password"
        return await pw_fut

    try:
        # Lazy import — keep startup fast and avoid loading Telethon in GUI process
        if str(BUNDLE_DIR) not in sys.path:
            sys.path.insert(0, str(BUNDLE_DIR))
        from telethon import TelegramClient as _TGClient  # type: ignore
        from telethon.sessions import StringSession as _SS

        _api_id, _api_hash = _load_api_credentials()
        _use_keyring = bool(tgd_common.load_config().get("use_keyring"))
        if _use_keyring:
            # Keep the auth key off disk: authorise into an in-memory
            # StringSession (seeded from the keyring if we already have one).
            _existing = tgd_common.load_session_string()
            client = _TGClient(_SS(_existing) if _existing else _SS(),
                               _api_id, _api_hash)
        else:
            client = _TGClient(str(DATA_DIR / "tg_audio_session"), _api_id, _api_hash)
        await client.start(
            phone=_get_phone,
            code_callback=_get_code,
            password=_get_pw,
        )
        me = await client.get_me()
        # Cache the StringSession so quality checks never touch disk; persist it to
        # the keyring (and drop any plaintext file) when keyring storage is on.
        try:
            _sess_str = _SS.save(client.session)
            with _quality_session_lock:
                global _quality_session_string
                _quality_session_string = _sess_str
            if _use_keyring:
                tgd_common.save_session_string(_sess_str)
                tgd_common.remove_session_file()
        except Exception:
            pass
        with _auth_lock:
            _auth_state["step"]     = "done"
            _auth_state["username"] = me.first_name
        logger.info("Telegram auth complete: %s", me.first_name)
        await client.disconnect()

    except Exception as exc:
        logger.exception("Telegram auth failed")
        with _auth_lock:
            _auth_state["step"]  = "error"
            _auth_state["error"] = str(exc)


def _wait_auth(target_steps: set[str], timeout: float = 30.0) -> str:
    """Block the calling (HTTP handler) thread until step is in target_steps."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        with _auth_lock:
            s = _auth_state["step"]
        if s in target_steps:
            return s
    with _auth_lock:
        return _auth_state["step"]


# ══════════════════════════════════════════════════════════════════════════════
#  TELEGRAM QUALITY  (persistent client — fast get/set without reconnecting)
# ══════════════════════════════════════════════════════════════════════════════
#
#  Strategy: keep one long-lived TelegramClient (_q_client) open on _tg_loop.
#  Cache the quality-menu message object (_q_menu_msg) so repeated calls skip
#  the /settings round-trip entirely.  The menu is invalidated after any set.
#
#  All access is from coroutines on _tg_loop so no extra locking is needed.

# Quality state is now purely local — no bot comms needed.


def _has_quality_btns(msg) -> bool:
    if not msg or not msg.reply_markup:
        return False
    btns = [b.text for row in msg.reply_markup.rows for b in row.buttons]
    return any("flac" in t.lower() or "mp3" in t.lower() for t in btns)



async def _tg_get_quality() -> dict:
    """Read quality setting from local config (no bot comms)."""
    try:
        return {"quality": tgd_common.load_config().get("target_quality", "FLAC")}
    except Exception as exc:
        return {"error": str(exc)}


async def _tg_set_quality(target: str) -> dict:
    """Write quality setting to local config (no bot comms)."""
    try:
        cfg = tgd_common.load_config()
        cfg["target_quality"] = target
        tgd_common.save_config(cfg)
        return {"ok": True, "quality": target}
    except Exception as exc:
        return {"error": str(exc)}


def _pick_folder_native(initial: str = "") -> str:
    """Open a Windows folder-picker dialog in a worker thread. Returns path or ''."""
    result   = [""]
    done_evt = threading.Event()

    def _run():
        try:
            import tkinter
            import tkinter.filedialog
            root = tkinter.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            chosen = tkinter.filedialog.askdirectory(
                title="Select Home Music Folder",
                initialdir=initial or None,
            )
            root.destroy()
            result[0] = chosen or ""
        except Exception:
            pass
        finally:
            done_evt.set()

    threading.Thread(target=_run, daemon=True).start()
    done_evt.wait(timeout=120)
    return result[0]


# ══════════════════════════════════════════════
#  MINIMAL WEBSOCKET  (stdlib only)
# ══════════════════════════════════════════════

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_handshake(conn, key: str):
    accept = b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    conn.sendall((
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
    ).encode())


def _ws_recv(conn) -> str | None:
    try:
        h = conn.recv(2)
        if len(h) < 2:
            return None
        b1, b2 = h
        masked = bool(b2 & 0x80)
        n = b2 & 0x7F
        if n == 126:
            n = struct.unpack(">H", conn.recv(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", conn.recv(8))[0]
        mask = conn.recv(4) if masked else b"\x00\x00\x00\x00"
        data = conn.recv(n)
        return bytes(b ^ mask[i % 4] for i, b in enumerate(data)).decode("utf-8", errors="replace")
    except Exception:
        return None


def _ws_send(conn, text: str) -> bool:
    try:
        p = text.encode("utf-8")
        n = len(p)
        if n <= 125:
            h = struct.pack("BB", 0x81, n)
        elif n <= 65535:
            h = struct.pack(">BBH", 0x81, 126, n)
        else:
            h = struct.pack(">BBQ", 0x81, 127, n)
        conn.sendall(h + p)
        return True
    except Exception:
        return False


# ══════════════════════════════════════════════
#  SESSIONS HELPERS
# ══════════════════════════════════════════════

def _load_sessions() -> dict:
    if SESSIONS_FILE.exists():
        try:
            return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_sessions(data: dict) -> None:
    SESSIONS_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ══════════════════════════════════════════════
#  PROCESS MANAGER
# ══════════════════════════════════════════════

# ── Telegram flood-wait health (v1.8.0) ───────────────────────────────────────
# The backend logs "⏳ Flood-wait Ns for <file> (attempt …" when Telegram
# rate-limits DC auth; the stdout relay below records them so /telegram-health
# can show how often the account is being throttled.
_FLOOD_RE = re.compile(r"Flood-wait (\d+)s for (.+?) \(attempt")
_flood_events: "deque[dict]" = deque(maxlen=200)

# The backend speaks JSON-lines: one message per stdout line, each a dict with a
# "type".  Anything else on the pipe (stray prints, tracebacks merged in from
# stderr) is not structured and is surfaced to the user as a raw log line.
_PROTOCOL_TYPES = frozenset({"log", "result", "progress", "paused", "resumed"})


def _parse_backend_line(raw: str) -> "dict | None":
    s = raw.strip()
    if s[:1] == "{" and s[-1:] == "}":
        try:
            obj = json.loads(s)
        except Exception:
            return None
        if isinstance(obj, dict) and obj.get("type") in _PROTOCOL_TYPES:
            return obj
    return None


def _flood_health(events: "list[dict] | None" = None,
                  now: "float | None" = None) -> dict:
    """Pure summary of recorded flood-wait events: counts for the rolling
    hour/day, total wait seconds, and the most recent events."""
    evs = list(_flood_events) if events is None else list(events)
    now = time.time() if now is None else now
    hour = [e for e in evs if now - e.get("t", 0) <= 3600]
    day  = [e for e in evs if now - e.get("t", 0) <= 86400]
    recent = [{"wait": e.get("wait", 0), "file": e.get("file", ""),
               "ts": time.strftime("%H:%M:%S", time.localtime(e.get("t", 0)))}
              for e in evs[-10:]][::-1]
    return {
        "total_recorded": len(evs),
        "last_hour":      len(hour),
        "last_24h":       len(day),
        "wait_secs_24h":  sum(e.get("wait", 0) for e in day),
        "recent":         recent,
    }


class ProcessManager:
    # Seconds to wait after the last client disconnects before shutting the
    # whole app down. A page reload drops and re-opens the socket within ~1.5s,
    # so this grace window keeps reloads from killing the server while still
    # quitting promptly when the browser window/tab is actually closed.
    _SHUTDOWN_GRACE = 3.0

    def __init__(self):
        self._proc    = None
        self._lock    = threading.Lock()
        self._clients = []
        self._cl_lock = threading.Lock()
        self._ever_connected = False
        self._shutdown_timer = None

    def add_client(self, conn):
        with self._cl_lock:
            self._clients.append(conn)
            self._ever_connected = True
            # A (re)connection cancels any pending auto-shutdown.
            if self._shutdown_timer is not None:
                self._shutdown_timer.cancel()
                self._shutdown_timer = None

    def remove_client(self, conn):
        with self._cl_lock:
            try:
                self._clients.remove(conn)
            except ValueError:
                pass
            # When the last UI client goes away (window closed), quit the app
            # after a short grace period unless a client reconnects (reload).
            if self._ever_connected and not self._clients and self._shutdown_timer is None:
                self._shutdown_timer = threading.Timer(
                    self._SHUTDOWN_GRACE, self._auto_shutdown
                )
                self._shutdown_timer.daemon = True
                self._shutdown_timer.start()

    def _auto_shutdown(self):
        with self._cl_lock:
            if self._clients:        # a client reconnected in the meantime
                self._shutdown_timer = None
                return
        logger.info("All UI clients disconnected — shutting down app.")
        try:
            self.stop()
        except Exception:
            pass
        server = globals().get("SERVER")
        if server is not None:
            try:
                server.shutdown()
            except Exception:
                pass
        os._exit(0)

    def broadcast(self, msg: dict):
        text = json.dumps(msg)
        dead = []
        with self._cl_lock:
            for c in list(self._clients):
                if not _ws_send(c, text):
                    dead.append(c)
        for c in dead:
            self.remove_client(c)

    def is_running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    @staticmethod
    def _note_flood(text: str) -> None:
        m = _FLOOD_RE.search(text)
        if m:
            _flood_events.append({"t": time.time(),
                                  "wait": int(m.group(1)),
                                  "file": m.group(2)[:200]})

    def start(self, stdin_data: str):
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return

            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONUTF8"]       = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["TGD_DATA_DIR"]   = str(DATA_DIR)
            env["TGD_BUNDLE_DIR"] = str(BUNDLE_DIR)
            env["TGD_LOG_FILE"]   = str(LOG_FILE)

            self._proc = subprocess.Popen(
                _BACKEND_CMD,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                cwd=str(DATA_DIR),
            )
            self._proc.stdin.write(stdin_data)
            self._proc.stdin.close()
            logger.info("Backend subprocess started (pid=%s)", self._proc.pid)

        def _stream():
            try:
                for line in iter(self._proc.stdout.readline, ""):
                    msg = _parse_backend_line(line)
                    if msg is None:
                        # Not JSON: a legacy marker or stray stdout/stderr.
                        if line.startswith("##RESULT## "):
                            try:
                                self.broadcast({"type": "result", **json.loads(line[11:])})
                            except Exception:
                                pass
                            continue
                        self._note_flood(line)
                        self.broadcast({"type": "log", "text": line})
                        continue
                    t = msg["type"]
                    if t == "log":
                        text = msg.get("text", "")
                        self._note_flood(text)
                        self.broadcast({"type": "log", "text": text})
                    elif t == "result":
                        self.broadcast({"type": "result",
                                        **{k: v for k, v in msg.items() if k != "type"}})
                    else:  # progress / paused / resumed pass straight through
                        self.broadcast(msg)
                self._proc.wait()
                rc = self._proc.returncode
            except Exception as ex:
                rc = -1
                self.broadcast({"type": "log", "text": f"\nServer error: {ex}\n"})
            finally:
                logger.info("Backend subprocess exited (rc=%s)", rc)
                self.broadcast({"type": "done", "code": rc})
                with self._lock:
                    self._proc = None

        threading.Thread(target=_stream, daemon=True).start()

    def stop(self):
        # Clear pause flag so next run starts unpaused
        try: (DATA_DIR / "pause.flag").unlink(missing_ok=True)
        except Exception: pass
        with self._lock:
            if self._proc and self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    logger.info("Backend subprocess terminated")
                except Exception:
                    pass


MANAGER = ProcessManager()
SERVER: "Server | None" = None


# ══════════════════════════════════════════════
#  SCHEDULED QUEUE RUN  (v1.9.0 — one pending schedule, in-memory)
# ══════════════════════════════════════════════

def _build_backend_stdin(entries: list) -> str:
    """The stdin payload the backend expects: mode, count, then per entry
    url / artist / playlist-name lines, closed with a confirmation."""
    lines = ["1", str(len(entries))]
    for e in entries:
        lines.append(e.get("url", ""))
        lines.append(e.get("artist", ""))
        pl = e.get("playlistName", "") if e.get("isPlaylist") else ""
        lines.append(pl.replace("\n", " ").strip())
    lines.append("Y")
    return "\n".join(lines) + "\n"


_schedule_lock = threading.Lock()
_scheduled: "dict | None" = None            # {at, entries, home, created}
_schedule_timer: "threading.Timer | None" = None


def _schedule_status() -> dict:
    with _schedule_lock:
        if not _scheduled:
            return {"scheduled": False}
        return {
            "scheduled": True,
            "at":        _scheduled["at"],
            "at_str":    time.strftime("%Y-%m-%d %H:%M",
                                       time.localtime(_scheduled["at"])),
            "entries":   len(_scheduled["entries"]),
            "in_secs":   max(0, int(_scheduled["at"] - time.time())),
        }


def _schedule_cancel() -> dict:
    global _scheduled, _schedule_timer
    with _schedule_lock:
        if _schedule_timer:
            _schedule_timer.cancel()
        _schedule_timer = None
        _scheduled = None
    return {"ok": True, "scheduled": False}


def _schedule_fire() -> None:
    global _scheduled, _schedule_timer
    with _schedule_lock:
        job = _scheduled
        _scheduled = None
        _schedule_timer = None
    if not job:
        return
    if MANAGER.is_running():
        logger.warning("Scheduled run skipped — a download is already running")
        MANAGER.broadcast({"type": "log",
                           "text": "Scheduled run skipped — a download is already running.\n"})
        return
    entries = job["entries"]
    if any(e.get("isPlaylist") for e in entries):
        try:
            _write_playlist_meta(job.get("home", ""), entries)
        except Exception:
            logger.exception("playlist meta prep failed (scheduled run)")
    logger.info("Scheduled run starting (%d entries)", len(entries))
    MANAGER.broadcast({"type": "log",
                       "text": f"Scheduled run starting — {len(entries)} URL(s)\n"})
    MANAGER.start(_build_backend_stdin(entries))
    MANAGER.broadcast({"type": "status", "running": True})


def _schedule_set(at: float, entries: list, home: str = "") -> dict:
    """Arm (or re-arm — one schedule at a time) a queue start at epoch *at*."""
    global _scheduled, _schedule_timer
    if not entries or not isinstance(entries, list):
        return {"error": "Queue is empty"}
    delay = at - time.time()
    if delay < 5:
        return {"error": "Scheduled time must be in the future"}
    if delay > 7 * 86400:
        return {"error": "Scheduled time must be within the next 7 days"}
    with _schedule_lock:
        if _schedule_timer:
            _schedule_timer.cancel()
        _scheduled = {"at": float(at), "entries": entries,
                      "home": home or "", "created": time.time()}
        _schedule_timer = threading.Timer(delay, _schedule_fire)
        _schedule_timer.daemon = True
        _schedule_timer.start()
    logger.info("Queue run scheduled for %s (%d entries)",
                time.strftime("%Y-%m-%d %H:%M", time.localtime(at)), len(entries))
    return _schedule_status()


# ── Filesystem watcher: auto-refresh the library when files change on disk ─────
# A lightweight polling thread (no watchdog dependency, matching the app's
# stdlib-first style) that fingerprints the album-folder layout and pushes a
# "library-changed" event to connected clients when it shifts. Files added,
# removed or moved by other tools — or dropped straight into the Music folder —
# then appear without pressing Refresh.
_watch_lock   = threading.Lock()
_watch_thread: "threading.Thread | None" = None
_watch_stop:   "threading.Event | None"  = None
_watch_home   = ""
_WATCH_INTERVAL = 5.0   # seconds between disk polls


def _lib_watch_signature(home_path: Path) -> str:
    """Cheap fingerprint that changes when tracks/albums are added, removed or
    moved. Reuses the /library-stats cache validator, so it reacts to exactly
    the changes the library view cares about with only per-directory stat()
    calls — no file reads."""
    artists_root = home_path / ARTISTS_DIRNAME
    if not artists_root.is_dir():
        artists_root = home_path
    return _library_stats_signature(home_path, artists_root)


def _lib_watcher_loop(home: str, stop: "threading.Event") -> None:
    home_path = Path(home)
    try:
        last_sig = _lib_watch_signature(home_path)
    except Exception:
        last_sig = ""
    logger.info("Library watcher active on %s (every %.0fs)", home, _WATCH_INTERVAL)
    while not stop.wait(_WATCH_INTERVAL):
        # A running download churns the library itself; the queue's own 'done'
        # event already refreshes it, so don't poll (or double-fire) mid-write.
        if MANAGER.is_running():
            continue
        try:
            sig = _lib_watch_signature(home_path)
        except Exception as exc:
            logger.debug("Library watcher poll failed: %s", exc)
            continue
        if sig != last_sig:
            last_sig = sig
            logger.info("Library change detected on disk — notifying clients")
            MANAGER.broadcast({"type": "library-changed"})
    logger.info("Library watcher stopped")


def _lib_watch_status() -> dict:
    with _watch_lock:
        alive = bool(_watch_thread and _watch_thread.is_alive())
        return {"watching": alive, "home": _watch_home if alive else "",
                "interval": _WATCH_INTERVAL}


def _start_lib_watcher(home: str) -> dict:
    """(Re)start the watcher on *home*. Idempotent — calling again restarts it
    cleanly (used when the music folder changes or the flag is re-applied)."""
    global _watch_thread, _watch_stop, _watch_home
    if not home:
        return {"error": "No music folder is configured yet."}
    if not Path(home).exists():
        return {"error": "The configured music folder doesn't exist."}
    with _watch_lock:
        if _watch_stop:
            _watch_stop.set()          # signal any previous thread to exit
        _watch_stop   = threading.Event()
        _watch_home   = home
        _watch_thread = threading.Thread(
            target=_lib_watcher_loop, args=(home, _watch_stop),
            daemon=True, name="lib-watch")
        _watch_thread.start()
    return _lib_watch_status()


def _stop_lib_watcher() -> dict:
    global _watch_thread, _watch_stop, _watch_home
    with _watch_lock:
        if _watch_stop:
            _watch_stop.set()
        _watch_stop   = None
        _watch_thread = None
        _watch_home   = ""
    return {"watching": False, "home": "", "interval": _WATCH_INTERVAL}


def _apply_lib_watcher(cfg: dict) -> dict:
    """Start or stop the watcher to match the persisted `watch_library` flag."""
    home = cfg.get("home_music_folder") or ""
    if cfg.get("watch_library") and home:
        return _start_lib_watcher(home)
    return _stop_lib_watcher()


# ── Discord Rich Presence (opt-in) ────────────────────────────────────────────
# Shows the current track on the user's Discord profile. Entirely best-effort:
# the IPC client no-ops when Discord isn't running or no client id is set, and
# every update runs on a daemon thread so it can never stall an HTTP request.
_presence = None                       # discord_presence.DiscordPresence | None
_presence_meta: dict = {}              # last {title, artist, album, position}


def _presence_client():
    global _presence
    if _presence is None:
        try:
            import discord_presence
            _presence = discord_presence.DiscordPresence()
        except Exception as exc:
            logger.debug("Discord presence unavailable: %s", exc)
    return _presence


def _presence_apply(cfg: dict) -> None:
    """Connect or disconnect to match the `discord_rich_presence` flag. The
    blocking connect/close runs on a daemon thread."""
    client = _presence_client()
    if client is None:
        return
    enabled = bool(cfg.get("discord_rich_presence"))
    cid     = (cfg.get("discord_client_id") or "").strip()
    if enabled and cid:
        threading.Thread(target=client.connect, args=(cid,),
                         daemon=True, name="discord-connect").start()
    else:
        threading.Thread(target=client.close, daemon=True).start()


def _presence_update(op: str, meta: dict) -> None:
    """Apply a play / resume / pause / stop presence change on a worker thread."""
    client = _presence_client()
    if client is None or not client.connected:
        return

    def _run():
        global _presence_meta
        try:
            import discord_presence
            if op == "stop":
                _presence_meta = {}
                client.clear()
                return
            if op in ("play", "resume") and (meta.get("title") or meta.get("artist")):
                _presence_meta = dict(meta)
            m = _presence_meta
            act = discord_presence.build_activity(
                m.get("title", ""), m.get("artist", ""), m.get("album", ""),
                float(m.get("position") or 0), paused=(op == "pause"))
            if act:
                client.set_activity(act)
        except Exception as exc:
            logger.debug("Discord presence update failed: %s", exc)

    threading.Thread(target=_run, daemon=True, name="discord-presence").start()


# ── Library tab caches (process lifetime) ─────────────────────────────────────
_cover_cache:         dict[str, str]          = {}   # deezer album_id → cover_medium URL
_path_hash_map:       dict[str, "Path"]       = {}   # path_hash[:16] → album_dir Path
_local_cover_cache:   dict[str, tuple]        = {}   # path_hash → (bytes, mime_type)
_track_cover_cache:   dict[str, "tuple | None"] = {} # path_hash\x00name → (bytes, mime)|None
_artist_cache:        dict[str, dict]         = {}   # deezer artist_id → artist metadata
_album_artist_id:     dict[str, str]          = {}   # deezer album_id  → artist_id
_bio_cache:           dict[str, dict]         = {}   # lowercased artist name → wikipedia bio dict
_lyrics_cache:        dict[str, dict]         = {}   # (artist,title) → {synced, plain} lyrics
_album_search_cache:  dict[str, str]          = {}   # "artist|album" → deezer album_id (or "" if not found)

# Load persisted album-ID cache so repeat library loads don't re-search Deezer
try:
    if _ALBUM_ID_CACHE_FILE.exists():
        _album_search_cache = json.loads(_ALBUM_ID_CACHE_FILE.read_text(encoding="utf-8"))
except Exception:
    pass

def _save_album_id_cache() -> None:
    try:
        _ALBUM_ID_CACHE_FILE.write_text(
            json.dumps(_album_search_cache, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass


# ══════════════════════════════════════════════
#  ARTIST METADATA  (cached, Deezer)
# ══════════════════════════════════════════════

def _deezer_artist_meta(artist_id: str) -> dict:
    """Fetch full artist metadata from Deezer (cached in _artist_cache)."""
    if artist_id in _artist_cache:
        return _artist_cache[artist_id]
    try:
        api_url = f"https://api.deezer.com/artist/{artist_id}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("id") and not data.get("error"):
            _artist_cache[artist_id] = data
            return data
    except Exception as exc:
        logger.debug("Artist meta fetch failed for id=%s: %s", artist_id, exc)
    return {"error": f"Artist {artist_id} not found"}


def _wikipedia_bio(name: str) -> dict:
    """Best-effort artist biography from Wikipedia (Deezer has none).

    Searches for the most relevant page (biased toward musicians/bands), then
    returns its plain-text summary extract plus the canonical page URL and a
    thumbnail. Results are cached per artist name to avoid repeat lookups.
    """
    key = (name or "").strip().lower()
    if not key:
        return {"error": "empty name"}
    if key in _bio_cache:
        return _bio_cache[key]

    headers = {"User-Agent": "TGDownloader/6 (local music library app)"}

    def _summary(title: str) -> dict:
        """Return the Wikipedia REST summary for a page title, or {}."""
        try:
            sum_url = (
                "https://en.wikipedia.org/api/rest_v1/page/summary/"
                + url_quote(title.replace(" ", "_"))
            )
            req = urllib.request.Request(sum_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception:
            return {}

    def _looks_musical(md: dict) -> bool:
        """Heuristic: is this summary about a musician/band, not a song/album?"""
        desc = (md.get("description") or "").lower()
        extract = (md.get("extract") or "").lower()
        bad = ("song", "album", "single", "ep by", "soundtrack", "film", "movie")
        good = ("band", "musician", "rapper", "singer", "songwriter", "duo",
                "trio", "group", "producer", "dj", "record producer",
                "hip hop", "recording artist")
        if any(b in desc for b in bad):
            return False
        if any(g in desc for g in good):
            return True
        # No description hint — accept unless the extract opens like a song/album
        return not extract.startswith(('"', "the album", "is a song", "is a studio"))

    def _accept(md: dict) -> "dict | None":
        if not md or md.get("type") == "disambiguation":
            return None
        extract = (md.get("extract") or "").strip()
        if not extract or not _looks_musical(md):
            return None
        return {
            "bio":       extract,
            "title":     md.get("title") or "",
            "url":       ((md.get("content_urls") or {}).get("desktop") or {}).get("page", ""),
            "thumbnail": (md.get("thumbnail") or {}).get("source", ""),
        }

    result: dict = {"error": "no match"}
    try:
        # 1) Try the page that exactly matches the artist name (most reliable —
        #    "De La Soul", "Eazy-E", "Radiohead" all resolve straight to the act).
        got = _accept(_summary(name))
        if not got:
            # 2) Fall back to a music-biased search, checking the top hits.
            search_url = (
                "https://en.wikipedia.org/w/api.php?action=query&list=search&format=json"
                f"&srlimit=5&srsearch={url_quote(name + ' band OR musician OR rapper OR singer')}"
            )
            req = urllib.request.Request(search_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                sd = json.loads(resp.read().decode("utf-8"))
            for hit in ((sd.get("query") or {}).get("search") or []):
                got = _accept(_summary(hit.get("title", "")))
                if got:
                    break
        if got:
            result = got
    except Exception as exc:
        logger.debug("Wikipedia bio fetch failed for %s: %s", name, exc)
        result = {"error": str(exc)}

    _bio_cache[key] = result
    return result


def _deezer_radio(name: str, artist_id: str = "") -> dict:
    """Build a 'radio' queue of Deezer 30s previews seeded from an artist and
    their related artists' top tracks. Used to auto-continue when a queue ends."""
    headers = {"User-Agent": "TGDownloader/6"}
    aid = (artist_id or "").strip()
    if not aid and name:
        try:
            su = f"https://api.deezer.com/search/artist?q={url_quote(name)}&limit=1&output=json"
            with urllib.request.urlopen(urllib.request.Request(su, headers=headers), timeout=10) as r:
                hit = (json.loads(r.read().decode("utf-8")).get("data") or [{}])[0]
            aid = str(hit.get("id") or "")
        except Exception:
            aid = ""
    if not aid:
        return {"error": "artist not found"}

    tracks: list = []
    seen: set = set()

    def _add_top(a_id, limit):
        try:
            tu = f"https://api.deezer.com/artist/{a_id}/top?limit={limit}&output=json"
            with urllib.request.urlopen(urllib.request.Request(tu, headers=headers), timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            return
        for t in (data.get("data") or []):
            prev = t.get("preview")
            if not prev:
                continue
            key = (t.get("title", "").lower(), (t.get("artist") or {}).get("name", "").lower())
            if key in seen:
                continue
            seen.add(key)
            tracks.append({
                "title":       t.get("title", ""),
                "artist":      (t.get("artist") or {}).get("name", ""),
                "album":       (t.get("album") or {}).get("title", ""),
                "preview_url": prev,
                "cover_url":   (t.get("album") or {}).get("cover_medium")
                               or (t.get("album") or {}).get("cover_small") or "",
                "duration":    t.get("duration", 30),
                "duration_str": _fmt_duration(int(t.get("duration", 30) or 30)),
            })

    _add_top(aid, 8)
    try:
        ru = f"https://api.deezer.com/artist/{aid}/related?limit=6&output=json"
        with urllib.request.urlopen(urllib.request.Request(ru, headers=headers), timeout=10) as r:
            related = json.loads(r.read().decode("utf-8")).get("data") or []
    except Exception:
        related = []
    for ra in related:
        if ra.get("id"):
            _add_top(ra["id"], 4)

    import random as _rnd
    _rnd.shuffle(tracks)
    return {"tracks": tracks[:40]}


def _ffmpeg_exe() -> "str | None":
    """Path to a usable ffmpeg binary, or None. Cached after first lookup."""
    return tgd_common.ffmpeg_exe()


def _transcode_to_mp3(src: "Path") -> "Path | None":
    """Transcode an audio file to a browser-playable MP3 (cached on disk).
    Used as a fallback when the browser's <audio> can't decode the original
    (e.g. 24-bit / hi-res FLAC, or exotic codecs). Returns the output path,
    or None if ffmpeg is unavailable or the transcode failed."""
    ff = _ffmpeg_exe()
    if not ff:
        return None
    try:
        import hashlib
        st  = src.stat()
        key = hashlib.sha1(f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()
        _TRANSCODE_DIR.mkdir(parents=True, exist_ok=True)
        out = _TRANSCODE_DIR / (key + ".mp3")
        if out.exists() and out.stat().st_size > 0:
            return out
        tmp = out.with_name(out.stem + ".part.mp3")
        cmd = [ff, "-y", "-vn", "-i", str(src), "-map", "0:a:0",
               "-c:a", "libmp3lame", "-q:a", "2", str(tmp)]
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=180)
        if proc.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
            tmp.replace(out)
            return out
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
    except Exception as exc:
        logger.debug("Transcode failed for %s: %s", src, exc)
    return None


def _parse_range_header(range_header: str, file_size: int) -> "tuple[int, int] | None":
    """Parse a single-range ``bytes=`` header into an inclusive (start, end).

    Handles suffix ranges (``bytes=-500`` = last 500 bytes), which the old
    inline parser silently misread as "from byte 0".  Returns None when the
    range is unsatisfiable (caller should answer 416).  A malformed header is
    treated as "no range" per RFC 7233 and yields the full span."""
    start, end = 0, file_size - 1
    if range_header.startswith("bytes="):
        spec = range_header[6:].split(",")[0].strip()
        s, _, e = spec.partition("-")
        try:
            if not s:
                n = int(e)              # suffix range: last n bytes
                if n <= 0:
                    return None
                start = max(0, file_size - n)
            else:
                start = int(s)
                if e:
                    end = min(int(e), file_size - 1)
            if start > end or start >= file_size:
                return None
        except ValueError:
            return 0, file_size - 1     # malformed → serve the whole file
    return start, end


def _send_file_with_range(h, file_path: "Path", mime: str) -> None:
    """Stream a file to the client with HTTP Range support (so the browser can
    seek). Shared by /audio-stream (transcoded) and /audio-file playback."""
    file_size = file_path.stat().st_size
    range_header = h.headers.get("Range", "")
    span = _parse_range_header(range_header, file_size)
    if span is None:
        h.send_response(416)
        h.send_header("Content-Range", f"bytes */{file_size}")
        h.end_headers()
        return
    start, end = span
    length = end - start + 1
    h.send_response(206 if range_header else 200)
    h.send_header("Content-Type", mime)
    h.send_header("Content-Length", str(length))
    h.send_header("Accept-Ranges", "bytes")
    h.send_header("Cache-Control", "no-cache")
    if range_header:
        h.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
    h.end_headers()
    try:
        with open(file_path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                h.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _resolve_in_dir(album_dir: "Path", filename: str) -> "Path | None":
    """Resolve `filename` inside `album_dir` (path-traversal guarded), with a
    Unicode-NFC / case-insensitive fallback when the exact name misses."""
    if not album_dir or not filename:
        return None
    base = album_dir.resolve()
    file_path = (album_dir / filename).resolve()
    try:
        file_path.relative_to(base)
    except ValueError:
        return None
    if file_path.is_file():
        return file_path
    try:
        import unicodedata as _ud
        want = _ud.normalize("NFC", filename).casefold()
        for f in album_dir.iterdir():
            if f.is_file() and _ud.normalize("NFC", f.name).casefold() == want:
                return f.resolve()
    except Exception:
        pass
    return None


def _rebuild_path_hash_map() -> None:
    """Populate _path_hash_map (path_hash → album/playlist dir) by scanning the
    music library directly.

    The map is normally filled lazily as a side effect of /library-albums, but
    playback can request a hash before the frontend has ever loaded the library
    tab — most notably the persist-restore on startup, which sets up a paused
    player (and later /audio-file, /audio-stream, /track-cover) using hashes
    saved in a previous session. Without this, those requests 404 purely because
    the map is empty, even though the files exist on disk."""
    try:
        import hashlib as _hl
        from pathlib import Path as _P
        m    = _tgd_import()
        cfg  = m.load_config()
        home = cfg.get("home_music_folder")
        if not home:
            return
        home_path = _P(home)
        if not home_path.exists():
            return
        AUDIO_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
                     ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}

        def _add(d: "_P") -> None:
            try:
                if not any(f.is_file() and f.suffix.lower() in AUDIO_EXT
                           for f in d.iterdir()):
                    return
            except Exception:
                return
            ph = _hl.sha256(str(d.resolve()).encode()).hexdigest()[:16]
            _path_hash_map[ph] = d

        artists_root = home_path / ARTISTS_DIRNAME
        if artists_root.is_dir():
            for artist_dir in artists_root.iterdir():
                if not artist_dir.is_dir() or artist_dir.name.startswith("."):
                    continue
                for album_dir in artist_dir.iterdir():
                    if album_dir.is_dir():
                        _add(album_dir)

        playlists_root = home_path / PLAYLISTS_DIRNAME
        if playlists_root.is_dir():
            for pl_dir in playlists_root.iterdir():
                if pl_dir.is_dir() and not pl_dir.name.startswith("."):
                    _add(pl_dir)
    except Exception:
        pass


def _lookup_album_dir(ph: str):
    """Resolve a path_hash to its dir, rebuilding the map once if it's missing
    (e.g. playback began before the library was ever scanned this session)."""
    ph = (ph or "").strip()
    d = _path_hash_map.get(ph)
    if d is None and ph:
        _rebuild_path_hash_map()
        d = _path_hash_map.get(ph)
    return d


def _deezer_recommendations(seed_names: list) -> dict:
    """Album recommendations seeded from the user's library: for a few seed
    artists, pull Deezer related artists and their top albums. Returns album
    cards (downloadable via their Deezer link)."""
    headers = {"User-Agent": "TGDownloader/6"}
    seeds = [s for s in (seed_names or []) if s][:4]
    if not seeds:
        return {"albums": []}

    out: list = []
    seen: set = set()

    def _resolve_id(name):
        try:
            su = f"https://api.deezer.com/search/artist?q={url_quote(name)}&limit=1&output=json"
            with urllib.request.urlopen(urllib.request.Request(su, headers=headers), timeout=10) as r:
                hit = (json.loads(r.read().decode("utf-8")).get("data") or [{}])[0]
            return str(hit.get("id") or "")
        except Exception:
            return ""

    def _add_albums(a_id, limit):
        try:
            au = f"https://api.deezer.com/artist/{a_id}/albums?limit={limit}&output=json"
            with urllib.request.urlopen(urllib.request.Request(au, headers=headers), timeout=10) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            return
        for al in (data.get("data") or []):
            if (al.get("record_type") or "album").lower() != "album":
                continue
            key = (al.get("title", "").lower(), (al.get("artist") or {}).get("name", "").lower())
            if not al.get("id") or key in seen:
                continue
            seen.add(key)
            out.append({
                "title":  al.get("title", ""),
                "artist": (al.get("artist") or {}).get("name", ""),
                "id":     al.get("id"),
                "cover":  al.get("cover_medium") or al.get("cover_small") or "",
                "link":   al.get("link") or f"https://www.deezer.com/album/{al.get('id')}",
                "nb_tracks": al.get("nb_tracks"),
            })

    for name in seeds:
        aid = _resolve_id(name)
        if not aid:
            continue
        try:
            ru = f"https://api.deezer.com/artist/{aid}/related?limit=4&output=json"
            with urllib.request.urlopen(urllib.request.Request(ru, headers=headers), timeout=10) as r:
                related = json.loads(r.read().decode("utf-8")).get("data") or []
        except Exception:
            related = []
        for ra in related:
            if ra.get("id"):
                _add_albums(ra["id"], 3)

    import random as _rnd
    _rnd.shuffle(out)
    return {"albums": out[:12]}


def _fetch_lyrics(artist: str, title: str, album: str = "", duration: str = "") -> dict:
    """Fetch lyrics from LRCLIB (free, no key). Returns {synced, plain} where
    `synced` is LRC-timestamped text (may be empty) and `plain` is the plain
    lyrics. Tries an exact get first, then a fuzzy search. Cached per track."""
    artist = (artist or "").strip()
    title  = (title or "").strip()
    if not artist or not title:
        return {"error": "missing artist/title"}
    key = f"{artist.lower()}\x00{title.lower()}"
    if key in _lyrics_cache:
        return _lyrics_cache[key]

    headers = {"User-Agent": "TGDownloader/6 (https://github.com/Thurlws/TGDownloader)"}
    result: dict = {"error": "not found"}

    def _shape(d: dict) -> "dict | None":
        synced = (d.get("syncedLyrics") or "").strip()
        plain  = (d.get("plainLyrics")  or "").strip()
        if not synced and not plain:
            return None
        return {"synced": synced, "plain": plain,
                "title": d.get("trackName") or title, "artist": d.get("artistName") or artist}

    try:
        # 1) Exact get (best — uses album + duration to disambiguate)
        q = f"track_name={url_quote(title)}&artist_name={url_quote(artist)}"
        if album:
            q += f"&album_name={url_quote(album)}"
        if str(duration).isdigit():
            q += f"&duration={int(duration)}"
        try:
            req = urllib.request.Request("https://lrclib.net/api/get?" + q, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                shaped = _shape(json.loads(resp.read().decode("utf-8")))
                if shaped:
                    result = shaped
        except urllib.error.HTTPError:
            pass

        # 2) Fuzzy search fallback
        if "error" in result:
            req = urllib.request.Request(
                f"https://lrclib.net/api/search?track_name={url_quote(title)}&artist_name={url_quote(artist)}",
                headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                hits = json.loads(resp.read().decode("utf-8"))
            for h in (hits or []):
                shaped = _shape(h)
                if shaped:
                    result = shaped
                    break
    except Exception as exc:
        logger.debug("Lyrics fetch failed for %s - %s: %s", artist, title, exc)
        result = {"error": str(exc)}

    _lyrics_cache[key] = result
    return result


# ══════════════════════════════════════════════
#  ALBUM TRACK LISTING  (local filesystem + Deezer preview URLs)
# ══════════════════════════════════════════════

def _fmt_duration(secs: int) -> str:
    return f"{secs // 60}:{secs % 60:02d}"


def _get_album_tracks(album_dir: "Path", album_id: str = "") -> list:
    """Return list of audio tracks in album_dir with metadata and Deezer preview URLs."""
    AUDIO_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
                 ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}
    import re as _re

    try:
        files = sorted(
            f for f in album_dir.iterdir()
            if f.is_file() and f.suffix.lower() in AUDIO_EXT
        )
    except Exception:
        return []

    # Optional per-track playlist sidecar (date added), written at download time.
    sidecar: dict = {}
    try:
        sc = album_dir / ".tgplaylist.json"
        if sc.exists():
            sidecar = json.loads(sc.read_text(encoding="utf-8"))
    except Exception:
        sidecar = {}

    tracks = []
    for f in files:
        meta: dict = {
            "name":         f.name,
            "title":        None,
            "artist":       None,
            "album":        None,
            "track_num":    None,
            "duration":     0,
            "duration_str": "—",
            "preview_url":  None,
            "deezer_id":    None,
            "date_added":   "",
        }
        try:
            from mutagen import File as _MF
            audio = _MF(f, easy=True)
            if audio:
                if audio.info:
                    secs = int(audio.info.length)
                    meta["duration"]     = secs
                    meta["duration_str"] = _fmt_duration(secs)
                t_tag = audio.get("title")
                if t_tag:
                    meta["title"] = str(t_tag[0])
                a_tag = audio.get("artist")
                if a_tag:
                    meta["artist"] = str(a_tag[0])
                alb_tag = audio.get("album")
                if alb_tag:
                    meta["album"] = str(alb_tag[0])
                tn = audio.get("tracknumber")
                if tn:
                    try:
                        meta["track_num"] = int(str(tn[0]).split("/")[0])
                    except Exception:
                        pass
        except Exception:
            pass

        if not meta["title"]:
            stem = f.stem
            stem = _re.sub(r"^(\d{1,3}[.\-_\s]+)", "", stem).strip()
            meta["title"] = stem or f.stem

        # Date added: prefer the playlist sidecar, else the file's mtime.
        da = (sidecar.get(f.name) or {}).get("date_added", "")
        if not da:
            try:
                from datetime import datetime as _dt
                da = _dt.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
            except Exception:
                da = ""
        meta["date_added"] = da

        tracks.append(meta)

    # Sort by track number when available
    tracks.sort(key=lambda x: (x["track_num"] is None, x["track_num"] or 0, x["name"].lower()))

    # Fetch Deezer tracklist for preview URLs
    if album_id:
        try:
            api_url = f"https://api.deezer.com/album/{album_id}/tracks"
            req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                dz_data = json.loads(resp.read().decode("utf-8"))
            dz_tracks = dz_data.get("data", [])

            for i, track in enumerate(tracks):
                best: dict | None = None
                # Match by track_position first
                if track["track_num"]:
                    for dz in dz_tracks:
                        if dz.get("track_position") == track["track_num"]:
                            best = dz
                            break
                # Fallback: positional match
                if best is None and i < len(dz_tracks):
                    best = dz_tracks[i]

                if best:
                    track["preview_url"] = best.get("preview") or ""
                    track["deezer_id"]   = best.get("id")
                    if not track["title"]:
                        track["title"] = best.get("title", track["name"])
        except Exception as exc:
            logger.debug("Deezer tracklist fetch failed for album %s: %s", album_id, exc)

    return tracks


# ══════════════════════════════════════════════
#  LOCAL COVER EXTRACTION  (Phase 2 fallback)
# ══════════════════════════════════════════════

def _extract_cover_bytes(album_dir: Path) -> "tuple[bytes, str] | None":
    """Extract embedded cover art from the first tagged audio file in album_dir.
    Returns (image_bytes, mime_type) or None if nothing found."""
    audio_exts = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".aac"}

    # Prefer a sidecar cover image (cover.jpg / folder.png …) when present —
    # this is how downloaded playlists keep their original Spotify/Deezer art.
    _img_mimes = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".png": "image/png",  ".webp": "image/webp", ".gif": "image/gif"}
    for stem in ("cover", "folder", "front"):
        for ext, mime in _img_mimes.items():
            img = album_dir / f"{stem}{ext}"
            if img.is_file():
                try:
                    return img.read_bytes(), mime
                except Exception:
                    pass

    try:
        from mutagen import File as _MF
    except ImportError:
        return None

    try:
        candidates = sorted(
            f for f in album_dir.iterdir()
            if f.is_file() and f.suffix.lower() in audio_exts
        )
    except Exception:
        return None

    for f in candidates:
        try:
            tags = _MF(f)
            if tags is None:
                continue
            # ID3 tags (MP3 and others using ID3)
            for key in list(tags.keys()):
                if key.startswith("APIC"):
                    pic = tags[key]
                    return pic.data, pic.mime or "image/jpeg"
            # FLAC pictures
            if hasattr(tags, "pictures") and tags.pictures:
                p = tags.pictures[0]
                return p.data, p.mime or "image/jpeg"
            # M4A / AAC
            if "covr" in tags:
                img = tags["covr"][0]
                return bytes(img), "image/jpeg"
        except Exception:
            continue
    return None


def _extract_file_cover_bytes(f: "Path") -> "tuple[bytes, str] | None":
    """Extract embedded cover art from a single audio file (per-track thumbnails)."""
    try:
        from mutagen import File as _MF
    except ImportError:
        return None
    try:
        tags = _MF(f)
        if tags is None:
            return None
        for key in list(tags.keys()):
            if key.startswith("APIC"):
                pic = tags[key]
                return pic.data, pic.mime or "image/jpeg"
        if hasattr(tags, "pictures") and tags.pictures:
            p = tags.pictures[0]
            return p.data, p.mime or "image/jpeg"
        if "covr" in tags:
            img = tags["covr"][0]
            return bytes(img), "image/jpeg"
    except Exception:
        pass
    return None


# ══════════════════════════════════════════════
#  GENRE PLAYLISTS  (local tags via Mutagen)
# ══════════════════════════════════════════════

_GENRE_AUDIO_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
                    ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}
PLAYLISTS_DIRNAME = "Playlists"   # generated playlists live here, beside artist folders
ARTISTS_DIRNAME   = "Artists"     # parent folder that all artist folders live under


def _split_genres(raw_values: "list") -> "list[str]":
    """Turn raw genre tag value(s) into a clean, de-duplicated list of distinct
    genres.  Handles multi-value frames AND compound strings like
    "Hip-Hop/Rap", "Pop; Dance", "Rock, Alternative" or "Soul & Funk" so a
    track tagged with several genres surfaces under each one separately."""
    import re as _re
    seen: "dict[str, str]" = {}          # lower-case key → display form
    for val in (raw_values or []):
        for part in _re.split(r"\s*[/;,|&]\s*|\s+[-–]\s+", str(val)):
            g = part.strip()
            if not g:
                continue
            key = g.lower()
            if key not in seen:
                seen[key] = g
    return list(seen.values())


def _library_stats_signature(home_path: Path, artists_root: Path) -> str:
    """A cheap fingerprint of the library's current state, used to validate the
    persisted /library-stats cache. Combines the manifest file's mtime+size with
    every album directory's mtime — enough to catch downloads, additions and
    deletions without the expensive full-tree walk + per-file tag reads that the
    stats compute itself performs. Returns a short hex digest."""
    import hashlib as _hl
    parts: list = []
    try:
        mp = home_path / ".tgdownloader" / "manifest.json"
        if mp.exists():
            st = mp.stat()
            parts.append(f"m:{int(st.st_mtime)}:{st.st_size}")
    except Exception:
        pass
    try:
        if artists_root.is_dir():
            for artist_dir in sorted(artists_root.iterdir()):
                if not artist_dir.is_dir() or artist_dir.name.startswith("."):
                    continue
                for album_dir in sorted(artist_dir.iterdir()):
                    if album_dir.is_dir():
                        try:
                            parts.append(f"{artist_dir.name}/{album_dir.name}:{int(album_dir.stat().st_mtime)}")
                        except Exception:
                            pass
    except Exception:
        pass
    return _hl.sha1("|".join(parts).encode("utf-8")).hexdigest()


def _scan_genres(home_path: Path) -> "dict[str, list[dict]]":
    """Walk every artist/album/track in the library and group local audio files
    by their genre tag (read via Mutagen).  Returns { genre: [ {path, artist,
    album, title, track_num} ] }.  Files with no genre tag are skipped."""
    from mutagen import File as _MF

    genres: "dict[str, list[dict]]" = {}
    scan_root = home_path / ARTISTS_DIRNAME
    if not scan_root.is_dir():
        scan_root = home_path          # pre-migration fallback
    for artist_dir in sorted(scan_root.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        if artist_dir.name in (PLAYLISTS_DIRNAME, ARTISTS_DIRNAME):
            continue
        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir():
                continue
            for f in sorted(album_dir.iterdir()):
                if not f.is_file() or f.suffix.lower() not in _GENRE_AUDIO_EXT:
                    continue
                try:
                    audio = _MF(f, easy=True)
                except Exception:
                    audio = None
                if not audio:
                    continue
                g_tag = audio.get("genre")
                if not g_tag:
                    continue
                track_genres = _split_genres(g_tag)
                if not track_genres:
                    continue
                title = None
                t_tag = audio.get("title")
                if t_tag:
                    title = str(t_tag[0])
                track_num = None
                tn = audio.get("tracknumber")
                if tn:
                    try:
                        track_num = int(str(tn[0]).split("/")[0])
                    except Exception:
                        pass
                entry = {
                    "path":      str(f),
                    "artist":    artist_dir.name,
                    "album":     album_dir.name,
                    "title":     title or f.stem,
                    "track_num": track_num,
                }
                for genre in track_genres:
                    genres.setdefault(genre, []).append(entry)
    return genres


def _set_track_number(dest: Path, number: int) -> None:
    """Rewrite the track-number tag of an audio file to `number` (Mutagen)."""
    from mutagen import File as _MF
    try:
        audio = _MF(dest, easy=True)
        if audio is not None:
            audio["tracknumber"] = str(number)
            audio.save()
    except Exception as exc:
        logger.debug("Could not set track number on %s: %s", dest, exc)


def _sanitise_dirname(name: str) -> str:
    import re as _re
    cleaned = _re.sub(r'[<>:"/\\|?*]', "_", name).strip().strip(".")
    return cleaned or "Playlist"


def _create_genre_playlist(home_path: Path, genre: str, name: str) -> dict:
    """Copy every track tagged with `genre` into home/Playlists/<name>/, then
    rewrite each copy's track-number metadata sequentially (1..N) so the
    playlist plays in a defined order."""
    import shutil

    genres = _scan_genres(home_path)
    # Case-insensitive genre match
    matches = None
    for g, tracks in genres.items():
        if g.lower() == genre.lower():
            matches = tracks
            break
    if not matches:
        return {"error": f'No tracks found with genre "{genre}".'}

    # Order: artist → album → original track number → title
    matches = sorted(matches, key=lambda t: (
        t["artist"].lower(), t["album"].lower(),
        t["track_num"] if t["track_num"] is not None else 9999,
        t["title"].lower(),
    ))

    pl_name = _sanitise_dirname(name or genre)
    pl_dir  = home_path / PLAYLISTS_DIRNAME / pl_name
    try:
        pl_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"error": f"Could not create playlist folder: {exc}"}

    pad     = max(2, len(str(len(matches))))
    copied  = 0
    for i, t in enumerate(matches, start=1):
        src = Path(t["path"])
        if not src.exists():
            continue
        dest = pl_dir / f"{str(i).zfill(pad)} - {src.name}"
        try:
            shutil.copy2(src, dest)
            _set_track_number(dest, i)
            copied += 1
        except Exception as exc:
            logger.debug("Failed copying %s → %s: %s", src, dest, exc)

    return {
        "ok":        True,
        "name":      pl_name,
        "genre":     genre,
        "copied":    copied,
        "total":     len(matches),
        "directory": str(pl_dir),
    }


def _scan_all_tracks(home_path: Path) -> "list[dict]":
    """Flat scan of every library track with the metadata needed for smart
    playlists: {path, artist, album, title, genres, track_num, ext, mtime,
    rating_key}."""
    from mutagen import File as _MF
    out: list = []
    scan_root = home_path / ARTISTS_DIRNAME
    if not scan_root.is_dir():
        scan_root = home_path
    for artist_dir in sorted(scan_root.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        if artist_dir.name in (PLAYLISTS_DIRNAME, ARTISTS_DIRNAME):
            continue
        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir():
                continue
            # Same 16-hex album hash as /library-albums, so ratings (keyed on
            # path_hash + NUL + filename) can be looked up per scanned track.
            album_ph = hashlib.sha256(
                str(album_dir.resolve()).encode()).hexdigest()[:16]
            for f in sorted(album_dir.iterdir()):
                if not f.is_file() or f.suffix.lower() not in _GENRE_AUDIO_EXT:
                    continue
                title = None; genres = []; track_num = None
                try:
                    audio = _MF(f, easy=True)
                except Exception:
                    audio = None
                if audio:
                    t_tag = audio.get("title")
                    if t_tag:
                        title = str(t_tag[0])
                    g_tag = audio.get("genre")
                    if g_tag:
                        genres = _split_genres(g_tag)
                    tn = audio.get("tracknumber")
                    if tn:
                        try:
                            track_num = int(str(tn[0]).split("/")[0])
                        except Exception:
                            pass
                try:
                    mtime = f.stat().st_mtime
                except Exception:
                    mtime = 0
                out.append({
                    "path": str(f), "artist": artist_dir.name, "album": album_dir.name,
                    "title": title or f.stem, "genres": genres, "track_num": track_num,
                    "ext": f.suffix.lower().lstrip("."), "mtime": mtime,
                    "rating_key": _liked_key(album_ph, f.name),
                })
    return out


def _last_played_map(events: "list[dict]") -> dict:
    """Most recent play time per track from the local listening history.
    Keys: ("artist_lower", "title_lower") plus a title-only fallback key
    ("", "title_lower") — folder artist names can differ slightly from the
    tag artist recorded in the history, and for a "not played in N days"
    rule a false 'played recently' merely excludes a track (safe)."""
    out: dict = {}
    for e in events:
        try:
            t = float(e.get("t") or 0)
        except Exception:
            continue
        title  = str(e.get("title")  or "").strip().lower()
        artist = str(e.get("artist") or "").strip().lower()
        if not title:
            continue
        for key in ((artist, title), ("", title)):
            if t > out.get(key, 0):
                out[key] = t
    return out


def _create_smart_playlist(home_path: Path, name: str, opts: dict) -> dict:
    """Materialise a playlist folder from rule-based filters over the library:
    format / genre / artist substrings, "added within N days", "min star
    rating", "not played in N days" (v1.7.0), "BPM range" (v1.11.0 — needs a
    Tempo Analysis scan), sort + limit."""
    import shutil, time

    tracks = _scan_all_tracks(home_path)
    fmt    = (opts.get("format") or "").lower().lstrip(".")
    genre  = (opts.get("genre")  or "").lower().strip()
    artist = (opts.get("artist") or "").lower().strip()
    try:    added_days = int(opts.get("added_days") or 0)
    except Exception: added_days = 0
    try:    limit = int(opts.get("limit") or 0)
    except Exception: limit = 0
    try:    min_rating = int(opts.get("min_rating") or 0)
    except Exception: min_rating = 0
    try:    not_played_days = int(opts.get("not_played_days") or 0)
    except Exception: not_played_days = 0
    try:    bpm_min = float(opts.get("bpm_min") or 0)
    except Exception: bpm_min = 0.0
    try:    bpm_max = float(opts.get("bpm_max") or 0)
    except Exception: bpm_max = 0.0
    cutoff = (time.time() - added_days * 86400) if added_days > 0 else None

    ratings = _load_ratings() if min_rating > 0 else {}
    played  = _last_played_map(_load_play_events()) if not_played_days > 0 else {}
    played_cutoff = time.time() - not_played_days * 86400
    bpm_cache = _load_bpm_cache() if (bpm_min > 0 or bpm_max > 0) else {}

    def _ok(t):
        if fmt and t["ext"] != fmt:
            return False
        if genre and not any(genre in g.lower() for g in t["genres"]):
            return False
        if artist and artist not in t["artist"].lower():
            return False
        if cutoff is not None and t["mtime"] < cutoff:
            return False
        if min_rating > 0:
            r = (ratings.get(t.get("rating_key", "")) or {}).get("rating", 0)
            if r < min_rating:
                return False
        if not_played_days > 0:
            title = t["title"].strip().lower()
            last  = max(played.get((t["artist"].strip().lower(), title), 0),
                        played.get(("", title), 0))
            if last > played_cutoff:      # played too recently (never-played = 0 passes)
                return False
        if bpm_min > 0 or bpm_max > 0:
            b = (bpm_cache.get(t.get("rating_key", "")) or {}).get("bpm")
            if not b:                     # unknown / unscanned → can't match a tempo rule
                return False
            if bpm_min > 0 and b < bpm_min:
                return False
            if bpm_max > 0 and b > bpm_max:
                return False
        return True

    matches = [t for t in tracks if _ok(t)]
    if opts.get("sort") == "recent":
        matches.sort(key=lambda t: t["mtime"], reverse=True)
    else:
        matches.sort(key=lambda t: (t["artist"].lower(), t["album"].lower(),
                                    t["track_num"] if t["track_num"] is not None else 9999,
                                    t["title"].lower()))
    if limit > 0:
        matches = matches[:limit]
    if not matches:
        return {"error": "No tracks matched those rules."}

    pl_name = _sanitise_dirname(name or "Smart Playlist")
    pl_dir  = home_path / PLAYLISTS_DIRNAME / pl_name
    try:
        pl_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"error": f"Could not create playlist folder: {exc}"}

    pad = max(2, len(str(len(matches))))
    copied = 0
    for i, t in enumerate(matches, start=1):
        src = Path(t["path"])
        if not src.exists():
            continue
        dest = pl_dir / f"{str(i).zfill(pad)} - {src.name}"
        try:
            shutil.copy2(src, dest)
            _set_track_number(dest, i)
            copied += 1
        except Exception as exc:
            logger.debug("smart playlist copy failed: %s", exc)

    return {"ok": True, "name": pl_name, "copied": copied, "total": len(matches),
            "directory": str(pl_dir)}


def _resolve_track_sources(tracks: list) -> list:
    """Map a list of {path_hash, name} (from the frontend) to actual source
    Paths on disk, using the in-memory _path_hash_map. Skips anything that can't
    be resolved or that escapes its album dir (path-traversal guard)."""
    out: list = []
    for t in tracks or []:
        ph   = (t.get("path_hash") or "").strip()
        name = (t.get("name") or "").strip()
        if not ph or not name:
            continue
        base = _path_hash_map.get(ph)
        if not base:
            continue
        src = (base / name).resolve()
        try:
            src.relative_to(base.resolve())
        except ValueError:
            continue
        if src.is_file():
            out.append(src)
    return out


def _playlist_sidecar_touch(pl_dir: Path, filenames: list) -> None:
    """Record a 'date_added' = now for each filename in the playlist's
    .tgplaylist.json sidecar so the Library shows when tracks were added."""
    sc = pl_dir / ".tgplaylist.json"
    try:
        data = json.loads(sc.read_text(encoding="utf-8")) if sc.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    from datetime import datetime as _dt
    now = _dt.now().isoformat(timespec="seconds")
    for fn in filenames:
        data[fn] = {"date_added": now}
    try:
        sc.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not update playlist sidecar %s: %s", sc, exc)


def _add_tracks_to_playlist(home_path: Path, name: str, tracks: list,
                            create_new: bool) -> dict:
    """Copy the given tracks ({path_hash, name}) into home/Playlists/<name>/.
    When create_new is True a fresh playlist folder is made; otherwise tracks
    are appended after the playlist's existing audio files. Copies keep a
    'NN - ' index prefix and have their track-number tag rewritten in order."""
    import shutil

    AUDIO_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
                 ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}

    pl_name = _sanitise_dirname(name)
    pl_dir  = home_path / PLAYLISTS_DIRNAME / pl_name

    if create_new and pl_dir.exists():
        return {"error": f'A playlist named "{pl_name}" already exists.'}
    if not create_new and not pl_dir.is_dir():
        return {"error": f'Playlist "{pl_name}" not found.'}

    srcs = _resolve_track_sources(tracks)
    if not srcs:
        return {"error": "None of the selected tracks could be located on disk."}

    try:
        pl_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        return {"error": f"Could not create playlist folder: {exc}"}

    # Continue numbering after any tracks already in the playlist
    existing = [f for f in pl_dir.iterdir()
                if f.is_file() and f.suffix.lower() in AUDIO_EXT]
    start = len(existing)
    total = start + len(srcs)
    pad   = max(2, len(str(total)))

    added_names: list = []
    for i, src in enumerate(srcs, start=start + 1):
        # Strip any pre-existing "NN - " prefix from the source name first
        import re as _re
        clean = _re.sub(r"^\d{1,3}\s*-\s*", "", src.name)
        dest  = pl_dir / f"{str(i).zfill(pad)} - {clean}"
        if dest.exists():
            dest = pl_dir / f"{str(i).zfill(pad)} - {src.stem}_{i}{src.suffix}"
        try:
            shutil.copy2(src, dest)
            _set_track_number(dest, i)
            added_names.append(dest.name)
        except Exception as exc:
            logger.debug("Failed copying %s → %s: %s", src, dest, exc)

    if added_names:
        _playlist_sidecar_touch(pl_dir, added_names)
        # Drop cached cover so a brand-new playlist picks up artwork next scan
        ph = hashlib.sha256(str(pl_dir.resolve()).encode()).hexdigest()[:16]
        _local_cover_cache.pop(ph, None)

    return {
        "ok":        True,
        "name":      pl_name,
        "added":     len(added_names),
        "total":     len(srcs),
        "directory": str(pl_dir),
    }


def _remove_tracks_from_playlist(path_hash: str, names: list) -> dict:
    """Delete the named audio files from the playlist folder identified by
    path_hash (path-traversal guarded). Also clears their sidecar entries."""
    pl_dir = _path_hash_map.get((path_hash or "").strip())
    if not pl_dir or not pl_dir.is_dir():
        return {"error": "Playlist not found — try refreshing the library."}

    base    = pl_dir.resolve()
    removed = 0
    for nm in names or []:
        nm = (nm or "").strip()
        if not nm:
            continue
        target = (pl_dir / nm).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            continue
        if target.is_file():
            try:
                target.unlink()
                removed += 1
            except Exception as exc:
                logger.debug("Could not remove %s: %s", target, exc)

    # Prune sidecar entries for removed files
    if removed:
        sc = pl_dir / ".tgplaylist.json"
        try:
            if sc.exists():
                data = json.loads(sc.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for nm in names or []:
                        data.pop(nm, None)
                    sc.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception:
            pass

    return {"ok": True, "removed": removed}


def _reorder_playlist_tracks(path_hash: str, names: list) -> dict:
    """Persist a new track order for a playlist by rewriting each file's
    track-number tag (1..N) to match the given filename order. Playlist track
    listing already sorts by track number, so this fixes the order on disk."""
    pl_dir = _path_hash_map.get((path_hash or "").strip())
    if not pl_dir or not pl_dir.is_dir():
        return {"error": "Playlist not found — try refreshing the library."}

    base = pl_dir.resolve()
    ordered = 0
    for i, nm in enumerate(names or [], start=1):
        nm = (nm or "").strip()
        if not nm:
            continue
        target = (pl_dir / nm).resolve()
        try:
            target.relative_to(base)
        except ValueError:
            continue
        if target.is_file():
            _set_track_number(target, i)
            ordered += 1

    return {"ok": True, "ordered": ordered}


# ══════════════════════════════════════════════
#  LIKED SONGS  (persisted favourites)
# ══════════════════════════════════════════════

def _liked_key(path_hash: str, name: str) -> str:
    return f"{path_hash}\x00{name}"


def _load_liked() -> "list[dict]":
    """Return the persisted list of liked songs (newest first). Each entry:
    {path_hash, name, title, artist, album, cover_url, added}."""
    try:
        if LIKED_SONGS_FILE.exists():
            data = json.loads(LIKED_SONGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except Exception as exc:
        logger.debug("Could not read liked songs: %s", exc)
    return []


def _save_liked(items: "list[dict]") -> None:
    try:
        LIKED_SONGS_FILE.write_text(
            json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not write liked songs: %s", exc)


def _toggle_liked(entry: dict) -> dict:
    """Add the song if absent, remove it if present (keyed by path_hash + name).
    Returns {liked: bool, count: int}."""
    ph   = (entry.get("path_hash") or "").strip()
    name = (entry.get("name") or "").strip()
    if not ph or not name:
        return {"error": "Missing path_hash or name"}

    items = _load_liked()
    key   = _liked_key(ph, name)
    kept  = [it for it in items if _liked_key(it.get("path_hash", ""),
                                              it.get("name", "")) != key]

    if len(kept) != len(items):
        # Was present → unlike
        _save_liked(kept)
        return {"liked": False, "count": len(kept)}

    # Was absent → like (prepend so newest shows first)
    new_item = {
        "path_hash": ph,
        "name":      name,
        "title":     entry.get("title") or name,
        "artist":    entry.get("artist") or "",
        "album":     entry.get("album") or "",
        "cover_url": entry.get("cover_url") or "",
        "added":     int(time.time()),
    }
    kept.insert(0, new_item)
    _save_liked(kept)
    return {"liked": True, "count": len(kept)}


# ══════════════════════════════════════════════
#  STAR RATINGS  (v1.7.0 — same keying as liked songs, feeds smart playlists)
# ══════════════════════════════════════════════

RATINGS_FILE = DATA_DIR / "ratings.json"


def _load_ratings(path: "Path | None" = None) -> dict:
    """{key: {rating, title, artist, album, updated}} where key is the same
    path_hash + NUL + filename compound used for liked songs."""
    p = path or RATINGS_FILE
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("Could not read ratings: %s", exc)
    return {}


def _save_ratings(data: dict, path: "Path | None" = None) -> None:
    try:
        (path or RATINGS_FILE).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not write ratings: %s", exc)


def _set_rating(entry: dict, path: "Path | None" = None) -> dict:
    """Set a track's star rating (1–5); 0 clears it.
    Returns {ok, rating, count} or {error}."""
    ph   = (entry.get("path_hash") or "").strip()
    name = (entry.get("name") or "").strip()
    if not ph or not name:
        return {"error": "Missing path_hash or name"}
    try:
        rating = int(entry.get("rating") or 0)
    except Exception:
        return {"error": "rating must be an integer 0–5"}
    if not 0 <= rating <= 5:
        return {"error": "rating must be 0–5"}

    items = _load_ratings(path)
    key   = _liked_key(ph, name)
    if rating == 0:
        items.pop(key, None)
    else:
        items[key] = {
            "rating":  rating,
            "title":   entry.get("title") or name,
            "artist":  entry.get("artist") or "",
            "album":   entry.get("album") or "",
            "updated": int(time.time()),
        }
    _save_ratings(items, path)
    return {"ok": True, "rating": rating, "count": len(items)}


# ══════════════════════════════════════════════
#  ARTIST WATCHLIST / NEW-RELEASE RADAR
# ══════════════════════════════════════════════

WATCHLIST_FILE = DATA_DIR / "watchlist.json"   # {artist_id: {...}}


def _load_watchlist() -> dict:
    """Return {artist_id: {name, cover_url, added, last_checked, known_album_ids}}."""
    try:
        if WATCHLIST_FILE.exists():
            data = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("Could not read watchlist: %s", exc)
    return {}


def _save_watchlist(data: dict) -> None:
    try:
        WATCHLIST_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not write watchlist: %s", exc)


def _fetch_artist_albums(artist_id: str) -> "list[dict]":
    """Fetch an artist's albums from Deezer as a normalised list:
    [{album_id, title, cover, link, release_date, record_type}]."""
    out: list = []
    try:
        api_url = (f"https://api.deezer.com/artist/{artist_id}/albums"
                   "?limit=200&output=json")
        req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        for a in data.get("data", []):
            out.append({
                "album_id":    str(a.get("id", "")),
                "title":       a.get("title", ""),
                "cover":       a.get("cover_medium") or a.get("cover") or "",
                "link":        a.get("link") or f"https://www.deezer.com/album/{a.get('id','')}",
                "release_date": a.get("release_date", ""),
                "record_type": a.get("record_type", ""),
            })
    except Exception as exc:
        logger.warning("Deezer artist albums fetch failed for %s: %s", artist_id, exc)
    return out


def _watchlist_add(artist_id: str, name: str, cover_url: str) -> dict:
    """Add an artist; seed known_album_ids with their current discography so
    only future releases surface as 'new'."""
    artist_id = str(artist_id).strip()
    if not artist_id:
        return {"error": "Missing artist_id"}
    wl = _load_watchlist()
    albums = _fetch_artist_albums(artist_id)
    wl[artist_id] = {
        "name":            name or "",
        "cover_url":       cover_url or "",
        "added":           int(time.time()),
        "last_checked":    int(time.time()),
        "known_album_ids": [a["album_id"] for a in albums],
    }
    _save_watchlist(wl)
    return {"ok": True, "watching": True, "count": len(wl)}


def _watchlist_check() -> dict:
    """Diff each watched artist's current discography against the seeded
    baseline; return albums released since they were added."""
    wl = _load_watchlist()
    new_releases: list = []
    for artist_id, info in wl.items():
        known = set(info.get("known_album_ids") or [])
        for a in _fetch_artist_albums(artist_id):
            if a["album_id"] and a["album_id"] not in known:
                new_releases.append({"artist_id": artist_id,
                                     "artist": info.get("name", ""), **a})
        info["last_checked"] = int(time.time())
    _save_watchlist(wl)
    # Newest first by release date
    new_releases.sort(key=lambda x: x.get("release_date", ""), reverse=True)
    return {"new_releases": new_releases, "checked": len(wl)}


# ══════════════════════════════════════════════
#  SCROBBLING  (ListenBrainz / Last.fm — opt-in)
# ══════════════════════════════════════════════

def _scrobble_submit(cfg: dict, meta: dict, now_playing: bool) -> dict:
    """Forward a play to the configured scrobble service.

    meta: {title, artist, album}.  Returns {"ok": bool, ...}.
    Only called for local-file plays (never 30s Deezer previews).
    """
    if not cfg.get("scrobble_enabled"):
        return {"ok": False, "skipped": "disabled"}
    title  = (meta.get("title")  or "").strip()
    artist = (meta.get("artist") or "").strip()
    album  = (meta.get("album")  or "").strip()
    if not title or not artist:
        return {"ok": False, "skipped": "missing artist/title"}

    service = (cfg.get("scrobble_service") or "listenbrainz").lower()
    try:
        if service == "listenbrainz":
            token = (cfg.get("listenbrainz_token") or "").strip()
            if not token:
                return {"ok": False, "skipped": "no token"}
            track_meta = {"artist_name": artist, "track_name": title}
            if album:
                track_meta["release_name"] = album
            if now_playing:
                payload = {"listen_type": "playing_now",
                           "payload": [{"track_metadata": track_meta}]}
            else:
                payload = {"listen_type": "single",
                           "payload": [{"listened_at": int(time.time()),
                                        "track_metadata": track_meta}]}
            data = json.dumps(payload).encode("utf-8")
            req  = urllib.request.Request(
                "https://api.listenbrainz.org/1/submit-listens",
                data=data, method="POST",
                headers={"Authorization": f"Token {token}",
                         "Content-Type": "application/json",
                         "User-Agent": "TGDownloader/6"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return {"ok": True}

        if service == "lastfm":
            api_key = (cfg.get("lastfm_api_key") or "").strip()
            secret  = (cfg.get("lastfm_secret") or "").strip()
            sk      = (cfg.get("lastfm_session_key") or "").strip()
            if not (api_key and secret and sk):
                return {"ok": False, "skipped": "lastfm not configured"}
            method = "track.updateNowPlaying" if now_playing else "track.scrobble"
            params = {"method": method, "api_key": api_key, "sk": sk,
                      "artist": artist, "track": title}
            if album:
                params["album"] = album
            if not now_playing:
                params["timestamp"] = str(int(time.time()))
            # API signature: md5 of sorted "<k><v>" pairs + secret
            sig_base = "".join(f"{k}{params[k]}" for k in sorted(params)) + secret
            params["api_sig"] = hashlib.md5(sig_base.encode("utf-8")).hexdigest()
            params["format"]  = "json"
            data = urllib.parse.urlencode(params).encode("utf-8")
            req  = urllib.request.Request(
                "https://ws.audioscrobbler.com/2.0/",
                data=data, method="POST",
                headers={"User-Agent": "TGDownloader/6"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return {"ok": True}

        return {"ok": False, "error": f"unknown service: {service}"}
    except Exception as exc:
        logger.warning("scrobble failed: %s", exc)
        return {"ok": False, "error": str(exc)}


# ══════════════════════════════════════════════
#  WEBSOCKET HANDLER
# ══════════════════════════════════════════════

def handle_ws(conn, key: str):
    _ws_handshake(conn, key)
    MANAGER.add_client(conn)
    _ws_send(conn, json.dumps({"type": "status", "running": MANAGER.is_running()}))

    try:
        while True:
            raw = _ws_recv(conn)
            if raw is None:
                break
            try:
                data = json.loads(raw)
            except Exception:
                continue

            action = data.get("action")

            if action == "start":
                if MANAGER.is_running():
                    _ws_send(conn, json.dumps({"type": "error", "text": "Already running"}))
                    continue
                entries = data.get("entries", [])
                # Persist playlist cover + original track order for the worker
                # (best-effort — does network I/O, so guard it).
                if any(e.get("isPlaylist") for e in entries):
                    try:
                        _write_playlist_meta(data.get("home", ""), entries)
                    except Exception:
                        logger.exception("playlist meta prep failed")
                MANAGER.start(_build_backend_stdin(entries))
                MANAGER.broadcast({"type": "status", "running": True})

            elif action == "stop":
                MANAGER.stop()

            elif action == "pause":
                try: (DATA_DIR / "pause.flag").touch()
                except Exception: pass
                MANAGER.broadcast({"type": "paused"})

            elif action == "resume":
                try: (DATA_DIR / "pause.flag").unlink(missing_ok=True)
                except Exception: pass
                MANAGER.broadcast({"type": "resumed"})

            elif action == "ping":
                _ws_send(conn, json.dumps({"type": "pong"}))

    finally:
        MANAGER.remove_client(conn)
        try:
            conn.close()
        except Exception:
            pass


# ══════════════════════════════════════════════
#  DEEZER SEARCH  (proxied to avoid CORS)
# ══════════════════════════════════════════════

def _deezer_search(raw_query: str) -> dict:
    api_url = (
        "https://api.deezer.com/search/album"
        f"?q={url_quote(raw_query)}&limit=24&output=json"
    )
    req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


# Spotify access token via the official Client Credentials flow. Needs a free
# Spotify Developer app — the user pastes its Client ID + Secret into Settings
# (stored as spotify_client_id / spotify_client_secret in the main config).
# Tokens last ~1h and are cached here. This replaced the keyless web-player
# token, which Spotify locked behind a rotating TOTP signature in 2025.
_spotify_token_cache: dict = {"token": "", "exp": 0.0}

_SPOTIFY_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
               "AppleWebKit/537.36 (KHTML, like Gecko) "
               "Chrome/120.0 Safari/537.36")


def _spotify_credentials() -> "tuple[str, str] | tuple[None, None]":
    """Return (client_id, client_secret) from config, or (None, None) if unset."""
    try:
        cfg    = _tgd_import().load_config()
        cid    = (cfg.get("spotify_client_id") or "").strip()
        secret = (cfg.get("spotify_client_secret") or "").strip()
        if cid and secret:
            return cid, secret
    except Exception:
        pass
    return None, None


def _spotify_access_token() -> str:
    """Return a cached Spotify app token, fetching a fresh one via the Client
    Credentials flow when missing or within 30s of expiry. Raises if the API
    credentials aren't configured or Spotify rejects them."""
    now = time.time()
    if _spotify_token_cache["token"] and _spotify_token_cache["exp"] - 30 > now:
        return _spotify_token_cache["token"]

    cid, secret = _spotify_credentials()
    if not (cid and secret):
        raise RuntimeError("Spotify API credentials not configured")

    auth = b64encode(f"{cid}:{secret}".encode("utf-8")).decode("ascii")
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": _SPOTIFY_UA},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    tok = data.get("access_token", "")
    if not tok:
        raise RuntimeError("Spotify returned no access token")
    _spotify_token_cache["token"] = tok
    _spotify_token_cache["exp"]   = now + float(data.get("expires_in") or 3600)
    return tok


def _spotify_search(raw_query: str) -> dict:
    """Album search via the official Spotify Web API, shaped like Deezer results
    so the UI renders identically. Each result carries its real Spotify album URL
    in `link` — the download bot accepts Spotify links directly, so no Deezer
    resolution is needed. Falls back to Deezer search if Spotify is unreachable
    or its API credentials aren't configured; the returned dict flags which case
    so the UI can prompt the user appropriately."""
    unconfigured = _spotify_credentials() == (None, None)
    try:
        token = _spotify_access_token()
        # Spotify caps Client-Credentials (app-only) search at limit=10; anything
        # higher 400s with "Invalid limit".
        api_url = ("https://api.spotify.com/v1/search"
                   f"?q={url_quote(raw_query)}&type=album&limit=10")
        req = urllib.request.Request(
            api_url, headers={"Authorization": f"Bearer {token}",
                              "User-Agent": _SPOTIFY_UA})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        detail = str(exc)
        # urllib HTTPError stringifies to just "HTTP Error 400: Bad Request"; the
        # real reason (e.g. {"error":"invalid_client"}) is in the response body.
        body = getattr(exc, "read", None)
        if callable(body):
            try:
                detail = f"{detail} — {body().decode('utf-8', 'replace')[:300]}"
            except Exception:
                pass
        logger.warning("Spotify search failed (%s) — falling back to Deezer", detail)
        fb = _deezer_search(raw_query)
        fb["fellback_to_deezer"] = True       # these are Deezer links, not Spotify
        fb["spotify_unconfigured"] = unconfigured  # creds missing vs. request failed
        fb["spotify_error"] = detail
        return fb

    out = []
    for a in data.get("albums", {}).get("items", []):
        images = a.get("images") or []
        cover_big   = images[0].get("url", "")  if images else ""
        cover_small = images[-1].get("url", "") if images else cover_big
        artists = ", ".join(ar.get("name", "") for ar in a.get("artists", []) if ar.get("name"))
        out.append({
            "id":           None,
            "link":         (a.get("external_urls") or {}).get("spotify")
                            or f"https://open.spotify.com/album/{a.get('id','')}",
            "title":        a.get("name", ""),
            "artist":       {"name": artists},
            "cover_small":  cover_small,
            "cover_medium": cover_big,
            "nb_tracks":    a.get("total_tracks"),
            "_provider":    "spotify",
        })
    return {"data": out}


def _deezer_search_album_id(artist: str, album: str) -> "str | None":
    """Search Deezer for an album by artist+album name and return the album ID.
    Results are cached in _album_search_cache (and persisted to disk) so the
    search runs at most once per (artist, album) pair.  Returns None on failure."""
    key = f"{artist.lower()}|{album.lower()}"
    if key in _album_search_cache:
        cached = _album_search_cache[key]
        return cached if cached else None

    import difflib as _dl
    try:
        for query in [f"{artist} {album}", album]:
            api_url = (
                "https://api.deezer.com/search/album"
                f"?q={url_quote(query)}&limit=10&output=json"
            )
            req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            items = data.get("data", [])
            best_score, best_id, best_item = 0.0, None, None
            for item in items:
                t_score = _dl.SequenceMatcher(
                    None, item.get("title", "").lower(), album.lower()
                ).ratio()
                a_score = _dl.SequenceMatcher(
                    None, (item.get("artist") or {}).get("name", "").lower(), artist.lower()
                ).ratio()
                combined = t_score * 0.65 + a_score * 0.35
                if combined > best_score:
                    best_score = combined
                    best_id    = str(item["id"])
                    best_item  = item
            if best_score >= 0.55 and best_id and best_item:
                _album_search_cache[key] = best_id
                # Pre-populate cover and artist caches from search result to skip extra fetch
                cover = best_item.get("cover_medium") or best_item.get("cover_small") or ""
                if cover and best_id not in _cover_cache:
                    _cover_cache[best_id] = cover
                art = best_item.get("artist") or {}
                a_id = str(art.get("id", "")) if art.get("id") else ""
                if a_id and best_id not in _album_artist_id:
                    _album_artist_id[best_id] = a_id
                _save_album_id_cache()
                return best_id
        _album_search_cache[key] = ""
        _save_album_id_cache()
        return None
    except Exception as exc:
        logger.debug("Album search failed for '%s / %s': %s", artist, album, exc)
        _album_search_cache[key] = ""
        return None


def _spotify_resolve(share_url: str) -> dict:
    """Resolve a Spotify link's REAL display metadata (name + artist + cover).

    The download bot accepts Spotify links directly, so the returned `link` is
    the ORIGINAL Spotify URL — we deliberately do NOT convert it to a Deezer
    album (the old behaviour, which produced wrong/irrelevant names & artists).

    Metadata is read without any API key from two public sources:
      1. the embed page's `__NEXT_DATA__` JSON (real name + artist(s) + cover),
      2. the oEmbed endpoint (very stable name + thumbnail) as a fallback,
      3. the page's og:description as a last-resort artist guess.
    The result is shaped like a Deezer album response and tagged
    `source="spotify"` so the UI can badge it."""
    import re as _re

    kind = "album"
    if   "/track/"    in share_url: kind = "track"
    elif "/playlist/" in share_url: kind = "playlist"
    elif "/artist/"   in share_url: kind = "artist"
    elif "/album/"    in share_url: kind = "album"

    clean = _re.sub(r"[?#].*$", "", share_url.strip())
    m_id  = _re.search(r"/(track|album|playlist|artist)/([A-Za-z0-9]+)", clean)
    sp_id = m_id.group(2) if m_id else None

    browser_ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0 Safari/537.36")

    title = artist = thumb = ""
    nb_tracks = None

    # 1) Embed page → __NEXT_DATA__ blob holds the real name + artist(s) + cover.
    if sp_id:
        try:
            embed = f"https://open.spotify.com/embed/{kind}/{sp_id}"
            req   = urllib.request.Request(embed, headers={"User-Agent": browser_ua})
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", "replace")
            mj = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                            html, _re.S)
            if mj:
                ent = (json.loads(mj.group(1))
                       .get("props", {}).get("pageProps", {})
                       .get("state", {}).get("data", {}).get("entity", {}) or {})
                title  = (ent.get("title") or ent.get("name") or "").strip()
                # tracks/albums carry `artists`; playlists carry `authors`
                # (the creator); fall back to the `subtitle` line otherwise.
                names  = [a.get("name") for a in
                          ((ent.get("artists") or []) + (ent.get("authors") or []))
                          if a.get("name")]
                artist = ", ".join(names) if names else (ent.get("subtitle") or "").strip()
                srcs   = ((ent.get("coverArt") or {}).get("sources")
                          or (ent.get("visualIdentity") or {}).get("image") or [])
                if srcs:
                    thumb = srcs[-1].get("url") or srcs[0].get("url") or ""
                tl = ent.get("trackList") or []
                if tl:
                    nb_tracks = len(tl)
        except Exception as exc:
            logger.debug("Spotify embed parse failed for %s: %s", clean, exc)

    # 2) oEmbed (very stable) — fills any missing name / thumbnail.
    if not title or not thumb:
        try:
            oe  = "https://open.spotify.com/oembed?url=" + url_quote(clean)
            req = urllib.request.Request(oe, headers={"User-Agent": "TGDownloader/6"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                meta = json.loads(resp.read().decode("utf-8"))
            title = title or (meta.get("title") or "").strip()
            thumb = thumb or (meta.get("thumbnail_url") or "")
        except Exception as exc:
            logger.debug("Spotify oEmbed failed for %s: %s", clean, exc)

    # 3) og:description heuristic — last-resort artist when embed gave none.
    if not artist:
        try:
            req = urllib.request.Request(clean, headers={"User-Agent": browser_ua})
            with urllib.request.urlopen(req, timeout=12) as resp:
                page = resp.read().decode("utf-8", "replace")
            md = _re.search(r'<meta property="og:description" content="([^"]*)"', page)
            if md:
                _skip = {"song", "album", "single", "ep", "playlist", "compilation"}
                for seg in (s.strip() for s in md.group(1).split("·")):
                    low = seg.lower()
                    if (seg and low not in _skip
                            and not _re.fullmatch(r"\d{4}", seg)
                            and "song" not in low and "item" not in low):
                        artist = seg
                        break
        except Exception as exc:
            logger.debug("Spotify og scrape failed for %s: %s", clean, exc)

    if not title:
        return {"error": f"Could not read Spotify metadata from {clean}"}

    if kind == "artist":
        artist = title                      # an artist link: the name IS the artist
        nb_tracks = None                    # "top tracks" count isn't an album

    return {
        "title":         title,
        "artist":        {"name": artist or "Unknown Artist"},
        "cover_medium":  thumb,
        "cover_small":   thumb,
        "cover_big":     thumb,
        "nb_tracks":     nb_tracks,
        "link":          clean,             # ← bot downloads the Spotify link itself
        "source":        "spotify",
        "kind":          kind,
        "spotify_kind":  kind,
        "spotify_title": title,
        "spotify_thumb": thumb,
    }


def _deezer_resolve(share_url: str) -> dict:
    """Follow a link.deezer.com/s/... short URL through the full redirect chain
    and return normalised metadata regardless of whether it resolves to an
    album, track, playlist, or artist.  Spotify links are delegated to
    _spotify_resolve (oEmbed → closest Deezer album)."""
    import re as _re
    from urllib.parse import urlparse as _urlparse

    if "spotify.com" in share_url:
        return _spotify_resolve(share_url)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    current_url = share_url

    for hop in range(12):                       # follow up to 12 hops
        req    = urllib.request.Request(current_url, headers=headers)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=12) as resp:
                # Landed on a 200 — use the final URL reported by urllib
                current_url = resp.geturl() or current_url
                break
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location", "").strip()
            if not location:
                return {"error": f"HTTP {exc.code} with no Location header from {current_url}"}
            # Make relative URLs absolute
            if location.startswith("/"):
                p        = _urlparse(current_url)
                location = f"{p.scheme}://{p.netloc}{location}"
            logger.debug("Redirect hop %d: %s → %s", hop, current_url, location)
            current_url = location
        except Exception as exc:
            return {"error": f"Network error resolving share link: {exc}"}

    logger.debug("Resolved share link to: %s", current_url)

    # Album
    m = _re.search(r"deezer\.com/(?:[a-z]{2,3}/)?album/(\d+)", current_url)
    if m:
        return _deezer_album(m.group(1))

    # Track  →  return its parent album info so the artist field is populated
    m = _re.search(r"deezer\.com/(?:[a-z]{2,3}/)?track/(\d+)", current_url)
    if m:
        return _deezer_track_info(m.group(1))

    # Playlist
    m = _re.search(r"deezer\.com/(?:[a-z]{2,3}/)?playlist/(\d+)", current_url)
    if m:
        return _deezer_playlist_info(m.group(1))

    # Artist
    m = _re.search(r"deezer\.com/(?:[a-z]{2,3}/)?artist/(\d+)", current_url)
    if m:
        return _deezer_artist_info(m.group(1))

    return {"error": f"Unrecognised Deezer URL after redirect: {current_url}"}


def _deezer_album(album_id: str) -> dict:
    """Fetch album metadata by ID.
    Tries the direct public API endpoint first (works on most IPs); falls back
    to a text search to find the exact ID match as a last resort."""

    # 1. Direct endpoint — fastest and most accurate
    try:
        api_url = f"https://api.deezer.com/album/{album_id}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("id") and not data.get("error"):
            return data
        logger.debug("Direct album API returned error payload for id=%s: %s", album_id, data.get("error"))
    except urllib.error.HTTPError as exc:
        logger.debug("Direct album API HTTP %s for id=%s — trying search fallback", exc.code, album_id)
    except Exception as exc:
        logger.debug("Direct album API error for id=%s: %s", album_id, exc)

    # 2. Search fallback — scan up to 100 results for an exact ID match
    try:
        api_url = (
            "https://api.deezer.com/search/album"
            f"?q={url_quote(album_id)}&limit=100&output=json"
        )
        req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        target_id = int(album_id)
        for item in data.get("data", []):
            if item.get("id") == target_id:
                return item
        logger.debug("Album id=%s not found in search results", album_id)
    except Exception as exc:
        logger.debug("Album search fallback error for id=%s: %s", album_id, exc)

    return {"error": f"Album {album_id} not found"}


def _deezer_track_info(track_id: str) -> dict:
    """Fetch a track and return a dict shaped like an album response so the
    caller always gets { artist: {name:…}, title:…, cover_medium:…, … }."""
    try:
        api_url = f"https://api.deezer.com/track/{track_id}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            track = json.loads(resp.read().decode("utf-8"))
        if track.get("error"):
            return {"error": str(track["error"])}
        album  = track.get("album", {})
        artist = track.get("artist", {})
        return {
            "id":           album.get("id"),
            "title":        album.get("title") or track.get("title", ""),
            "artist":       artist,
            "cover_medium": album.get("cover_medium", ""),
            "cover_small":  album.get("cover_small", ""),
            "nb_tracks":    None,
        }
    except Exception as exc:
        return {"error": f"Track lookup failed: {exc}"}


def _deezer_playlist_info(playlist_id: str) -> dict:
    """Fetch a playlist and return a normalised metadata dict."""
    try:
        api_url = f"https://api.deezer.com/playlist/{playlist_id}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("error"):
            return {"error": str(data["error"])}
        creator = data.get("creator", {})
        return {
            "id":           data.get("id"),
            "title":        data.get("title", "Playlist"),
            "artist":       {"name": creator.get("name", "Playlist"), "id": creator.get("id")},
            "cover_medium": data.get("picture_medium", ""),
            "cover_small":  data.get("picture_small", ""),
            "nb_tracks":    data.get("nb_tracks"),
            "kind":         "playlist",
        }
    except Exception as exc:
        return {"error": f"Playlist lookup failed: {exc}"}


def _deezer_artist_info(artist_id: str) -> dict:
    """Fetch an artist page and return a normalised metadata dict."""
    try:
        api_url = f"https://api.deezer.com/artist/{artist_id}"
        req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("error"):
            return {"error": str(data["error"])}
        return {
            "id":           data.get("id"),
            "title":        data.get("name", ""),
            "artist":       {"name": data.get("name", ""), "id": data.get("id")},
            "cover_medium": data.get("picture_medium", ""),
            "cover_small":  data.get("picture_small", ""),
            "nb_tracks":    None,
            "kind":         "artist",
        }
    except Exception as exc:
        return {"error": f"Artist lookup failed: {exc}"}


# ══════════════════════════════════════════════
#  PLAYLIST META  (cover + ordered tracklist)
# ══════════════════════════════════════════════

def _deezer_playlist_meta(playlist_id: str) -> dict:
    """Return {cover, tracks:[{title,artist}]} for a Deezer playlist (ordered)."""
    tracks: list = []
    cover = ""
    api = f"https://api.deezer.com/playlist/{playlist_id}"
    for _hop in range(6):                       # follow tracks.next pagination
        try:
            req = urllib.request.Request(api, headers={"User-Agent": "TGDownloader/6"})
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            break
        if data.get("error"):
            break
        if not cover:
            cover = (data.get("picture_xl") or data.get("picture_big")
                     or data.get("picture_medium") or data.get("picture_small") or "")
        block = data.get("tracks", {})
        for t in (block.get("data", []) or []):
            title = (t.get("title") or "").strip()
            if title:
                date_added = ""
                ts = t.get("time_add")
                if ts:
                    try:
                        from datetime import datetime as _dt
                        date_added = _dt.fromtimestamp(int(ts)).isoformat(timespec="seconds")
                    except Exception:
                        date_added = ""
                tracks.append({"title": title,
                               "artist": (t.get("artist") or {}).get("name", ""),
                               "album":  (t.get("album") or {}).get("title", ""),
                               "date_added": date_added})
        nxt = block.get("next") or data.get("next")
        if nxt and len(tracks) < 1000:
            api = nxt
            continue
        break
    return {"cover": cover, "tracks": tracks}


def _spotify_playlist_meta(url: str) -> dict:
    """Return {cover, tracks:[{title,artist}]} for a Spotify playlist (ordered),
    parsed from the keyless embed page's __NEXT_DATA__ blob."""
    import re as _re
    clean = _re.sub(r"[?#].*$", "", url.strip())
    m = _re.search(r"/playlist/([A-Za-z0-9]+)", clean)
    if not m:
        return {}
    sp_id = m.group(1)
    browser_ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36")
    try:
        embed = f"https://open.spotify.com/embed/playlist/{sp_id}"
        req = urllib.request.Request(embed, headers={"User-Agent": browser_ua})
        with urllib.request.urlopen(req, timeout=12) as resp:
            html = resp.read().decode("utf-8", "replace")
        mj = _re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, _re.S)
        if not mj:
            return {}
        ent = (json.loads(mj.group(1))
               .get("props", {}).get("pageProps", {})
               .get("state", {}).get("data", {}).get("entity", {}) or {})
        tracks = []
        for it in (ent.get("trackList") or []):
            title = (it.get("title") or it.get("name") or "").strip()
            if title:
                tracks.append({"title": title,
                               "artist": (it.get("subtitle") or "").strip()})
        srcs  = ((ent.get("coverArt") or {}).get("sources") or [])
        cover = (srcs[-1].get("url") if srcs else "") or ""
        return {"cover": cover, "tracks": tracks}
    except Exception as exc:
        logger.debug("Spotify playlist meta failed for %s: %s", clean, exc)
        return {}


def _playlist_meta(url: str) -> dict:
    """Resolve a playlist URL to {cover, tracks}.  Best-effort → {} on failure."""
    import re as _re
    try:
        if "spotify.com" in url and "/playlist/" in url:
            return _spotify_playlist_meta(url)
        pid = None
        m = _re.search(r"deezer\.com/(?:[a-z]{2,3}/)?playlist/(\d+)", url)
        if m:
            pid = m.group(1)
        elif "link.deezer.com" in url or "deezer.page.link" in url:
            info = _deezer_resolve(url)
            if info.get("kind") == "playlist" and info.get("id"):
                pid = str(info["id"])
        if pid:
            return _deezer_playlist_meta(pid)
    except Exception as exc:
        logger.debug("playlist meta failed for %s: %s", url, exc)
    return {}


def _write_playlist_meta(home: str, entries: list) -> None:
    """At session start, persist {url: {cover, tracks}} for every playlist entry
    so the worker can restore the original cover art + track order."""
    if not home:
        return
    meta: dict = {}
    for e in entries:
        if not e.get("isPlaylist"):
            continue
        url = e.get("url", "")
        if not url:
            continue
        info = _playlist_meta(url)
        if not info.get("cover") and e.get("coverUrl"):
            info["cover"] = e["coverUrl"]
        if info.get("cover") or info.get("tracks"):
            meta[url] = info
    if not meta:
        return
    try:
        from pathlib import Path as _P
        d = _P(home) / ".tgdownloader"
        d.mkdir(parents=True, exist_ok=True)
        (d / "playlist_meta.json").write_text(
            json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    except Exception:
        logger.exception("Could not write playlist_meta.json")


# ══════════════════════════════════════════════
#  TGDownloader imports (lazy)
# ══════════════════════════════════════════════

def _tgd_import():
    if str(BUNDLE_DIR) not in sys.path:
        sys.path.insert(0, str(BUNDLE_DIR))
    import TGDownloader as _m  # type: ignore
    return _m


# ══════════════════════════════════════════════
#  LOCAL PLAY TRACKING  (append-only JSONL history)
# ══════════════════════════════════════════════
# Every completed listen (same ≥50%/4-min threshold as scrobbling, but
# independent of any scrobble service) is appended as one JSON line, so
# writes never rewrite the whole file and a torn write loses at most the
# final line.

PLAY_HISTORY_FILE = DATA_DIR / "play_history.jsonl"


def _record_play_event(meta: dict, path: "Path | None" = None) -> bool:
    """Append one play to the local listening history.  Returns False when
    the event lacks the minimum identifying metadata (title + artist)."""
    title  = str(meta.get("title")  or "").strip()[:300]
    artist = str(meta.get("artist") or "").strip()[:300]
    album  = str(meta.get("album")  or "").strip()[:300]
    if not (title and artist):
        return False
    entry = {
        "t":  time.time(),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "title": title, "artist": artist, "album": album,
    }
    with open(path or PLAY_HISTORY_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return True


def _load_play_events(path: "Path | None" = None) -> "list[dict]":
    p = path or PLAY_HISTORY_FILE
    events: "list[dict]" = []
    if not p.exists():
        return events
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                    if isinstance(ev, dict):
                        events.append(ev)
                except Exception:
                    continue        # tolerate a torn final line
    except Exception as exc:
        logger.warning("Could not read play history: %s", exc)
    return events


def _aggregate_play_stats(events: "list[dict]", now: "float | None" = None) -> dict:
    """Pure aggregation: totals, rolling windows, top tracks/artists, recent."""
    now = time.time() if now is None else now
    last7  = sum(1 for e in events if now - float(e.get("t") or 0) <= 7  * 86400)
    last30 = sum(1 for e in events if now - float(e.get("t") or 0) <= 30 * 86400)
    tracks:  "dict[tuple, int]" = {}
    artists: "dict[str, int]"   = {}
    for e in events:
        artist = e.get("artist", "")
        tracks[(artist, e.get("title", ""))] = tracks.get((artist, e.get("title", "")), 0) + 1
        artists[artist] = artists.get(artist, 0) + 1
    top_tracks = [{"artist": a, "title": t, "plays": n}
                  for (a, t), n in sorted(tracks.items(), key=lambda kv: -kv[1])[:8]]
    top_artists = [{"artist": a, "plays": n}
                   for a, n in sorted(artists.items(), key=lambda kv: -kv[1])[:8]]
    recent = [{"title": e.get("title", ""), "artist": e.get("artist", ""), "ts": e.get("ts", "")}
              for e in events[-10:]][::-1]
    return {"total": len(events), "last7": last7, "last30": last30,
            "top_tracks": top_tracks, "top_artists": top_artists, "recent": recent}


def _filter_play_events(events: "list[dict]", q: str = "",
                        limit: int = 200, offset: int = 0) -> dict:
    """Newest-first slice of the listening history, optionally filtered by a
    case-insensitive substring across title / artist / album.  Pure."""
    ql = (q or "").strip().lower()
    if ql:
        events = [e for e in events
                  if ql in str(e.get("title") or "").lower()
                  or ql in str(e.get("artist") or "").lower()
                  or ql in str(e.get("album") or "").lower()]
    newest_first = events[::-1]
    limit  = max(1, min(int(limit or 200), 1000))
    offset = max(0, int(offset or 0))
    page = [{"title": e.get("title", ""), "artist": e.get("artist", ""),
             "album": e.get("album", ""), "ts": e.get("ts", "")}
            for e in newest_first[offset:offset + limit]]
    return {"total": len(newest_first), "events": page, "offset": offset}


def _wrapped_stats(events: "list[dict]", year: int,
                   now: "float | None" = None) -> dict:
    """Year-end "Wrapped" summary from the local listening history.  Pure:
    totals, top artists/tracks/albums, per-month counts, busiest day and the
    longest daily listening streak for the given calendar year."""
    import datetime as _dt
    yr_events: "list[tuple[_dt.datetime, dict]]" = []
    years_seen: set = set()
    for e in events:
        try:
            dt = _dt.datetime.fromtimestamp(float(e.get("t") or 0))
        except Exception:
            continue
        years_seen.add(dt.year)
        if dt.year == year:
            yr_events.append((dt, e))

    tracks:  "dict[tuple, int]" = {}
    artists: "dict[str, int]"   = {}
    albums:  "dict[tuple, int]" = {}
    by_month = [0] * 12
    by_day:  "dict[str, int]"   = {}
    for dt, e in yr_events:
        artist = e.get("artist", "")
        title  = e.get("title", "")
        album  = e.get("album", "")
        tracks[(artist, title)] = tracks.get((artist, title), 0) + 1
        artists[artist] = artists.get(artist, 0) + 1
        if album:
            albums[(artist, album)] = albums.get((artist, album), 0) + 1
        by_month[dt.month - 1] += 1
        day = dt.strftime("%Y-%m-%d")
        by_day[day] = by_day.get(day, 0) + 1

    # Longest run of consecutive listening days
    streak = best_streak = 0
    prev: "_dt.date | None" = None
    for day in sorted(by_day):
        d = _dt.date.fromisoformat(day)
        streak = streak + 1 if (prev and (d - prev).days == 1) else 1
        best_streak = max(best_streak, streak)
        prev = d

    busiest = max(by_day.items(), key=lambda kv: kv[1]) if by_day else None
    return {
        "year": year,
        "years": sorted(years_seen, reverse=True),
        "total_plays":    len(yr_events),
        "unique_tracks":  len(tracks),
        "unique_artists": len({a for a in artists if a}),
        "top_artists": [{"artist": a, "plays": n}
                        for a, n in sorted(artists.items(), key=lambda kv: -kv[1])[:10]],
        "top_tracks":  [{"artist": a, "title": t, "plays": n}
                        for (a, t), n in sorted(tracks.items(), key=lambda kv: -kv[1])[:10]],
        "top_albums":  [{"artist": a, "album": al, "plays": n}
                        for (a, al), n in sorted(albums.items(), key=lambda kv: -kv[1])[:5]],
        "by_month": by_month,
        "listening_days": len(by_day),
        "busiest_day": ({"date": busiest[0], "plays": busiest[1]} if busiest else None),
        "longest_streak_days": best_streak,
        "first_play": (yr_events[0][1].get("ts", "") if yr_events else None),
    }


# ══════════════════════════════════════════════
#  BACKUP RESTORE
# ══════════════════════════════════════════════

_RESTORABLE_STATE_FILES = {"liked_songs.json", "watchlist.json",
                           "tg_sessions.json", "album_id_cache.json",
                           "ratings.json"}


def _validate_backup_zip(data: bytes) -> "tuple[dict[str, bytes], list[str]]":
    """({basename: raw_bytes}, skipped_names).  Only allowlisted basenames
    containing valid JSON are accepted, so a crafted zip can neither traverse
    paths (basenames only) nor plant executable/non-state files."""
    import io as _io
    import zipfile as _zf
    accepted: "dict[str, bytes]" = {}
    skipped:  "list[str]" = []
    with _zf.ZipFile(_io.BytesIO(data)) as zf:
        for name in zf.namelist():
            base = name.replace("\\", "/").rsplit("/", 1)[-1]
            if base not in _RESTORABLE_STATE_FILES and base != "tg_audio_config.json":
                skipped.append(name)
                continue
            raw = zf.read(name)
            try:
                json.loads(raw.decode("utf-8"))
            except Exception:
                skipped.append(name)
                continue
            accepted[base] = raw
    return accepted, skipped


# ══════════════════════════════════════════════
#  WAVEFORM PEAKS  (for the seekbar; cached like transcodes)
# ══════════════════════════════════════════════

_WAVEFORM_DIR     = DATA_DIR / "tg_waveform_cache"
_WAVEFORM_BUCKETS = 160


def _waveform_peaks(src: "Path") -> "list[float] | None":
    """~160 normalised peak values (0..1) for *src*, decoded via ffmpeg to
    8 kHz mono PCM and bucketed.  Cached on disk keyed by path+mtime+size."""
    ff = tgd_common.ffmpeg_exe()
    if not ff:
        return None
    try:
        st  = src.stat()
        key = hashlib.sha1(
            f"{src.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8")).hexdigest()
        _WAVEFORM_DIR.mkdir(parents=True, exist_ok=True)
        cache = _WAVEFORM_DIR / (key + ".json")
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        proc = subprocess.run(
            [ff, "-hide_banner", "-nostats", "-i", str(src), "-map", "0:a:0",
             "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
            capture_output=True, timeout=120)
        raw = proc.stdout
        if not raw:
            return None
        import array
        samples = array.array("h")
        samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
        if not len(samples):
            return None
        size  = max(1, len(samples) // _WAVEFORM_BUCKETS)
        peaks = []
        for i in range(_WAVEFORM_BUCKETS):
            seg = samples[i * size:(i + 1) * size]
            if not len(seg):
                break
            peaks.append(max(abs(s) for s in seg) / 32768.0)
        mx    = max(peaks) or 1.0
        peaks = [round(p / mx, 3) for p in peaks]
        cache.write_text(json.dumps(peaks), encoding="utf-8")
        return peaks
    except Exception as exc:
        logger.debug("Waveform failed for %s: %s", src, exc)
        return None


# ══════════════════════════════════════════════
#  LOUDNESS SCAN  (batch ReplayGain tagging for the existing library)
# ══════════════════════════════════════════════

def _loudness_scan(limit: int = 25) -> dict:
    """Tag up to *limit* untagged files with REPLAYGAIN_TRACK_GAIN.  Counts the
    full backlog so the UI can say how many remain."""
    if not tgd_common.ffmpeg_available():
        return {"error": "ffmpeg not found — install it to analyze loudness"}
    m    = _tgd_import()
    cfg  = m.load_config()
    home = cfg.get("home_music_folder")
    if not home:
        return {"error": "No home music folder configured"}
    home_path = Path(home)
    if not home_path.exists():
        return {"error": f"Folder not found: {home}"}

    audio_ext = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
                 ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}
    checked = missing = tagged = failed = analyzed = 0
    for p in home_path.rglob("*"):
        if not (p.is_file() and p.suffix.lower() in audio_ext):
            continue
        checked += 1
        if tgd_common.read_replaygain_gain(p) is not None:
            continue
        missing += 1
        if analyzed >= limit:
            continue                    # keep counting the backlog
        analyzed += 1
        lufs = tgd_common.analyze_loudness(p)
        gain = tgd_common.replaygain_from_lufs(lufs) if lufs is not None else None
        if gain is not None and tgd_common.write_replaygain_tag(p, gain):
            tagged += 1
        else:
            failed += 1
    return {"checked": checked, "missing": missing, "tagged": tagged,
            "failed": failed, "remaining": max(0, missing - analyzed)}


# ── Tempo (BPM) analysis via Deezer — feeds smart-playlist tempo rules ─────────
BPM_CACHE_FILE = DATA_DIR / "bpm_cache.json"


def _load_bpm_cache() -> dict:
    """{rating_key: {bpm: float|None, gain: float|None, ts: int}} keyed by the
    same path_hash + NUL + filename compound as ratings, so a scanned track's
    tempo can be looked up directly in _create_smart_playlist. A stored key —
    even with bpm=None — means 'already looked up', so re-runs skip it."""
    try:
        if BPM_CACHE_FILE.exists():
            data = json.loads(BPM_CACHE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.debug("Could not read BPM cache: %s", exc)
    return {}


def _save_bpm_cache(data: dict) -> None:
    try:
        BPM_CACHE_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.debug("Could not write BPM cache: %s", exc)


def _deezer_bpm_lookup(artist: str, title: str) -> dict:
    """Best-effort tempo for a local track: find the closest Deezer track, then
    read its bpm + gain from the full track object (search results omit them).
    Returns {} when offline / unmatched. Deezer reports bpm=0 for many tracks,
    which we normalise to None (unknown)."""
    def _search(q: str) -> "int | None":
        url = ("https://api.deezer.com/search/track?q="
               + url_quote(q) + "&limit=1&output=json")
        req = urllib.request.Request(url, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("data") or []
        return items[0].get("id") if items else None
    try:
        tid = None
        if artist and title:
            tid = _search(f'artist:"{artist}" track:"{title}"')
        if not tid:                       # looser fallback query
            tid = _search(f"{artist} {title}".strip())
        if not tid:
            return {}
        turl = f"https://api.deezer.com/track/{tid}?output=json"
        req = urllib.request.Request(turl, headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            tr = json.loads(resp.read().decode("utf-8"))
        bpm  = tr.get("bpm")
        gain = tr.get("gain")
        return {"bpm":  float(bpm) if bpm else None,
                "gain": float(gain) if gain is not None else None}
    except Exception as exc:
        logger.debug("BPM lookup failed for %s — %s: %s", artist, title, exc)
        return {}


def _bpm_scan(limit: int = 40) -> dict:
    """Look up Deezer tempo for up to *limit* not-yet-checked library tracks and
    cache each result. Counts the backlog so the UI can say how many remain."""
    m    = _tgd_import()
    cfg  = m.load_config()
    home = cfg.get("home_music_folder")
    if not home:
        return {"error": "No home music folder configured"}
    home_path = Path(home)
    if not home_path.exists():
        return {"error": f"Folder not found: {home}"}

    tracks = _scan_all_tracks(home_path)
    cache  = _load_bpm_cache()
    todo   = [t for t in tracks if t["rating_key"] not in cache]
    processed = matched = 0
    for t in todo:
        if processed >= limit:
            break
        processed += 1
        info = _deezer_bpm_lookup(t["artist"], t["title"])
        cache[t["rating_key"]] = {"bpm":  info.get("bpm"),
                                  "gain": info.get("gain"),
                                  "ts":   int(time.time())}
        if info.get("bpm"):
            matched += 1
    _save_bpm_cache(cache)
    with_bpm = sum(1 for v in cache.values() if v.get("bpm"))
    return {"total": len(tracks), "processed": processed, "matched": matched,
            "with_bpm": with_bpm, "remaining": max(0, len(todo) - processed)}


# ══════════════════════════════════════════════
#  LIBRARY INTELLIGENCE  (v1.6.0)
# ══════════════════════════════════════════════

_AUDIO_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
              ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}

# Rough per-format quality rank for duplicate keep-best (higher = keep).
_FORMAT_RANK = {".flac": 100, ".wav": 95, ".aif": 94, ".aiff": 94, ".ape": 93,
                ".wv": 92, ".m4a": 60, ".aac": 55, ".ogg": 50, ".opus": 50,
                ".mp3": 40, ".wma": 30}


def _iter_library_audio(home_path: "Path"):
    for p in home_path.rglob("*"):
        if p.is_file() and p.suffix.lower() in _AUDIO_EXT:
            yield p


def _quality_score(path: "Path") -> "tuple[int, int]":
    """(format_rank, bitrate_or_size) — bigger is better, for keep-best."""
    rank = _FORMAT_RANK.get(path.suffix.lower(), 0)
    bitrate = 0
    try:
        from mutagen import File as _MF
        a = _MF(str(path))
        if a is not None and getattr(a, "info", None) is not None:
            bitrate = int(getattr(a.info, "bitrate", 0) or 0)
    except Exception:
        pass
    if not bitrate:
        try:
            bitrate = path.stat().st_size
        except OSError:
            bitrate = 0
    return rank, bitrate


_YEAR_LOOKUP_CAP = 25    # Deezer year lookups per janitor run (keeps runs bounded)


def _deezer_album_year(artist: str, album: str) -> "str | None":
    """Release year ("YYYY") of the best Deezer match for artist+album, or
    None when unmatched / offline."""
    album_id = _deezer_search_album_id(artist, album)
    if not album_id:
        return None
    try:
        req = urllib.request.Request(
            f"https://api.deezer.com/album/{album_id}",
            headers={"User-Agent": "TGDownloader/6"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        rd = str(data.get("release_date") or "")
        return rd[:4] if len(rd) >= 4 and rd[:4].isdigit() else None
    except Exception as exc:
        logger.debug("Deezer year lookup failed for %s — %s: %s", artist, album, exc)
        return None


def _tag_janitor(apply: bool = False, limit: int = 500,
                 fill_year: bool = False) -> dict:
    """Scan the library for fixable tag issues (feat. formatting, genre
    canonicalisation, missing albumartist; with fill_year, missing release
    years via Deezer).  Dry-run by default: returns the proposed changes;
    with apply=True, writes them."""
    m    = _tgd_import()
    cfg  = m.load_config()
    home = cfg.get("home_music_folder")
    if not home:
        return {"error": "No home music folder configured"}
    home_path = Path(home)
    if not home_path.exists():
        return {"error": f"Folder not found: {home}"}

    from mutagen import File as _MF
    changes: "list[dict]" = []
    scanned = 0
    # Year fill: one Deezer lookup per album dir, capped per run.
    year_cache: "dict[Path, str | None]" = {}
    year_lookups = 0
    for p in _iter_library_audio(home_path):
        if len(changes) >= limit:
            break
        scanned += 1
        try:
            audio = _MF(str(p), easy=True)
            if audio is None:
                continue
        except Exception:
            continue
        fixes = {}
        title = (audio.get("title") or [""])[0]
        if title:
            nt = tgd_common.normalise_featuring(title)
            if nt != title:
                fixes["title"] = nt
        genre = (audio.get("genre") or [""])[0]
        if genre:
            cg = tgd_common.canonical_genre(genre)
            if cg != genre:
                fixes["genre"] = cg
        # albumartist for compilations: fill from the parent artist folder when absent
        if not (audio.get("albumartist") or [""])[0]:
            try:
                artist_folder = p.relative_to(home_path / ARTISTS_DIRNAME).parts[0]
                if artist_folder:
                    fixes["albumartist"] = artist_folder
            except (ValueError, IndexError):
                pass
        # Release year from Deezer for files with no date tag (v1.7.0, opt-in)
        if fill_year and not (audio.get("date") or [""])[0]:
            album_dir = p.parent
            if album_dir not in year_cache and year_lookups < _YEAR_LOOKUP_CAP:
                year_lookups += 1
                try:
                    artist_folder = p.relative_to(home_path / ARTISTS_DIRNAME).parts[0]
                except (ValueError, IndexError):
                    artist_folder = ""
                year_cache[album_dir] = (
                    _deezer_album_year(artist_folder, album_dir.name)
                    if artist_folder else None)
            yr = year_cache.get(album_dir)
            if yr:
                fixes["date"] = yr
        if not fixes:
            continue
        rel = str(p.relative_to(home_path))
        if apply:
            try:
                for k, v in fixes.items():
                    audio[k] = [v]
                audio.save()
            except Exception as exc:
                changes.append({"file": rel, "error": str(exc)})
                continue
        changes.append({"file": rel, "fixes": fixes})
    return {"scanned": scanned, "changes": changes,
            "applied": apply, "count": len([c for c in changes if "fixes" in c]),
            "year_filled": len([c for c in changes
                                if "date" in (c.get("fixes") or {})]),
            "year_lookups_capped": fill_year and year_lookups >= _YEAR_LOOKUP_CAP}


def _corruption_scan(limit: int = 400) -> dict:
    """ffmpeg decode-test each file (stronger than a tag-open check): a file
    that fails to decode is genuinely damaged.  Returns the bad files."""
    ff = tgd_common.ffmpeg_exe()
    if not ff:
        return {"error": "ffmpeg not found — install it to decode-test files"}
    m    = _tgd_import()
    cfg  = m.load_config()
    home = cfg.get("home_music_folder")
    if not home:
        return {"error": "No home music folder configured"}
    home_path = Path(home)
    if not home_path.exists():
        return {"error": f"Folder not found: {home}"}

    bad: "list[str]" = []
    scanned = 0
    for p in _iter_library_audio(home_path):
        if scanned >= limit:
            break
        scanned += 1
        try:
            proc = subprocess.run(
                [ff, "-v", "error", "-xerror", "-i", str(p),
                 "-f", "null", "NUL" if sys.platform == "win32" else "/dev/null"],
                capture_output=True, timeout=120)
            if proc.returncode != 0 or proc.stderr.strip():
                bad.append(str(p.relative_to(home_path)))
        except Exception:
            bad.append(str(p.relative_to(home_path)))
    return {"scanned": scanned, "corrupt": bad, "count": len(bad)}


def _album_completeness(limit: int = 60) -> dict:
    """For each album folder, compare the local track count against Deezer's
    tracklist for the best-matching album.  Best-effort: albums we can't match
    on Deezer are reported as 'unknown' rather than incomplete."""
    m    = _tgd_import()
    cfg  = m.load_config()
    home = cfg.get("home_music_folder")
    if not home:
        return {"error": "No home music folder configured"}
    home_path = Path(home)
    artists_dir = home_path / ARTISTS_DIRNAME
    if not artists_dir.is_dir():
        return {"albums": [], "checked": 0}

    results: "list[dict]" = []
    checked = 0
    for artist_dir in sorted(artists_dir.iterdir()):
        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
            continue
        for album_dir in sorted(artist_dir.iterdir()):
            if not album_dir.is_dir():
                continue
            local = sum(1 for f in album_dir.iterdir()
                        if f.is_file() and f.suffix.lower() in _AUDIO_EXT)
            if not local:
                continue
            if checked >= limit:
                break
            checked += 1
            try:
                hits = (_deezer_search(f"{artist_dir.name} {album_dir.name}").get("data") or [])
                expected = int(hits[0].get("nb_tracks") or 0) if hits else 0
            except Exception:
                expected = 0
            if expected and local < expected:
                results.append({"artist": artist_dir.name, "album": album_dir.name,
                                "have": local, "total": expected})
            time.sleep(0.15)
    results.sort(key=lambda r: r["total"] - r["have"], reverse=True)
    return {"albums": results, "checked": checked, "incomplete": len(results)}


def _fingerprint_scan(limit: int = 50) -> dict:
    """Opt-in AcoustID fingerprinting for untagged files (needs Chromaprint's
    fpcalc binary AND an acoustid_api_key in config).  Fills missing
    artist/title/album from the AcoustID/MusicBrainz match."""
    fp = tgd_common.fpcalc_exe()
    if not fp:
        return {"error": "fpcalc (Chromaprint) not found — install it to enable fingerprinting"}
    m    = _tgd_import()
    cfg  = m.load_config()
    api_key = (cfg.get("acoustid_api_key") or "").strip()
    if not api_key:
        return {"error": "Set acoustid_api_key in config to enable fingerprinting"}
    home = cfg.get("home_music_folder")
    if not home:
        return {"error": "No home music folder configured"}
    home_path = Path(home)

    from mutagen import File as _MF
    identified = failed = scanned = 0
    updates: "list[dict]" = []
    for p in _iter_library_audio(home_path):
        if scanned >= limit:
            break
        try:
            audio = _MF(str(p), easy=True)
            if audio is None:
                continue
            has_meta = (audio.get("artist") or [""])[0] and (audio.get("title") or [""])[0]
            if has_meta:
                continue                        # only fill genuinely untagged files
        except Exception:
            continue
        scanned += 1
        try:
            proc = subprocess.run([fp, "-json", str(p)], capture_output=True,
                                  timeout=60, text=True)
            fpdata = json.loads(proc.stdout or "{}")
            dur = int(float(fpdata.get("duration") or 0))
            fingerprint = fpdata.get("fingerprint")
            if not (dur and fingerprint):
                failed += 1
                continue
            url = ("https://api.acoustid.org/v2/lookup?client=" + url_quote(api_key)
                   + "&meta=recordings&duration=" + str(dur)
                   + "&fingerprint=" + url_quote(fingerprint))
            with urllib.request.urlopen(url, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            best = (data.get("results") or [{}])[0]
            rec  = (best.get("recordings") or [{}])[0]
            title  = rec.get("title", "")
            artist = ((rec.get("artists") or [{}])[0]).get("name", "")
            if title and artist:
                audio["title"]  = [title]
                audio["artist"] = [artist]
                audio.save()
                identified += 1
                updates.append({"file": str(p.relative_to(home_path)),
                                "artist": artist, "title": title})
            else:
                failed += 1
        except Exception as exc:
            logger.debug("Fingerprint failed for %s: %s", p, exc)
            failed += 1
        time.sleep(0.4)
    return {"scanned": scanned, "identified": identified, "failed": failed,
            "updates": updates}


# ══════════════════════════════════════════════
#  COVER ART REPAIR
# ══════════════════════════════════════════════

_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _dir_has_cover(d: "Path") -> bool:
    try:
        return any(f.is_file() and f.suffix.lower() in _IMAGE_EXTS
                   for f in d.iterdir())
    except Exception:
        return False


def _art_repair(limit: int = 25) -> dict:
    """Find album/playlist folders without any cover image and fetch one from
    Deezer.  Capped per run (default 25) to stay polite to the public API;
    the response reports how many folders remain for a follow-up run."""
    m    = _tgd_import()
    cfg  = m.load_config()
    home = cfg.get("home_music_folder")
    if not home:
        return {"error": "No home music folder configured"}
    home_path = Path(home)
    if not home_path.exists():
        return {"error": f"Folder not found: {home}"}

    audio_ext = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
                 ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}

    def _has_audio(d: Path) -> bool:
        try:
            return any(f.is_file() and f.suffix.lower() in audio_ext
                       for f in d.iterdir())
        except Exception:
            return False

    targets: "list[tuple[Path, str]]" = []      # (folder, deezer search query)
    checked = 0
    artists_dir = home_path / ARTISTS_DIRNAME
    if artists_dir.is_dir():
        for artist_dir in sorted(artists_dir.iterdir()):
            if not artist_dir.is_dir() or artist_dir.name.startswith("."):
                continue
            for album_dir in sorted(artist_dir.iterdir()):
                if not album_dir.is_dir() or not _has_audio(album_dir):
                    continue
                checked += 1
                if not _dir_has_cover(album_dir):
                    targets.append((album_dir, f"{artist_dir.name} {album_dir.name}"))
    playlists_dir = home_path / PLAYLISTS_DIRNAME
    if playlists_dir.is_dir():
        for pl_dir in sorted(playlists_dir.iterdir()):
            if not pl_dir.is_dir() or pl_dir.name.startswith(".") or not _has_audio(pl_dir):
                continue
            checked += 1
            if not _dir_has_cover(pl_dir):
                targets.append((pl_dir, pl_dir.name))

    fixed = failed = 0
    for d, query in targets[:limit]:
        try:
            hits = (_deezer_search(query).get("data") or [])
            url = ""
            if hits:
                url = hits[0].get("cover_xl") or hits[0].get("cover_big") or ""
            if not url:
                failed += 1
                continue
            req = urllib.request.Request(
                url, headers={"User-Agent": f"TGDownloader/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                img = resp.read()
            if img:
                (d / "cover.jpg").write_bytes(img)
                fixed += 1
                logger.info("Art repair: saved cover for %s", d)
            else:
                failed += 1
        except Exception as exc:
            logger.debug("Art repair failed for %s: %s", d, exc)
            failed += 1
        time.sleep(0.25)                        # be polite to the Deezer API

    attempted = min(len(targets), limit)
    return {"checked": checked, "missing": len(targets), "fixed": fixed,
            "failed": failed, "remaining": max(0, len(targets) - attempted)}


# ══════════════════════════════════════════════
#  HTTP HANDLER
# ══════════════════════════════════════════════

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send_json(self, code: int, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _reject_foreign(self) -> bool:
        """CSRF / DNS-rebinding guard — see _is_local_request. True = rejected."""
        if _is_local_request(self.headers.get("Host"), self.headers.get("Origin")):
            return False
        logger.warning(
            "Rejected non-local request: host=%r origin=%r path=%s",
            self.headers.get("Host"), self.headers.get("Origin"), self.path,
        )
        self.send_error(403, "Forbidden: non-local request")
        return True

    # ── GET ────────────────────────────────────────────────────────────────

    def do_GET(self):
        if self._reject_foreign():
            return
        path = urlparse(self.path).path

        # WebSocket upgrade (covered by the origin guard above — browsers
        # always send Origin on WebSocket handshakes)
        if self.headers.get("Upgrade", "").lower() == "websocket":
            key = self.headers.get("Sec-WebSocket-Key", "")
            handle_ws(self.connection, key)
            return

        # Static assets (split out of gui.html in 1.4.0): /static/app.css,
        # /static/js/*.js. Allowlisted extensions + containment check.
        if path.startswith("/static/"):
            base   = (BUNDLE_DIR / "static").resolve()
            target = (BUNDLE_DIR / path.lstrip("/")).resolve()
            try:
                target.relative_to(base)
            except ValueError:
                self.send_error(403)
                return
            mime = {".css": "text/css; charset=utf-8",
                    ".js":  "application/javascript; charset=utf-8",
                    ".png": "image/png",
                    ".svg": "image/svg+xml",
                    ".ico": "image/x-icon",
                    ".webmanifest": "application/manifest+json"}.get(target.suffix.lower())
            if mime is None or not target.is_file():
                self.send_error(404)
                return
            content = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(content)
            return

        # PWA manifest + service worker (v1.10.0). The SW must be served from
        # the root so its scope covers the whole app.
        if path in ("/manifest.webmanifest", "/sw.js"):
            fname = "manifest.webmanifest" if path.endswith("webmanifest") else "sw.js"
            f = BUNDLE_DIR / "static" / fname
            if not f.is_file():
                self.send_error(404)
                return
            content = f.read_bytes()
            ctype = ("application/manifest+json; charset=utf-8"
                     if fname.endswith("webmanifest")
                     else "application/javascript; charset=utf-8")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-cache")
            if fname == "sw.js":
                self.send_header("Service-Worker-Allowed", "/")
            self.end_headers()
            self.wfile.write(content)
            return

        if path in ("/", "/index.html", "/gui.html"):
            # Serve setup wizard if credentials not yet configured
            if not _credentials_configured():
                html_path = SETUP_WIZARD_HTML if SETUP_WIZARD_HTML.exists() else GUI_HTML
            else:
                html_path = GUI_HTML
            try:
                content = html_path.read_bytes()
            except FileNotFoundError:
                self.send_error(404, f"{html_path.name} not found")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(content))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == "/setup-status":
            self._send_json(200, {"configured": _credentials_configured()})
            return

        if path == "/check-update":
            force = "force=1" in (urlparse(self.path).query or "")
            self._send_json(200, _check_for_update(force=force))
            return

        if path == "/version":
            self._send_json(200, {"version": APP_VERSION, "repo": GITHUB_REPO})
            return

        if path == "/config":
            m = _tgd_import()
            self._send_json(200, m.load_config())
            return

        if path == "/history":
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(200, [])
                    return
                from pathlib import Path as _P
                home_path = _P(home)
                manifest  = m.load_manifest(home_path)
                UNKNOWN_ALBUM = "_Unknown Album"

                def _get_albums(artist: str, albums_from_manifest: list) -> list:
                    if albums_from_manifest:
                        return albums_from_manifest
                    artist_dir = home_path / ARTISTS_DIRNAME / m._sanitise_path(artist)
                    if not artist_dir.exists():
                        return []
                    return [
                        d.name for d in sorted(artist_dir.iterdir())
                        if d.is_dir() and d.name != UNKNOWN_ALBUM and not d.name.startswith(".")
                    ]

                items = []
                for url, info in manifest.items():
                    artist        = info.get("artist", "")
                    manifest_albs = info.get("albums", [])
                    is_pl         = bool(info.get("is_playlist"))
                    items.append({
                        "url":       url,
                        "artist":    artist,
                        "files":     info.get("files", []),
                        # Playlists already carry their display name in `albums`;
                        # don't fall back to a disk scan of the creator's folder.
                        "albums":    manifest_albs if is_pl else _get_albums(artist, manifest_albs),
                        "timestamp": info.get("timestamp", ""),
                        "status":    info.get("status", ""),
                        "is_playlist": is_pl,
                        "playlist":    info.get("playlist", ""),
                    })
                items.sort(key=lambda x: x["timestamp"], reverse=True)
                self._send_json(200, items[:50])
            except Exception as exc:
                logger.exception("Error in /history")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/resolve":
            qs     = urlparse(self.path).query
            params: dict[str, str] = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            share_url = params.get("url", "").strip()
            if not share_url:
                self._send_json(400, {"error": "Missing url parameter"})
                return
            try:
                self._send_json(200, _deezer_resolve(share_url))
            except Exception as exc:
                logger.warning("Deezer resolve failed: %s", exc)
                self._send_json(500, {"error": str(exc)})
            return

        # ── Resolve a Deezer album by artist + album name (for per-track links) ──
        if path == "/find-album":
            qs     = urlparse(self.path).query
            params: dict[str, str] = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            album  = params.get("album", "").strip()
            artist = params.get("artist", "").strip()
            if not album:
                self._send_json(400, {"error": "Missing album"})
                return
            try:
                aid = _deezer_search_album_id(artist, album)
                if not aid:
                    self._send_json(404, {"error": "Album not found"})
                    return
                data = _deezer_album(aid)
                if data.get("error"):
                    self._send_json(404, {"error": data["error"]})
                    return
                self._send_json(200, {
                    "id":        aid,
                    "title":     data.get("title", album),
                    "artist":    (data.get("artist") or {}).get("name", artist),
                    "cover":     data.get("cover_medium") or data.get("cover_small") or "",
                    "nb_tracks": data.get("nb_tracks"),
                })
            except Exception as exc:
                logger.warning("find-album failed: %s", exc)
                self._send_json(500, {"error": str(exc)})
            return

        if path.startswith("/album/") and path.count("/") == 2:
            album_id = path.split("/")[2]
            if album_id.isdigit():
                try:
                    self._send_json(200, _deezer_album(album_id))
                except Exception as exc:
                    logger.warning("Deezer album fetch failed: %s", exc)
                    self._send_json(500, {"error": str(exc)})
            else:
                self._send_json(400, {"error": "Invalid album id"})
            return

        if path.startswith("/track/") and path.count("/") == 2:
            track_id = path.split("/")[2]
            if track_id.isdigit():
                try:
                    self._send_json(200, _deezer_track_info(track_id))
                except Exception as exc:
                    logger.warning("Deezer track fetch failed: %s", exc)
                    self._send_json(500, {"error": str(exc)})
            else:
                self._send_json(400, {"error": "Invalid track id"})
            return

        if path == "/artist-search":
            qs     = urlparse(self.path).query
            params: dict[str, str] = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            query = params.get("q", "").strip()
            if not query:
                self._send_json(400, {"error": "Missing query parameter 'q'"})
                return
            try:
                api_url = (
                    "https://api.deezer.com/search/artist"
                    f"?q={url_quote(query)}&limit=20&output=json"
                )
                req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._send_json(200, data)
            except Exception as exc:
                logger.warning("Deezer artist search failed: %s", exc)
                self._send_json(500, {"error": str(exc)})
            return

        if path.startswith("/artist-albums/") and path.count("/") == 2:
            artist_id = path.split("/")[2]
            if artist_id.isdigit():
                try:
                    api_url = (
                        f"https://api.deezer.com/artist/{artist_id}/albums"
                        "?limit=100&output=json"
                    )
                    req = urllib.request.Request(api_url, headers={"User-Agent": "TGDownloader/6"})
                    with urllib.request.urlopen(req, timeout=12) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    self._send_json(200, data)
                except Exception as exc:
                    logger.warning("Deezer artist albums failed for id=%s: %s", artist_id, exc)
                    self._send_json(500, {"error": str(exc)})
            else:
                self._send_json(400, {"error": "Invalid artist id"})
            return

        if path == "/watchlist":
            wl = _load_watchlist()
            items = [
                {"artist_id": aid, **{k: v for k, v in info.items()
                                      if k != "known_album_ids"},
                 "known_count": len(info.get("known_album_ids") or [])}
                for aid, info in wl.items()
            ]
            items.sort(key=lambda x: x.get("added", 0), reverse=True)
            self._send_json(200, {"artists": items})
            return

        if path == "/watchlist-check":
            try:
                self._send_json(200, _watchlist_check())
            except Exception as exc:
                logger.exception("watchlist-check failed")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/duplicates":
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(200, {"error": "No home music folder configured."})
                    return
                from pathlib import Path as _P
                home_path = _P(home)
                groups_raw = m.build_duplicate_groups(home_path)
                groups = []
                for paths in groups_raw:
                    members = []
                    for p in paths:
                        pp = _P(p)
                        try:
                            sz = pp.stat().st_size
                        except OSError:
                            sz = 0
                        rank, bitrate = _quality_score(pp)
                        members.append({
                            "path":     p,
                            "rel":      str(pp.relative_to(home_path)) if str(pp).startswith(str(home_path)) else pp.name,
                            "name":     pp.name,
                            "size":     sz,
                            "ext":      pp.suffix.lower().lstrip("."),
                            "quality":  rank * 10_000_000 + bitrate,
                        })
                    # Best copy first so the UI can mark it "keep".
                    members.sort(key=lambda x: x["quality"], reverse=True)
                    if members:
                        members[0]["best"] = True
                    groups.append({"size": members[0]["size"], "files": members})
                groups.sort(key=lambda g: g["size"] * (len(g["files"]) - 1), reverse=True)
                wasted = sum(g["size"] * (len(g["files"]) - 1) for g in groups)
                self._send_json(200, {"groups": groups, "wasted_bytes": wasted})
            except Exception as exc:
                logger.exception("Error in /duplicates")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/health":
            # Lightweight, read-only environment check for the diagnostics panel.
            # Each item: {ok: bool, label, detail}. Never raises — every probe is
            # individually guarded so one failure can't blank the whole report.
            checks: list = []

            def _add(ok, label, detail=""):
                checks.append({"ok": bool(ok), "label": label, "detail": str(detail)})

            # ffmpeg (needed for in-app playback of undecodable files)
            try:
                ff = _ffmpeg_exe()
                _add(bool(ff), "ffmpeg",
                     ff if ff else "Not found — hi-res/exotic tracks can't play in-app")
            except Exception as exc:
                _add(False, "ffmpeg", str(exc))

            # Telegram session (required before any download works)
            try:
                _has_sess = tgd_common.has_session()
                _add(_has_sess, "Telegram session",
                     "Connected" if _has_sess else "Not connected — use the TG button")
            except Exception as exc:
                _add(False, "Telegram session", str(exc))

            # Home music folder: set / exists / writable
            home_path = None
            try:
                cfg  = _tgd_import().load_config()
                home = cfg.get("home_music_folder") or ""
                if not home:
                    _add(False, "Home folder", "Not set")
                else:
                    from pathlib import Path as _P
                    home_path = _P(home)
                    if not home_path.exists():
                        _add(False, "Home folder", f"Missing: {home}")
                    else:
                        writable = os.access(home_path, os.W_OK)
                        _add(writable, "Home folder",
                             home if writable else f"Not writable: {home}")
            except Exception as exc:
                _add(False, "Home folder", str(exc))

            # Free disk space on the home folder's drive
            try:
                import shutil as _sh
                target = home_path if (home_path and home_path.exists()) else DATA_DIR
                usage  = _sh.disk_usage(str(target))
                gb_free = usage.free / 1_073_741_824
                _add(gb_free >= 1.0, "Free disk space",
                     f"{gb_free:.1f} GB free" + ("" if gb_free >= 1.0 else " — running low"))
            except Exception as exc:
                _add(False, "Free disk space", str(exc))

            ok_all = all(c["ok"] for c in checks)
            self._send_json(200, {"ok": ok_all, "checks": checks})
            return

        if path == "/library-scan":
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(200, {"error": "No home music folder configured."})
                    return
                from pathlib import Path as _P
                home_path    = _P(home)
                artists_root = home_path / ARTISTS_DIRNAME
                AUDIO_EXT = {".mp3",".flac",".ogg",".opus",".m4a",".aac",
                             ".wav",".aif",".aiff",".wma",".ape",".wv"}
                corrupt:    list = []
                no_cover:   list = []
                scanned_files = scanned_albums = 0

                if artists_root.is_dir():
                    for artist_dir in artists_root.iterdir():
                        if not artist_dir.is_dir() or artist_dir.name.startswith("."):
                            continue
                        for album_dir in artist_dir.iterdir():
                            if not album_dir.is_dir():
                                continue
                            audio = [f for f in album_dir.iterdir()
                                     if f.is_file() and f.suffix.lower() in AUDIO_EXT]
                            if not audio:
                                continue
                            scanned_albums += 1
                            # Corrupt / unreadable tracks
                            for f in audio:
                                scanned_files += 1
                                try:
                                    from mutagen import File as _MF
                                    if _MF(f) is None:
                                        corrupt.append(str(f.relative_to(home_path)))
                                except Exception:
                                    corrupt.append(str(f.relative_to(home_path)))
                            # Missing cover art
                            if _extract_cover_bytes(album_dir) is None:
                                no_cover.append(str(album_dir.relative_to(home_path)))

                self._send_json(200, {
                    "scanned_files":  scanned_files,
                    "scanned_albums": scanned_albums,
                    "corrupt":        corrupt,
                    "missing_cover":  no_cover,
                })
            except Exception as exc:
                logger.exception("Error in /library-scan")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/export-m3u":
            params = {}
            for part in urlparse(self.path).query.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            ph = params.get("path_hash", "")
            album_dir = _path_hash_map.get(ph)
            if not album_dir or not album_dir.is_dir():
                self.send_error(404); return
            AUDIO_EXT = {".mp3",".flac",".ogg",".opus",".m4a",".aac",
                         ".wav",".aif",".aiff",".wma",".ape",".wv"}
            audio = sorted(f for f in album_dir.iterdir()
                           if f.is_file() and f.suffix.lower() in AUDIO_EXT)
            lines = ["#EXTM3U"]
            for f in audio:
                title, dur = f.stem, -1
                try:
                    from mutagen import File as _MF
                    mf = _MF(f, easy=True)
                    if mf is not None:
                        if mf.get("title"):
                            title = mf["title"][0]
                        if getattr(mf, "info", None) and getattr(mf.info, "length", None):
                            dur = int(mf.info.length)
                except Exception:
                    pass
                lines.append(f"#EXTINF:{dur},{title}")
                lines.append(str(f.resolve()))
            data = ("\n".join(lines) + "\n").encode("utf-8")
            fname = (params.get("name") or album_dir.name or "playlist").replace('"', "")
            self.send_response(200)
            self.send_header("Content-Type", "audio/x-mpegurl")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="{fname}.m3u"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/backup":
            import io, zipfile
            buf = io.BytesIO()
            state_files = ["liked_songs.json", "watchlist.json",
                           "tg_sessions.json", "album_id_cache.json",
                           "ratings.json"]
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for name in state_files:
                    fp = DATA_DIR / name
                    if fp.exists():
                        try:
                            zf.writestr(name, fp.read_bytes())
                        except Exception:
                            pass
                # Drop credentials from the archived config copy
                try:
                    cfg = json.loads((DATA_DIR / "tg_audio_config.json").read_text("utf-8"))
                    for secret in ("api_id", "api_hash", "listenbrainz_token",
                                   "lastfm_api_key", "lastfm_secret", "lastfm_session_key",
                                   "spotify_client_id", "spotify_client_secret"):
                        cfg.pop(secret, None)
                    zf.writestr("tg_audio_config.json", json.dumps(cfg, indent=2))
                except Exception:
                    pass
            data = buf.getvalue()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", f'attachment; filename="tgdownloader-backup-{stamp}.zip"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/search":
            qs     = urlparse(self.path).query
            params: dict[str, str] = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            query = params.get("q", "").strip()
            provider = (params.get("provider", "") or "deezer").lower()
            if not query:
                self._send_json(400, {"error": "Missing query parameter 'q'"})
                return
            try:
                self._send_json(200, _spotify_search(query) if provider == "spotify" else _deezer_search(query))
            except Exception as exc:
                logger.warning("%s search failed: %s", provider, exc)
                self._send_json(500, {"error": str(exc)})
            return

        # Resolve an album (by artist + title) to a downloadable Deezer album.
        if path == "/resolve-deezer":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            artist = params.get("artist", "").strip()
            album  = params.get("album", "").strip()
            if not album:
                self._send_json(400, {"error": "Missing album"})
                return
            aid = _deezer_search_album_id(artist, album)
            if aid:
                self._send_json(200, {"id": aid, "url": f"https://www.deezer.com/album/{aid}"})
            else:
                self._send_json(404, {"error": "Not found on Deezer"})
            return

        if path == "/debug-log":
            try:
                if LOG_FILE.exists():
                    size   = LOG_FILE.stat().st_size
                    offset = max(0, size - 102_400)
                    with LOG_FILE.open("rb") as f:
                        f.seek(offset)
                        content = f.read().decode("utf-8", errors="replace")
                    if offset > 0:
                        content = f"[… truncated, showing last 100 KB of {size // 1024} KB …]\n\n" + content
                else:
                    content = "No log file yet."
                self._send_json(200, {
                    "log":  content,
                    "path": str(LOG_FILE),
                    "size": LOG_FILE.stat().st_size if LOG_FILE.exists() else 0,
                })
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        # Telegram session status — local check only, no network call
        if path == "/telegram-status":
            session_exists = tgd_common.has_session()
            with _auth_lock:
                self._send_json(200, {
                    "session_exists": session_exists,
                    "step":           _auth_state["step"],
                    "username":       _auth_state["username"],
                    "error":          _auth_state["error"],
                })
            return

        # Telegram audio quality — GET fetches current setting from bot
        if path == "/telegram-quality":
            try:
                result = asyncio.run_coroutine_threadsafe(
                    _tg_get_quality(), _tg_loop
                ).result(timeout=15)
                self._send_json(200, result)
            except Exception as exc:
                logger.warning("Quality fetch failed: %s", exc)
                self._send_json(200, {"error": str(exc)})
            return

        if path == "/play-stats":
            self._send_json(200, _aggregate_play_stats(_load_play_events()))
            return

        # ── Scheduled queue run status (v1.9.0) ───────────────────────────
        if path == "/schedule-queue":
            self._send_json(200, _schedule_status())
            return

        # ── Telegram flood-wait health (v1.8.0) ───────────────────────────
        if path == "/telegram-health":
            data = _flood_health()
            data["backend_running"] = MANAGER.is_running()
            self._send_json(200, data)
            return

        # ── Export debug bundle (v1.8.0) ──────────────────────────────────
        if path == "/debug-bundle":
            import io as _io
            import platform as _platform
            import zipfile as _zf
            buf = _io.BytesIO()
            with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED) as zf:
                # Debug log — last 2 MB is plenty for a bug report
                try:
                    if LOG_FILE.exists():
                        raw = LOG_FILE.read_bytes()
                        zf.writestr("tgdownloader_debug.log", raw[-2_000_000:])
                except Exception:
                    pass
                # Config with every secret stripped (same list as /backup)
                try:
                    cfg = json.loads((DATA_DIR / "tg_audio_config.json").read_text("utf-8"))
                    for secret in ("api_id", "api_hash", "listenbrainz_token",
                                   "lastfm_api_key", "lastfm_secret", "lastfm_session_key",
                                   "spotify_client_id", "spotify_client_secret"):
                        cfg.pop(secret, None)
                    zf.writestr("tg_audio_config.json", json.dumps(cfg, indent=2))
                except Exception:
                    pass
                # Environment snapshot
                try:
                    info = [
                        f"version:  {APP_VERSION}",
                        f"platform: {_platform.platform()}",
                        f"python:   {sys.version.split()[0]}",
                        f"frozen:   {bool(getattr(sys, 'frozen', False))}",
                        f"data_dir: {DATA_DIR}",
                        f"ffmpeg:   {tgd_common.ffmpeg_exe() or 'not found'}",
                        f"fpcalc:   {tgd_common.fpcalc_exe() or 'not found'}",
                        f"keyring:  {bool(tgd_common._keyring())}",
                    ]
                    zf.writestr("environment.txt", "\n".join(info) + "\n")
                except Exception:
                    pass
            data = buf.getvalue()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition",
                             f'attachment; filename="tgdownloader-debug-{stamp}.zip"')
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        # ── Star ratings map for the library UI (v1.7.0) ──────────────────
        if path == "/ratings":
            items = _load_ratings()
            self._send_json(200, {"ratings": {k: v.get("rating", 0)
                                              for k, v in items.items()}})
            return

        # ── Browsable listening history (v1.7.0) ──────────────────────────
        if path == "/play-history":
            qs = urlparse(self.path).query
            params = dict(part.split("=", 1) for part in qs.split("&") if "=" in part)
            try:    limit = int(params.get("limit") or 200)
            except Exception: limit = 200
            try:    offset = int(params.get("offset") or 0)
            except Exception: offset = 0
            q = unquote_plus(params.get("q") or "")
            self._send_json(200, _filter_play_events(_load_play_events(),
                                                     q=q, limit=limit, offset=offset))
            return

        # ── Year-end Wrapped summary (v1.7.0) ─────────────────────────────
        if path == "/wrapped":
            qs = urlparse(self.path).query
            params = dict(part.split("=", 1) for part in qs.split("&") if "=" in part)
            try:
                year = int(params.get("year") or 0)
            except Exception:
                year = 0
            if not year:
                year = int(time.strftime("%Y"))
            self._send_json(200, _wrapped_stats(_load_play_events(), year))
            return

        # Pre-1.4.0 the frontend's loadSessions() GET always 404ed (only the
        # POST route existed) and silently fell back to an empty list.
        if path == "/sessions":
            self._send_json(200, _load_sessions())
            return

        if path == "/waveform":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            album_dir = _lookup_album_dir(params.get("path_hash", ""))
            src = _resolve_in_dir(album_dir, params.get("name", "")) if album_dir else None
            if not src:
                self.send_error(404)
                return
            peaks = _waveform_peaks(src)
            self._send_json(200, {"peaks": peaks or []})
            return

        if path == "/track-gain":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            album_dir = _lookup_album_dir(params.get("path_hash", ""))
            src = _resolve_in_dir(album_dir, params.get("name", "")) if album_dir else None
            if not src:
                self.send_error(404)
                return
            self._send_json(200, {"gain": tgd_common.read_replaygain_gain(src)})
            return

        if path == "/library-stats":
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(200, {"error": "No home music folder configured."})
                    return
                from pathlib import Path as _P
                home_path = _P(home)
                if not home_path.exists():
                    self._send_json(200, {"error": f"Folder not found: {home}"})
                    return

                m.migrate_artists_layout(home_path)   # ensure Artists/ layout
                artists_root = home_path / ARTISTS_DIRNAME

                # Serve from the persisted cache when the library is unchanged.
                # The signature only stats the manifest + album dirs (cheap),
                # whereas a full recompute walks every file twice and reads tags
                # from each (slow — this is what made the first load drag).
                from urllib.parse import parse_qs as _parse_qs
                _q = _parse_qs(urlparse(self.path).query)
                force_refresh = _q.get("refresh", ["0"])[0] in ("1", "true", "yes")
                signature = _library_stats_signature(home_path, artists_root)
                if not force_refresh:
                    try:
                        if _LIB_STATS_CACHE_FILE.exists():
                            cached = json.loads(_LIB_STATS_CACHE_FILE.read_text(encoding="utf-8"))
                            if cached.get("signature") == signature and isinstance(cached.get("data"), dict):
                                self._send_json(200, cached["data"])
                                return
                    except Exception:
                        logger.debug("library-stats cache read failed", exc_info=True)

                AUDIO_EXT = {".mp3",".flac",".ogg",".opus",".m4a",".aac",
                             ".wav",".aif",".aiff",".wma",".ape",".wv"}
                total_files = total_bytes = album_count = 0
                artist_stats: dict = {}   # artist -> {tracks, bytes}
                orphaned: list = []
                format_counts: dict = {}  # extension (e.g. "flac") -> file count

                for f in home_path.rglob("*"):
                    if not (f.is_file() and f.suffix.lower() in AUDIO_EXT):
                        continue
                    total_files += 1
                    sz           = f.stat().st_size
                    total_bytes += sz
                    ext          = f.suffix.lower().lstrip(".")
                    format_counts[ext] = format_counts.get(ext, 0) + 1

                # Per-artist stats from the Artists/ tree (artist/album/file)
                if artists_root.is_dir():
                    for f in artists_root.rglob("*"):
                        if not (f.is_file() and f.suffix.lower() in AUDIO_EXT):
                            continue
                        parts = f.relative_to(artists_root).parts
                        if len(parts) >= 2:
                            artist = parts[0]
                            if artist not in artist_stats:
                                artist_stats[artist] = {"tracks": 0, "bytes": 0}
                            artist_stats[artist]["tracks"] += 1
                            artist_stats[artist]["bytes"]  += f.stat().st_size
                        else:
                            orphaned.append(str(f.relative_to(home_path)))
                    # Count albums (subdirs at depth 2 under Artists/)
                    for artist_dir in artists_root.iterdir():
                        if artist_dir.is_dir() and not artist_dir.name.startswith("."):
                            for album_dir in artist_dir.iterdir():
                                if album_dir.is_dir():
                                    album_count += 1

                artists_by_tracks = sorted(
                    [{"name": k, **v} for k, v in artist_stats.items()],
                    key=lambda x: x["tracks"], reverse=True
                )
                avg_tracks = (total_files / max(album_count, 1))

                # History summary
                manifest = m.load_manifest(home_path)
                timestamps = [v.get("timestamp","") for v in manifest.values() if v.get("timestamp")]
                timestamps.sort()
                history_summary = {
                    "total_entries":  len(manifest),
                    "unique_artists": len({v.get("artist","") for v in manifest.values()}),
                    "earliest":       timestamps[0][:10]  if timestamps else None,
                    "latest":         timestamps[-1][:10] if timestamps else None,
                }

                # Library growth over time: cumulative tracks added per month,
                # derived from manifest entry timestamps + their file counts.
                monthly: dict = {}   # "YYYY-MM" -> tracks added that month
                for v in manifest.values():
                    ts = v.get("timestamp", "")
                    if len(ts) < 7:
                        continue
                    month = ts[:7]
                    added = len(v.get("files") or []) or 1
                    monthly[month] = monthly.get(month, 0) + added
                growth: list = []
                running = 0
                for month in sorted(monthly):
                    running += monthly[month]
                    growth.append({"month": month, "added": monthly[month], "cumulative": running})

                # Top genres (reuse the genre scanner used for playlist creation)
                top_genres: list = []
                try:
                    genre_map = _scan_genres(home_path)
                    top_genres = sorted(
                        ({"name": g, "tracks": len(tracks)} for g, tracks in genre_map.items()),
                        key=lambda x: x["tracks"], reverse=True
                    )[:12]
                except Exception:
                    logger.exception("genre scan failed in /library-stats")

                # Format / quality breakdown
                formats = sorted(
                    ({"ext": e.upper(), "count": c} for e, c in format_counts.items()),
                    key=lambda x: x["count"], reverse=True
                )

                payload = {
                    "total_files":           total_files,
                    "total_bytes":           total_bytes,
                    "artist_count":          len(artist_stats),
                    "album_count":           album_count,
                    "avg_tracks_per_album":  avg_tracks,
                    "artists_by_tracks":     artists_by_tracks[:20],
                    "orphaned":              orphaned,
                    "download_history_summary": history_summary,
                    "growth":                growth,
                    "top_genres":            top_genres,
                    "formats":               formats,
                }
                # Persist for instant first-load next time (validated by signature).
                try:
                    _LIB_STATS_CACHE_FILE.write_text(
                        json.dumps({"signature": signature, "data": payload}),
                        encoding="utf-8")
                except Exception:
                    logger.debug("library-stats cache write failed", exc_info=True)
                self._send_json(200, payload)
            except Exception as exc:
                logger.exception("Error in /library-stats")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/library-albums":
            try:
                import hashlib as _hl
                import re as _re_alb
                from concurrent.futures import ThreadPoolExecutor

                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(200, {"error": "No home music folder configured."})
                    return
                from pathlib import Path as _P
                home_path = _P(home)
                if not home_path.exists():
                    self._send_json(200, {"error": f"Folder not found: {home}"})
                    return

                AUDIO_EXT = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
                             ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}
                _ALBUM_ID_RE = _re_alb.compile(r"deezer\.com/(?:[a-z]{2,3}/)?album/(\d+)")

                m.migrate_artists_layout(home_path)   # nest artists under Artists/
                artists_root = home_path / ARTISTS_DIRNAME

                manifest = m.load_manifest(home_path)

                # Build lookup: sanitised_artist_name → list of (url, entry)
                artist_manifest: dict = {}
                for url_key, entry in manifest.items():
                    san = m._sanitise_path(entry.get("artist", ""))
                    artist_manifest.setdefault(san, []).append((url_key, entry))

                albums_list = []

                for artist_dir in (sorted(artists_root.iterdir()) if artists_root.is_dir() else []):
                    if not artist_dir.is_dir() or artist_dir.name.startswith("."):
                        continue

                    for album_dir in sorted(artist_dir.iterdir()):
                        if not album_dir.is_dir():
                            continue

                        # Count audio files in this album dir
                        track_count = sum(
                            1 for f in album_dir.iterdir()
                            if f.is_file() and f.suffix.lower() in AUDIO_EXT
                        )
                        if track_count == 0:
                            continue

                        # Stable hash for the local cover endpoint
                        ph = _hl.sha256(
                            str(album_dir.resolve()).encode()
                        ).hexdigest()[:16]
                        _path_hash_map[ph] = album_dir

                        # Match to manifest (album name match first)
                        deezer_url  = None
                        in_manifest = False
                        album_id    = None

                        artist_entries = artist_manifest.get(artist_dir.name, [])
                        for url_key, entry in artist_entries:
                            sanitised_albums = [m._sanitise_path(a) for a in entry.get("albums", [])]
                            if album_dir.name in sanitised_albums:
                                deezer_url  = url_key
                                in_manifest = True
                                break

                        # Fallback: artist-level manifest entry with no album breakdown
                        if not in_manifest:
                            for url_key, entry in artist_entries:
                                if not entry.get("albums"):
                                    deezer_url  = url_key
                                    in_manifest = True
                                    break

                        if deezer_url:
                            mm = _ALBUM_ID_RE.search(deezer_url)
                            if mm:
                                album_id = mm.group(1)

                        try:
                            _mtime = album_dir.stat().st_mtime
                        except Exception:
                            _mtime = 0
                        albums_list.append({
                            "artist":      artist_dir.name,
                            "album":       album_dir.name,
                            "album_dir":   str(album_dir.resolve()),
                            "track_count": track_count,
                            "cover_url":   None,
                            "deezer_url":  deezer_url,
                            "in_manifest": in_manifest,
                            "path_hash":   ph,
                            "is_playlist": False,
                            "mtime":       _mtime,
                            "_album_id":   album_id,
                        })

                # Fetch Deezer covers for IDs not yet cached (parallel, rate-limited)
                ids_needed = {
                    alb["_album_id"] for alb in albums_list
                    if alb["_album_id"] and alb["_album_id"] not in _cover_cache
                }

                # Albums with no known ID — search Deezer by artist+album name
                no_id_albums = [
                    alb for alb in albums_list
                    if not alb["_album_id"]
                ]

                def _fetch_cover(aid: str) -> "tuple[str, str, str]":
                    try:
                        data    = _deezer_album(aid)
                        url_val = data.get("cover_medium") or data.get("cover_small") or ""
                        artist  = data.get("artist") or {}
                        a_id    = str(artist.get("id", "")) if artist.get("id") else ""
                    except Exception:
                        url_val = ""
                        a_id    = ""
                    return aid, url_val, a_id

                def _search_and_fetch(alb: dict) -> "tuple[dict, str]":
                    """Resolve album ID via search. Cover/artist caches are populated
                    as a side-effect of _deezer_search_album_id. Returns (alb, aid)."""
                    aid = _deezer_search_album_id(alb["artist"], alb["album"])
                    return alb, aid or ""

                if ids_needed:
                    with ThreadPoolExecutor(max_workers=8) as ex:
                        for aid, url_val, a_id in ex.map(_fetch_cover, list(ids_needed)):
                            _cover_cache[aid] = url_val
                            if a_id:
                                _album_artist_id[aid] = a_id

                if no_id_albums:
                    with ThreadPoolExecutor(max_workers=8) as ex:
                        for alb, aid in ex.map(_search_and_fetch, no_id_albums):
                            if aid:
                                alb["_album_id"] = aid

                # Assign resolved cover URLs; expose album ID for frontend preview fetches
                for alb in albums_list:
                    if alb["_album_id"]:
                        cached = _cover_cache.get(alb["_album_id"], "")
                        alb["cover_url"]       = cached if cached else None
                        alb["artist_id"]       = _album_artist_id.get(alb["_album_id"], "")
                        alb["deezer_album_id"] = alb["_album_id"]
                    else:
                        alb["artist_id"]       = ""
                        alb["deezer_album_id"] = ""
                    del alb["_album_id"]

                # Sort: in_manifest first, then artist A→Z, album A→Z
                albums_list.sort(key=lambda x: (
                    not x["in_manifest"],
                    x["artist"].lower(),
                    x["album"].lower(),
                ))

                # ── Generated/downloaded playlists (home/Playlists/<name>/) ──
                # Downloaded playlists store their original Spotify/Deezer cover
                # URL in the manifest; genre playlists fall back to embedded art.
                pl_cover_by_name: dict = {}
                for _u, _e in manifest.items():
                    if _e.get("is_playlist") and _e.get("cover"):
                        pl_cover_by_name[m._sanitise_path(_e.get("playlist", ""))] = _e["cover"]

                playlists_list = []
                playlists_root = home_path / PLAYLISTS_DIRNAME
                if playlists_root.is_dir():
                    for pl_dir in sorted(playlists_root.iterdir()):
                        if not pl_dir.is_dir() or pl_dir.name.startswith("."):
                            continue
                        track_count = sum(
                            1 for f in pl_dir.iterdir()
                            if f.is_file() and f.suffix.lower() in AUDIO_EXT
                        )
                        if track_count == 0:
                            continue
                        ph = _hl.sha256(str(pl_dir.resolve()).encode()).hexdigest()[:16]
                        _path_hash_map[ph] = pl_dir
                        # Drop any cached cover so a re-downloaded playlist's new
                        # sidecar cover.jpg is always served fresh.
                        _local_cover_cache.pop(ph, None)
                        playlists_list.append({
                            "artist":          "Playlist",
                            "album":           pl_dir.name,
                            "album_dir":       str(pl_dir.resolve()),
                            "track_count":     track_count,
                            "cover_url":       pl_cover_by_name.get(pl_dir.name) or None,
                            "deezer_url":      None,
                            "in_manifest":     False,
                            "path_hash":       ph,
                            "is_playlist":     True,
                            "artist_id":       "",
                            "deezer_album_id": "",
                        })
                playlists_list.sort(key=lambda x: x["album"].lower())

                self._send_json(200, albums_list + playlists_list)
            except Exception as exc:
                logger.exception("Error in /library-albums")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Available genres (from local tags) for the playlist builder ───
        if path == "/genres":
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(200, {"error": "No home music folder configured."})
                    return
                home_path = Path(home)
                if not home_path.exists():
                    self._send_json(200, {"error": f"Folder not found: {home}"})
                    return
                genres = _scan_genres(home_path)
                out = sorted(
                    ({"genre": g, "track_count": len(tracks)} for g, tracks in genres.items()),
                    key=lambda x: (-x["track_count"], x["genre"].lower()),
                )
                self._send_json(200, {"genres": out})
            except Exception as exc:
                logger.exception("Error in /genres")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Liked songs (persisted favourites) ────────────────────────────
        if path == "/liked-songs":
            try:
                self._send_json(200, {"tracks": _load_liked()})
            except Exception as exc:
                logger.exception("Error in /liked-songs")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Local embedded cover art (Phase 2 fallback) ───────────────────
        if path.startswith("/cover/") and path.count("/") == 2:
            ph = path.split("/")[2]
            if ph in _local_cover_cache:
                img_bytes, mime = _local_cover_cache[ph]
            else:
                album_dir = _lookup_album_dir(ph)
                if not album_dir:
                    self.send_error(404)
                    return
                result = _extract_cover_bytes(album_dir)
                if result is None:
                    self.send_error(404)
                    return
                img_bytes, mime = result
                _local_cover_cache[ph] = (img_bytes, mime)
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", len(img_bytes))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(img_bytes)
            return

        # ── Per-track embedded cover (thumbnail beside each track title) ──
        if path == "/track-cover":
            qs = urlparse(self.path).query
            params: dict[str, str] = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            ph       = params.get("path_hash", "")
            filename = params.get("name", "")
            album_dir = _lookup_album_dir(ph)
            if not album_dir or not filename:
                self.send_error(404)
                return
            ckey = ph + "\x00" + filename
            if ckey in _track_cover_cache:
                cached = _track_cover_cache[ckey]
                if cached is None:
                    self.send_error(404)
                    return
                img_bytes, mime = cached
            else:
                file_path = (album_dir / filename).resolve()
                try:
                    file_path.relative_to(album_dir.resolve())
                except ValueError:
                    self.send_error(403)
                    return
                result = _extract_file_cover_bytes(file_path) if file_path.is_file() else None
                _track_cover_cache[ckey] = result
                if result is None:
                    self.send_error(404)
                    return
                img_bytes, mime = result
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", len(img_bytes))
            self.send_header("Cache-Control", "max-age=3600")
            self.end_headers()
            self.wfile.write(img_bytes)
            return

        # ── Artist metadata (Deezer, cached) ─────────────────────────────
        if path.startswith("/artist-meta/") and path.count("/") == 2:
            artist_id = path.split("/")[2]
            if artist_id.isdigit():
                data = _deezer_artist_meta(artist_id)
                self._send_json(200 if "error" not in data else 404, data)
            else:
                self._send_json(400, {"error": "Invalid artist id"})
            return

        # ── Artist biography (Wikipedia — Deezer provides none) ──────────
        if path == "/artist-bio":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            name = params.get("name", "").strip()
            if not name:
                self._send_json(400, {"error": "Missing 'name'"})
                return
            data = _wikipedia_bio(name)
            self._send_json(200 if "error" not in data else 404, data)
            return

        # ── Album recommendations (seeded from library artists) ──────────
        if path == "/recommendations":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            seeds = [s for s in (params.get("seeds", "").split("|")) if s.strip()]
            self._send_json(200, _deezer_recommendations(seeds))
            return

        # ── Autoplay radio (Deezer related-artist previews) ──────────────
        if path == "/radio":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            data = _deezer_radio(params.get("artist", ""), params.get("artist_id", ""))
            self._send_json(200 if "error" not in data else 404, data)
            return

        # ── Song lyrics (LRCLIB) ─────────────────────────────────────────
        if path == "/lyrics":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            data = _fetch_lyrics(
                params.get("artist", ""), params.get("title", ""),
                params.get("album", ""),  params.get("duration", ""),
            )
            self._send_json(200 if "error" not in data else 404, data)
            return

        # ── Album track listing (local files + Deezer preview URLs) ──────
        if path == "/album-tracks":
            qs = urlparse(self.path).query
            params: dict[str, str] = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            ph       = params.get("path_hash", "")
            album_id = params.get("album_id", "")
            if not ph:
                self._send_json(400, {"error": "Missing path_hash"})
                return
            album_dir = _path_hash_map.get(ph)
            if not album_dir:
                self._send_json(404, {"error": "Album not found — try refreshing the library."})
                return
            try:
                tracks = _get_album_tracks(album_dir, album_id or "")
                self._send_json(200, {"tracks": tracks})
            except Exception as exc:
                logger.exception("Error in /album-tracks")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Local audio file streaming ─────────────────────────────────────
        # ── Transcoded stream (fallback for files the browser can't decode) ──
        if path == "/audio-stream":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            ph       = params.get("path_hash", "")
            filename = params.get("name", "")
            album_dir = _lookup_album_dir(ph)
            if not album_dir or not filename:
                self.send_error(404)
                return
            src = _resolve_in_dir(album_dir, filename)
            if not src:
                self.send_error(404)
                return
            if not _ffmpeg_exe():
                # 501 → the frontend tells the user to install ffmpeg
                self.send_error(501, "ffmpeg not installed")
                return
            out = _transcode_to_mp3(src)
            if not out:
                self.send_error(500, "transcode failed")
                return
            _send_file_with_range(self, out, "audio/mpeg")
            return

        if path == "/audio-file":
            qs = urlparse(self.path).query
            params: dict[str, str] = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[unquote_plus(k)] = unquote_plus(v)
            ph       = params.get("path_hash", "")
            filename = params.get("name", "")
            if not ph or not filename:
                self.send_error(400)
                return
            album_dir = _lookup_album_dir(ph)
            if not album_dir:
                self.send_error(404)
                return
            # Traversal-guarded resolve with Unicode-NFC / case fallback,
            # then the shared Range-aware streamer (same path as /audio-stream).
            file_path = _resolve_in_dir(album_dir, filename)
            if not file_path:
                self.send_error(404)
                return
            mime_map = {
                ".mp3":  "audio/mpeg",  ".flac": "audio/flac",
                ".ogg":  "audio/ogg",   ".opus": "audio/ogg",
                ".m4a":  "audio/mp4",   ".aac":  "audio/aac",
                ".wav":  "audio/wav",   ".aif":  "audio/aiff",
                ".aiff": "audio/aiff",  ".wma":  "audio/x-ms-wma",
                ".ape":  "audio/ape",   ".wv":   "audio/wavpack",
            }
            mime = mime_map.get(file_path.suffix.lower(), "audio/mpeg")
            _send_file_with_range(self, file_path, mime)
            return

        self.send_error(404)

    def do_POST(self):
        if self._reject_foreign():
            return
        path   = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"Invalid JSON body: {exc}"})
            return

        if path == "/apply-update":
            # Downloads + stages the latest release, then relaunches into it.
            self._send_json(200, _apply_update())
            return

        if path == "/setup":
            api_id      = body.get("api_id")
            api_hash    = (body.get("api_hash") or "").strip().lower()
            bot_username = (body.get("bot_username") or "").strip()

            if not api_id or not str(api_id).isdigit():
                self._send_json(400, {"error": "api_id must be a number"})
                return
            if not api_hash or len(api_hash) != 32:
                self._send_json(400, {"error": "api_hash must be 32 hex characters"})
                return
            if not bot_username or not bot_username.startswith("@") or len(bot_username) < 5:
                self._send_json(400, {"error": "bot_username must start with @ and be at least 5 characters"})
                return

            # Through tgd_common so the keyring overlay (use_keyring) applies
            # to api_hash the same way it does for every other secret.
            try:
                cfg = tgd_common.load_config()
                cfg["api_id"]       = int(api_id)
                cfg["api_hash"]     = api_hash
                cfg["bot_username"] = bot_username
                tgd_common.save_config(cfg)
                logger.info("Setup wizard complete: credentials and bot saved")
                self._send_json(200, {"ok": True})
            except Exception as exc:
                logger.exception("Failed to save setup wizard data")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/config":
            m   = _tgd_import()
            cfg = m.load_config()
            cfg.update(body)
            m.save_config(cfg)
            # Applying the library-watcher flag needs to (re)start/stop its thread,
            # not just persist the value — do it whenever the flag is in the patch.
            if "watch_library" in body:
                _apply_lib_watcher(cfg)
            if "discord_rich_presence" in body or "discord_client_id" in body:
                _presence_apply(cfg)
            self._send_json(200, {"ok": True})
            return

        if path == "/scrobble":
            m   = _tgd_import()
            cfg = m.load_config()
            kind = body.get("kind", "scrobble")   # "now_playing" | "scrobble"
            result = _scrobble_submit(cfg, {
                "title":  body.get("title"),
                "artist": body.get("artist"),
                "album":  body.get("album"),
            }, now_playing=(kind == "now_playing"))
            self._send_json(200, result)
            return

        if path == "/presence":
            # Discord Rich Presence updates from the player (fire-and-forget).
            try:
                _presence_update((body.get("op") or "").strip(), {
                    "title":    body.get("title", ""),
                    "artist":   body.get("artist", ""),
                    "album":    body.get("album", ""),
                    "position": body.get("position", 0),
                })
            except Exception:
                logger.debug("presence endpoint error", exc_info=True)
            self._send_json(200, {"ok": True})
            return

        if path == "/watchlist":
            action = body.get("action", "add")
            if action == "add":
                self._send_json(200, _watchlist_add(
                    body.get("artist_id"), body.get("name"), body.get("cover_url")))
            elif action == "remove":
                wl = _load_watchlist()
                wl.pop(str(body.get("artist_id", "")), None)
                _save_watchlist(wl)
                self._send_json(200, {"ok": True, "watching": False, "count": len(wl)})
            elif action == "mark_seen":
                wl  = _load_watchlist()
                aid = str(body.get("artist_id", ""))
                info = wl.get(aid)
                if info is not None:
                    known = set(info.get("known_album_ids") or [])
                    known.update(str(x) for x in (body.get("album_ids") or []))
                    info["known_album_ids"] = sorted(known)
                    _save_watchlist(wl)
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": f"unknown action: {action}"})
            return

        if path == "/delete-file":
            m    = _tgd_import()
            cfg  = m.load_config()
            home = cfg.get("home_music_folder")
            target = body.get("path", "")
            if not home or not target:
                self._send_json(400, {"error": "Missing home folder or path"})
                return
            from pathlib import Path as _P
            home_path = _P(home).resolve()
            try:
                tp = _P(target).resolve()
            except Exception:
                self._send_json(400, {"error": "Invalid path"})
                return
            # Safety: only allow deleting files that live under the music library
            if home_path not in tp.parents:
                self._send_json(403, {"error": "Refusing to delete outside the library folder"})
                return
            try:
                method = tgd_common.send_to_trash(tp)
                logger.info("Trashed duplicate file (%s): %s", method, tp)
                self._send_json(200, {"ok": True, "trash": method})
            except Exception as exc:
                logger.exception("delete-file failed")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/edit-tags":
            m    = _tgd_import()
            cfg  = m.load_config()
            home = cfg.get("home_music_folder")
            tags = body.get("tags") or {}
            # Two input modes: explicit absolute `files`, or {path_hash, names}
            # resolved through the in-memory album map (preferred from the UI).
            files = list(body.get("files") or [])
            ph = (body.get("path_hash") or "").strip()
            if ph:
                names = body.get("names") or []
                files += [str(p) for p in _resolve_track_sources(
                    [{"path_hash": ph, "name": n} for n in names])]
            if not home or not files:
                self._send_json(400, {"error": "Missing home folder or files"})
                return
            from pathlib import Path as _P
            home_path = _P(home).resolve()
            # Only persist the recognised, non-empty easy-tag fields
            allowed = {k: str(v) for k, v in tags.items()
                       if k in ("artist", "album", "albumartist", "genre", "date", "title")
                       and str(v).strip() != ""}
            if not allowed:
                self._send_json(400, {"error": "No editable tags provided"})
                return
            updated, errors = 0, []
            for fp in files:
                try:
                    tp = _P(fp).resolve()
                except Exception:
                    continue
                if home_path not in tp.parents:
                    errors.append({"file": fp, "error": "outside library"})
                    continue
                try:
                    from mutagen import File as _MF
                    audio = _MF(tp, easy=True)
                    if audio is None:
                        errors.append({"file": fp, "error": "unreadable"})
                        continue
                    for k, v in allowed.items():
                        audio[k] = [v]
                    audio.save()
                    updated += 1
                except Exception as exc:
                    errors.append({"file": fp, "error": str(exc)})
            self._send_json(200, {"ok": True, "updated": updated, "errors": errors})
            return

        if path == "/sessions":
            sessions = _load_sessions()
            if "delete" in body:
                sessions.pop(body["delete"], None)
                _save_sessions(sessions)
                self._send_json(200, {"ok": True})
            elif "name" in body and "entries" in body:
                sessions[body["name"]] = body["entries"]
                _save_sessions(sessions)
                self._send_json(200, {"ok": True})
            else:
                self._send_json(400, {"error": "Invalid body"})
            return

        if path == "/history-remove":
            url_to_remove = body.get("url", "")
            if url_to_remove:
                try:
                    m    = _tgd_import()
                    cfg  = m.load_config()
                    home = cfg.get("home_music_folder")
                    if home:
                        from pathlib import Path as _P
                        manifest = m.load_manifest(_P(home))
                        manifest.pop(url_to_remove, None)
                        m.save_manifest(_P(home), manifest)
                except Exception as exc:
                    logger.exception("Error in /history-remove")
                    self._send_json(500, {"error": str(exc)})
                    return
            self._send_json(200, {"ok": True})
            return

        if path == "/debug-log-clear":
            try:
                LOG_FILE.write_text("", encoding="utf-8")
                logger.info("Debug log cleared by user")
                self._send_json(200, {"ok": True})
            except Exception as exc:
                self._send_json(500, {"error": str(exc)})
            return

        # ── Delete artist folder ──────────────────────────────────────────
        if path == "/delete-artist":
            artist_name = body.get("artist", "").strip()
            if not artist_name:
                self._send_json(400, {"error": "Missing artist name"})
                return
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(400, {"error": "No home music folder configured"})
                    return
                artist_dir = Path(home) / ARTISTS_DIRNAME / m._sanitise_path(artist_name)
                if artist_dir.exists() and artist_dir.is_dir():
                    method = tgd_common.send_to_trash(artist_dir)
                    logger.info("Trashed artist folder (%s): %s", method, artist_dir)
                    self._send_json(200, {"ok": True, "trash": method})
                else:
                    self._send_json(404, {"error": f"Artist folder not found: {artist_dir}"})
            except Exception as exc:
                logger.exception("Error in /delete-artist")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Delete album folder ───────────────────────────────────────────
        if path == "/delete-album":
            album_dir_str = body.get("album_dir", "").strip()
            if not album_dir_str:
                self._send_json(400, {"error": "Missing album_dir"})
                return
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(400, {"error": "No home music folder configured"})
                    return
                album_dir = Path(album_dir_str)
                # Security: must be inside home music folder
                home_path = Path(home).resolve()
                if not str(album_dir.resolve()).startswith(str(home_path)):
                    self._send_json(403, {"error": "Album dir outside music folder"})
                    return
                if album_dir.exists() and album_dir.is_dir():
                    method = tgd_common.send_to_trash(album_dir)
                    logger.info("Trashed album folder (%s): %s", method, album_dir)
                    self._send_json(200, {"ok": True, "trash": method})
                else:
                    self._send_json(404, {"error": f"Album folder not found: {album_dir}"})
            except Exception as exc:
                logger.exception("Error in /delete-album")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Create a genre-based playlist (copies + renumbers tracks) ─────
        if path == "/create-genre-playlist":
            genre = (body.get("genre") or "").strip()
            name  = (body.get("name")  or "").strip()
            if not genre:
                self._send_json(400, {"error": "Missing genre"})
                return
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(400, {"error": "No home music folder configured"})
                    return
                home_path = Path(home)
                if not home_path.exists():
                    self._send_json(400, {"error": f"Folder not found: {home}"})
                    return
                result = _create_genre_playlist(home_path, genre, name)
                self._send_json(200 if result.get("ok") else 400, result)
            except Exception as exc:
                logger.exception("Error in /create-genre-playlist")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/create-smart-playlist":
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home or not Path(home).exists():
                    self._send_json(400, {"error": "No home music folder configured"})
                    return
                result = _create_smart_playlist(Path(home), (body.get("name") or "").strip(), body or {})
                self._send_json(200 if result.get("ok") else 400, result)
            except Exception as exc:
                logger.exception("Error in /create-smart-playlist")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Add selected tracks to a playlist (new or existing) ───────────
        if path == "/playlist-add-tracks":
            name   = (body.get("name") or "").strip()
            tracks = body.get("tracks") or []
            create = bool(body.get("create"))
            if not name:
                self._send_json(400, {"error": "Missing playlist name"})
                return
            if not tracks:
                self._send_json(400, {"error": "No tracks selected"})
                return
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(400, {"error": "No home music folder configured"})
                    return
                result = _add_tracks_to_playlist(Path(home), name, tracks, create)
                self._send_json(200 if result.get("ok") else 400, result)
            except Exception as exc:
                logger.exception("Error in /playlist-add-tracks")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Remove selected tracks from a playlist ────────────────────────
        if path == "/playlist-remove-tracks":
            ph    = (body.get("path_hash") or "").strip()
            names = body.get("names") or []
            if not ph or not names:
                self._send_json(400, {"error": "Missing path_hash or names"})
                return
            try:
                result = _remove_tracks_from_playlist(ph, names)
                self._send_json(200 if result.get("ok") else 400, result)
            except Exception as exc:
                logger.exception("Error in /playlist-remove-tracks")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/playlist-reorder":
            ph    = (body.get("path_hash") or "").strip()
            names = body.get("names") or []
            if not ph or not names:
                self._send_json(400, {"error": "Missing path_hash or names"})
                return
            try:
                result = _reorder_playlist_tracks(ph, names)
                self._send_json(200 if result.get("ok") else 400, result)
            except Exception as exc:
                logger.exception("Error in /playlist-reorder")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Toggle a song's "liked" state ─────────────────────────────────
        if path == "/toggle-like":
            try:
                result = _toggle_liked(body or {})
                self._send_json(400 if result.get("error") else 200, result)
            except Exception as exc:
                logger.exception("Error in /toggle-like")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Schedule / cancel a queue run (v1.9.0) ────────────────────────
        if path == "/schedule-queue":
            try:
                result = _schedule_set(float(body.get("at") or 0),
                                       body.get("entries") or [],
                                       str(body.get("home") or ""))
                self._send_json(400 if result.get("error") else 200, result)
            except Exception as exc:
                logger.exception("Error in /schedule-queue")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/schedule-cancel":
            self._send_json(200, _schedule_cancel())
            return

        # ── Set a track's star rating (v1.7.0) ────────────────────────────
        if path == "/rate":
            try:
                result = _set_rating(body or {})
                self._send_json(400 if result.get("error") else 200, result)
            except Exception as exc:
                logger.exception("Error in /rate")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Native Windows folder picker ──────────────────────────────────
        if path == "/browse-folder":
            current = body.get("current", "")
            chosen  = _pick_folder_native(current)
            self._send_json(200, {"path": chosen})
            return

        # ── Import an external folder through the sort/dedupe pipeline ─────
        if path == "/import-folder":
            src = (body.get("path") or "").strip()
            if not src:
                # No path given → open the native picker for the user.
                src = _pick_folder_native("")
                if not src:
                    self._send_json(200, {"ok": False, "cancelled": True})
                    return
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(400, {"error": "No home music folder configured"})
                    return
                home_path = Path(home)
                src_path  = Path(src)
                if not src_path.is_dir():
                    self._send_json(400, {"error": f"Not a folder: {src}"})
                    return
                if home_path.resolve() in src_path.resolve().parents or \
                   src_path.resolve() == home_path.resolve():
                    self._send_json(400, {"error": "Choose a folder outside your library to import from."})
                    return
                # Group the imported files by their album tag, exactly like a
                # download: sort_by_album fuzzy-matches artist/album folders and
                # applies the hash dedupe index so re-imports are skipped.
                hash_index = m.build_library_hash_index(home_path)
                by_artist: "dict[str, list]" = {}
                for f in src_path.rglob("*"):
                    if f.is_file() and f.suffix.lower() in _AUDIO_EXT:
                        art = m._get_artist(f) if hasattr(m, "_get_artist") else ""
                        by_artist.setdefault(art or "Imported", []).append(f)
                imported = dupes = 0
                for art, files in by_artist.items():
                    artist_dir = (m._fuzzy_match_dir(art, m.artists_root(home_path))
                                  or m.artists_root(home_path) / m._sanitise_path(art))
                    # Stage into a temp dir so sort_by_album can move them out.
                    staged = home_path / ".tgimport_tmp"
                    staged.mkdir(parents=True, exist_ok=True)
                    for f in files:
                        try:
                            dest = staged / f.name
                            import shutil as _sh
                            _sh.copy2(str(f), str(dest))
                        except Exception:
                            pass
                    d, _albums = m.sort_by_album(staged, artist_dir, hash_index)
                    dupes    += d
                    imported += sum(1 for _ in files) - d
                    import shutil as _sh
                    _sh.rmtree(staged, ignore_errors=True)
                self._send_json(200, {"ok": True, "imported": imported, "dupes": dupes,
                                      "source": str(src_path)})
            except Exception as exc:
                logger.exception("import-folder failed")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Telegram audio quality SET ────────────────────────────────────
        if path == "/telegram-quality":
            target = body.get("quality", "").strip()
            if not target:
                self._send_json(400, {"error": "Missing quality"})
                return
            try:
                result = asyncio.run_coroutine_threadsafe(
                    _tg_set_quality(target), _tg_loop
                ).result(timeout=25)
                self._send_json(200, result)
            except Exception as exc:
                logger.warning("Quality set failed: %s", exc)
                self._send_json(200, {"error": str(exc)})
            return

        # ── Telegram auth (step-by-step, driven from the browser) ─────────
        if path == "/telegram-auth":
            action = body.get("action", "")

            if action == "start":
                with _auth_lock:
                    cur = _auth_state["step"]
                # Don't restart an already-running flow
                if cur not in ("idle", "done", "error"):
                    with _auth_lock:
                        self._send_json(200, {
                            "step":     _auth_state["step"],
                            "username": _auth_state["username"],
                        })
                    return
                # Kick off the auth coroutine in _tg_loop
                asyncio.run_coroutine_threadsafe(_do_telegram_auth(), _tg_loop)
                # Give Telethon up to 5 s to validate an existing session
                # before we decide whether the UI needs to ask for a phone number
                _wait_auth({"done", "error", "need_phone"}, timeout=5.0)
                with _auth_lock:
                    self._send_json(200, {
                        "step":     _auth_state["step"],
                        "username": _auth_state["username"],
                        "error":    _auth_state["error"],
                    })
                return

            if action == "submit_phone":
                phone = body.get("value", "").strip()
                with _auth_lock:
                    fut  = _auth_state.get("phone_future")
                    step = _auth_state["step"]
                if fut and not fut.done() and step == "need_phone":
                    _tg_loop.call_soon_threadsafe(fut.set_result, phone)
                # Wait for Telegram to send the SMS and step to advance
                _wait_auth({"need_code", "error", "done"}, timeout=30.0)
                with _auth_lock:
                    self._send_json(200, {
                        "step":  _auth_state["step"],
                        "error": _auth_state["error"],
                    })
                return

            if action == "submit_code":
                code = body.get("value", "").strip()
                with _auth_lock:
                    fut = _auth_state.get("code_future")
                if fut and not fut.done():
                    _tg_loop.call_soon_threadsafe(fut.set_result, code)
                _wait_auth({"done", "need_password", "error"}, timeout=30.0)
                with _auth_lock:
                    self._send_json(200, {
                        "step":     _auth_state["step"],
                        "username": _auth_state["username"],
                        "error":    _auth_state["error"],
                    })
                return

            if action == "submit_password":
                pw = body.get("value", "").strip()
                with _auth_lock:
                    fut = _auth_state.get("pw_future")
                if fut and not fut.done():
                    _tg_loop.call_soon_threadsafe(fut.set_result, pw)
                _wait_auth({"done", "error"}, timeout=30.0)
                with _auth_lock:
                    self._send_json(200, {
                        "step":     _auth_state["step"],
                        "username": _auth_state["username"],
                        "error":    _auth_state["error"],
                    })
                return

            if action == "disconnect":
                try:
                    # Remove the session from BOTH stores: plaintext file + keyring.
                    tgd_common.remove_session_file()
                    tgd_common.clear_session_string()
                    with _quality_session_lock:
                        global _quality_session_string
                        _quality_session_string = None
                    with _auth_lock:
                        _auth_state.update({"step": "idle", "username": None, "error": None})
                    logger.info("Telegram session disconnected by user")
                    self._send_json(200, {"ok": True})
                except Exception as exc:
                    self._send_json(500, {"error": str(exc)})
                return

            self._send_json(400, {"error": f"Unknown action: {action}"})
            return

        # ── Open folder in system file manager ───────────────────────────
        if path == "/open-folder":
            folder_path = body.get("path", "").strip()
            if not folder_path:
                self._send_json(400, {"error": "Missing path"})
                return
            try:
                import subprocess as _sp
                if sys.platform == "win32":
                    _sp.Popen(["explorer", folder_path])
                elif sys.platform == "darwin":
                    _sp.Popen(["open", folder_path])
                else:
                    _sp.Popen(["xdg-open", folder_path])
                self._send_json(200, {"ok": True})
            except Exception as exc:
                logger.warning("open-folder failed: %s", exc)
                self._send_json(500, {"error": str(exc)})
            return

        # ── Local play tracking ───────────────────────────────────────────
        if path == "/play-event":
            try:
                ok = _record_play_event(body)
                self._send_json(200 if ok else 400,
                                {"ok": ok} if ok else
                                {"ok": False, "error": "title and artist required"})
            except Exception as exc:
                logger.exception("play-event failed")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Restore a state backup made by /backup ────────────────────────
        if path == "/restore-backup":
            try:
                from base64 import b64decode as _b64d
                raw = _b64d(body.get("zip_b64") or "")
                accepted, skipped = _validate_backup_zip(raw)
            except Exception as exc:
                self._send_json(400, {"error": f"Not a valid backup zip: {exc}"})
                return
            restored: "list[str]" = []
            try:
                for base, data_bytes in accepted.items():
                    if base == "tg_audio_config.json":
                        incoming = json.loads(data_bytes.decode("utf-8"))
                        # Never let a backup overwrite live credentials —
                        # /backup strips them, but a hand-edited zip might not.
                        for k in (*tgd_common.SECRET_KEYS, "api_id", "spotify_client_id"):
                            incoming.pop(k, None)
                        cfg = tgd_common.load_config()
                        cfg.update(incoming)
                        tgd_common.save_config(cfg)
                    else:
                        (DATA_DIR / base).write_bytes(data_bytes)
                    restored.append(base)
                logger.info("Backup restored: %s (skipped: %s)", restored, skipped)
                self._send_json(200, {"ok": True, "restored": restored, "skipped": skipped})
            except Exception as exc:
                logger.exception("restore-backup failed")
                self._send_json(500, {"error": str(exc), "restored": restored})
            return

        # ── Save a synced-lyrics sidecar next to the audio file ───────────
        if path == "/save-lrc":
            lrc = body.get("lrc")
            if not isinstance(lrc, str) or not lrc.strip():
                self._send_json(400, {"error": "Missing lrc text"})
                return
            album_dir = _lookup_album_dir(body.get("path_hash", ""))
            src = _resolve_in_dir(album_dir, body.get("name", "")) if album_dir else None
            if not src:
                self._send_json(404, {"error": "Track not found"})
                return
            try:
                dst = src.with_suffix(".lrc")
                dst.write_text(lrc[:200_000], encoding="utf-8")
                logger.info("Saved lyrics sidecar: %s", dst)
                self._send_json(200, {"ok": True, "file": dst.name})
            except Exception as exc:
                logger.exception("save-lrc failed")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Library intelligence (v1.6.0) ─────────────────────────────────
        if path == "/tag-janitor":
            try:
                result = _tag_janitor(apply=bool(body.get("apply")),
                                      limit=max(1, min(int(body.get("limit") or 500), 2000)),
                                      fill_year=bool(body.get("fill_year")))
                self._send_json(200 if not result.get("error") else 400, result)
            except Exception as exc:
                logger.exception("tag-janitor failed")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/corruption-scan":
            try:
                result = _corruption_scan(limit=max(1, min(int(body.get("limit") or 400), 5000)))
                self._send_json(200 if not result.get("error") else 400, result)
            except Exception as exc:
                logger.exception("corruption-scan failed")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/album-completeness":
            try:
                result = _album_completeness(limit=max(1, min(int(body.get("limit") or 60), 500)))
                self._send_json(200 if not result.get("error") else 400, result)
            except Exception as exc:
                logger.exception("album-completeness failed")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/fingerprint-scan":
            try:
                result = _fingerprint_scan(limit=max(1, min(int(body.get("limit") or 50), 500)))
                self._send_json(200 if not result.get("error") else 400, result)
            except Exception as exc:
                logger.exception("fingerprint-scan failed")
                self._send_json(500, {"error": str(exc)})
            return

        if path == "/dedupe-auto":
            # Trash all but the best copy in every duplicate group.
            try:
                m    = _tgd_import()
                cfg  = m.load_config()
                home = cfg.get("home_music_folder")
                if not home:
                    self._send_json(400, {"error": "No home music folder configured"})
                    return
                home_path = Path(home).resolve()
                trashed = 0
                for paths in m.build_duplicate_groups(home_path):
                    scored = sorted(paths, key=lambda p: _quality_score(Path(p)), reverse=True)
                    for loser in scored[1:]:                 # keep scored[0]
                        lp = Path(loser).resolve()
                        if home_path in lp.parents:
                            tgd_common.send_to_trash(lp)
                            trashed += 1
                self._send_json(200, {"ok": True, "trashed": trashed})
            except Exception as exc:
                logger.exception("dedupe-auto failed")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Batch ReplayGain tagging for the existing library ─────────────
        if path == "/loudness-scan":
            try:
                limit  = int(body.get("limit") or 25)
                result = _loudness_scan(limit=max(1, min(limit, 200)))
                self._send_json(200 if not result.get("error") else 400, result)
            except Exception as exc:
                logger.exception("loudness-scan failed")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Look up track tempo (BPM) from Deezer for smart-playlist rules ─
        if path == "/bpm-scan":
            try:
                limit  = int(body.get("limit") or 40)
                result = _bpm_scan(limit=max(1, min(limit, 200)))
                self._send_json(200 if not result.get("error") else 400, result)
            except Exception as exc:
                logger.exception("bpm-scan failed")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Fetch missing album covers from Deezer ────────────────────────
        if path == "/art-repair":
            try:
                limit  = int(body.get("limit") or 25)
                result = _art_repair(limit=max(1, min(limit, 100)))
                self._send_json(200 if not result.get("error") else 400, result)
            except Exception as exc:
                logger.exception("art-repair failed")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Graceful quit ─────────────────────────────────────────────────
        if path == "/quit":
            self._send_json(200, {"ok": True})
            logger.info("Quit requested via /quit endpoint")

            def _shutdown():
                time.sleep(0.4)   # let the HTTP response flush first
                MANAGER.stop()
                if SERVER is not None:
                    try:
                        SERVER.shutdown()
                    except Exception:
                        pass
                # os._exit bypasses Python's atexit / thread cleanup so the
                # process actually dies even if daemon threads are mid-operation.
                os._exit(0)

            threading.Thread(target=_shutdown, daemon=True).start()
            return

        self.send_error(404)


class Server(ThreadingMixIn, HTTPServer):
    daemon_threads     = True
    allow_reuse_address = True   # Prevents "Address already in use" on quick restart


# ══════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════



async def _silent_auth_check() -> None:
    """On startup, reuse an existing session (keyring or file) without prompts,
    so the quality badge works immediately without pressing the TG button."""
    try:
        if str(BUNDLE_DIR) not in sys.path:
            sys.path.insert(0, str(BUNDLE_DIR))
        from telethon import TelegramClient as _TGC
        from telethon.sessions import StringSession as _SS
        _api_id, _api_hash = _load_api_credentials()
        if not _api_id:
            return
        _kr_sess = tgd_common.load_session_string()
        if _kr_sess:
            client = _TGC(_SS(_kr_sess), _api_id, _api_hash)
        elif (DATA_DIR / "tg_audio_session.session").exists():
            client = _TGC(str(DATA_DIR / "tg_audio_session"), _api_id, _api_hash)
        else:
            return
        await client.connect()
        if await client.is_user_authorized():
            sess_str = _SS.save(client.session)
            with _quality_session_lock:
                global _quality_session_string
                _quality_session_string = sess_str
            me = await client.get_me()
            with _auth_lock:
                _auth_state["step"]     = "done"
                _auth_state["username"] = me.first_name
            logger.info("Silent auth check OK: %s", me.first_name)
        await client.disconnect()
    except Exception as exc:
        logger.debug("Silent auth check failed: %s", exc)

def main():
    global SERVER

    # If we're here, a previous self-update (if any) succeeded — clear its
    # rollback backups and staging folder.
    _cleanup_update_leftovers()

    # If keyring storage is on but the session is still a plaintext file, move it
    # into the OS keyring and delete the file (no-op unless use_keyring is set).
    try:
        tgd_common.migrate_session_to_keyring()
    except Exception as exc:
        logger.debug("Session keyring migration skipped: %s", exc)

    # ── Single-instance guard ─────────────────────────────────────────────
    # We bind a private "lock" port.  If it's already taken, another instance
    # is running — just focus its browser window and exit cleanly.
    import socket as _socket
    _lock_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    _lock_sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    try:
        _lock_sock.bind(("127.0.0.1", LOCK_PORT))
        # Success — we are the first/only instance.  Keep _lock_sock open so
        # the port stays bound for the lifetime of this process.
    except OSError:
        logger.info("Another instance already running — opening browser")
        if not os.environ.get("TGD_NO_BROWSER"):
            if not _open_app_window(f"http://127.0.0.1:{HTTP_PORT}/"):
                webbrowser.open(f"http://127.0.0.1:{HTTP_PORT}/")
        sys.exit(0)

    if not GUI_HTML.exists():
        print(f"ERROR: gui.html not found at {GUI_HTML}", file=sys.stderr)
        sys.exit(1)

    # One-time library reorganisation: nest artist folders under Artists/.
    try:
        _m   = _tgd_import()
        _cfg = _m.load_config()
        _hm  = _cfg.get("home_music_folder")
        if _hm:
            from pathlib import Path as _P
            _m.migrate_artists_layout(_P(_hm))
    except Exception:
        logger.exception("Artists-layout migration at startup failed")

    SERVER = Server(("127.0.0.1", HTTP_PORT), Handler)
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()
    # Restore session on startup so quality works without pressing TG button
    asyncio.run_coroutine_threadsafe(_silent_auth_check(), _tg_loop)
    # Resume opt-in background integrations if the user left them enabled
    try:
        _wcfg = _tgd_import().load_config()
        if _wcfg.get("watch_library"):
            _apply_lib_watcher(_wcfg)
        if _wcfg.get("discord_rich_presence") and _wcfg.get("discord_client_id"):
            _presence_apply(_wcfg)
    except Exception:
        logger.exception("Could not resume background integrations at startup")

    url = f"http://127.0.0.1:{HTTP_PORT}/"
    logger.info("Server listening on %s", url)
    print(f"TGDownloader GUI  →  {url}")
    print("Ctrl+C to quit.\n")
    # TGD_NO_BROWSER=1 → headless mode (tests, previews, remote use)
    if not os.environ.get("TGD_NO_BROWSER"):
        if not _open_app_window(url):
            webbrowser.open(url)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nShutting down.")
        logger.info("KeyboardInterrupt — shutting down")
        MANAGER.stop()
        SERVER.shutdown()
        os._exit(0)


if __name__ == "__main__":
    main()