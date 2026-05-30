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
import struct
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from base64 import b64encode
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, quote as url_quote, unquote_plus


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

# ── Auto-update (notify-only) ─────────────────────────────────────────────────
# The GUI polls GitHub Releases for a newer tag and shows a banner. It never
# downloads or replaces files — the user updates manually from the release page.
APP_VERSION  = "1.1.0"                        # bump this when you cut a new release
GITHUB_REPO  = "Thurlws/TGDownloader"         # owner/repo the update check targets

# Backend process command
if getattr(sys, "frozen", False):
    _BACKEND_CMD: list[str] = [sys.executable, "--backend"]
else:
    _BACKEND_PY  = BUNDLE_DIR / "TGDownloader.py"
    _BACKEND_CMD = [sys.executable, "-u", str(_BACKEND_PY)]

HTTP_PORT = 7842
LOCK_PORT = 7843   # single-instance sentinel — we bind this; nobody else does

logger = logging.getLogger("gui_server")


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
    data = {
        "current":          APP_VERSION,
        "latest":           latest_tag.lstrip("vV") or latest_tag,
        "update_available": available,
        "url":              rel.get("html_url", f"https://github.com/{GITHUB_REPO}/releases"),
        "name":             rel.get("name") or latest_tag,
        "notes":            (rel.get("body") or "")[:4000],
        "published_at":     rel.get("published_at", ""),
    }
    _UPDATE_CACHE.update(ts=now, data=data)
    return data


def _load_api_credentials() -> "tuple[int | None, str | None]":
    """Read API_ID and API_HASH from tg_audio_config.json.

    Returns (api_id, api_hash) or (None, None) if not yet configured.
    Called at startup AND before each Telegram operation so credentials set
    via the wizard take effect without restarting the server.
    """
    cfg_path = DATA_DIR / "tg_audio_config.json"
    if not cfg_path.exists():
        return None, None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        api_id   = cfg.get("api_id")
        api_hash = cfg.get("api_hash", "")
        if api_id and api_hash:
            return int(api_id), str(api_hash)
    except Exception:
        pass
    return None, None


def _credentials_configured() -> bool:
    """True when valid-looking API credentials AND bot_username exist in config."""
    api_id, api_hash = _load_api_credentials()
    if not (api_id and api_hash and len(str(api_hash)) == 32):
        return False
    cfg_path = DATA_DIR / "tg_audio_config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        return bool(cfg.get("bot_username", "").strip())
    except Exception:
        return False


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
    """Read the Telethon SQLite session and return it as a StringSession string.
    Uses raw sqlite3 with WAL mode so it can read even while the downloader has
    the file open for writes.
    Fixed: correct column name (server_address) and Telethon struct layout."""
    import sqlite3 as _sq3, struct as _st, base64 as _b64, socket as _sock
    db = DATA_DIR / "tg_audio_session.session"
    if not db.exists():
        return None
    try:
        conn = _sq3.connect(str(db), timeout=8.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute(
            "SELECT dc_id, server_address, port, auth_key FROM sessions"
        ).fetchone()
        conn.close()
        if not row:
            return None
        dc_id, server, port, auth_key = row
        # Telethon StringSession wire format: dc_id(B) + ip(4s) + port(H) + auth_key(256s), prefixed "1"
        ip_bytes = _sock.inet_aton(server)
        data = _st.pack(">B4sH256s", dc_id, ip_bytes, port, bytes(auth_key))
        return "1" + _b64.urlsafe_b64encode(data).decode("ascii")
    except Exception as exc:
        logger.debug("Could not read session string from file: %s", exc)
        return None


def _get_quality_session() -> "str | None":
    """Return a cached StringSession string, loading it if needed."""
    global _quality_session_string
    with _quality_session_lock:
        if not _quality_session_string:
            _quality_session_string = _read_session_string_from_file()
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

        session_file = str(DATA_DIR / "tg_audio_session")
        _api_id, _api_hash = _load_api_credentials()
        client = _TGClient(session_file, _api_id, _api_hash)
        await client.start(
            phone=_get_phone,
            code_callback=_get_code,
            password=_get_pw,
        )
        me = await client.get_me()
        # Cache as StringSession so quality checks never need to open the SQLite file
        try:
            from telethon.sessions import StringSession as _SS
            _sess_str = _SS.save(client.session)
            with _quality_session_lock:
                global _quality_session_string
                _quality_session_string = _sess_str
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
    cfg_path = DATA_DIR / "tg_audio_config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        return {"quality": cfg.get("target_quality", "FLAC")}
    except Exception as exc:
        return {"error": str(exc)}


async def _tg_set_quality(target: str) -> dict:
    """Write quality setting to local config (no bot comms)."""
    cfg_path = DATA_DIR / "tg_audio_config.json"
    try:
        cfg = {}
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["target_quality"] = target
        cfg_path.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
        )
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
                    if line.startswith("##RESULT## "):
                        try:
                            result = json.loads(line[11:])
                            self.broadcast({"type": "result", **result})
                        except Exception:
                            pass
                        continue
                    self.broadcast({"type": "log", "text": line})
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

# ── Library tab caches (process lifetime) ─────────────────────────────────────
_cover_cache:         dict[str, str]          = {}   # deezer album_id → cover_medium URL
_path_hash_map:       dict[str, "Path"]       = {}   # path_hash[:16] → album_dir Path
_local_cover_cache:   dict[str, tuple]        = {}   # path_hash → (bytes, mime_type)
_track_cover_cache:   dict[str, "tuple | None"] = {} # path_hash\x00name → (bytes, mime)|None
_artist_cache:        dict[str, dict]         = {}   # deezer artist_id → artist metadata
_album_artist_id:     dict[str, str]          = {}   # deezer album_id  → artist_id
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


def _scan_genres(home_path: Path) -> "dict[str, list[dict]]":
    """Walk every artist/album/track in the library and group local audio files
    by their genre tag (read via Mutagen).  Returns { genre: [ {path, artist,
    album, title, track_num} ] }.  Files with no genre tag are skipped."""
    from mutagen import File as _MF
    import re as _re

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
                lines   = ["1", str(len(entries))]
                for e in entries:
                    lines.append(e.get("url", ""))
                    lines.append(e.get("artist", ""))
                    # 3rd line per entry: playlist name (empty = not a playlist)
                    pl = e.get("playlistName", "") if e.get("isPlaylist") else ""
                    lines.append(pl.replace("\n", " ").strip())
                lines.append("Y")
                MANAGER.start("\n".join(lines) + "\n")
                MANAGER.broadcast({"type": "status", "running": True})

            elif action == "stop":
                MANAGER.stop()

            elif action == "pause":
                try: (DATA_DIR / "pause.flag").touch()
                except Exception: pass
                MANAGER.broadcast({"type": "log", "text": "##PAUSED##\n"})

            elif action == "resume":
                try: (DATA_DIR / "pause.flag").unlink(missing_ok=True)
                except Exception: pass
                MANAGER.broadcast({"type": "log", "text": "##RESUMED##\n"})

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

    # ── GET ────────────────────────────────────────────────────────────────

    def do_GET(self):
        path = urlparse(self.path).path

        # WebSocket upgrade
        if self.headers.get("Upgrade", "").lower() == "websocket":
            key = self.headers.get("Sec-WebSocket-Key", "")
            handle_ws(self.connection, key)
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

        if path == "/search":
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
                self._send_json(200, _deezer_search(query))
            except Exception as exc:
                logger.warning("Deezer search failed: %s", exc)
                self._send_json(500, {"error": str(exc)})
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

        # Telegram session status — file check only, no network call
        if path == "/telegram-status":
            session_exists = (DATA_DIR / "tg_audio_session.session").exists()
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

                AUDIO_EXT = {".mp3",".flac",".ogg",".opus",".m4a",".aac",
                             ".wav",".aif",".aiff",".wma",".ape",".wv"}
                total_files = total_bytes = album_count = 0
                artist_stats: dict = {}   # artist -> {tracks, bytes}
                orphaned: list = []

                for f in home_path.rglob("*"):
                    if not (f.is_file() and f.suffix.lower() in AUDIO_EXT):
                        continue
                    total_files += 1
                    sz           = f.stat().st_size
                    total_bytes += sz

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

                self._send_json(200, {
                    "total_files":           total_files,
                    "total_bytes":           total_bytes,
                    "artist_count":          len(artist_stats),
                    "album_count":           album_count,
                    "avg_tracks_per_album":  avg_tracks,
                    "artists_by_tracks":     artists_by_tracks[:20],
                    "orphaned":              orphaned,
                    "download_history_summary": history_summary,
                })
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
                album_dir = _path_hash_map.get(ph)
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
            album_dir = _path_hash_map.get(ph)
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
            album_dir = _path_hash_map.get(ph)
            if not album_dir:
                self.send_error(404)
                return
            file_path = (album_dir / filename).resolve()
            try:
                file_path.relative_to(album_dir.resolve())
            except ValueError:
                self.send_error(403)
                return
            if not file_path.exists() or not file_path.is_file():
                self.send_error(404)
                return
            ext = file_path.suffix.lower()
            mime_map = {
                ".mp3":  "audio/mpeg",  ".flac": "audio/flac",
                ".ogg":  "audio/ogg",   ".opus": "audio/ogg",
                ".m4a":  "audio/mp4",   ".aac":  "audio/aac",
                ".wav":  "audio/wav",   ".aif":  "audio/aiff",
                ".aiff": "audio/aiff",  ".wma":  "audio/x-ms-wma",
                ".ape":  "audio/ape",   ".wv":   "audio/wavpack",
            }
            mime      = mime_map.get(ext, "audio/mpeg")
            file_size = file_path.stat().st_size
            # Range request support so the browser can seek freely
            range_header = self.headers.get("Range", "")
            start, end = 0, file_size - 1
            if range_header.startswith("bytes="):
                try:
                    parts = range_header[6:].split("-")
                    start = int(parts[0]) if parts[0] else 0
                    end   = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
                    end   = min(end, file_size - 1)
                except (ValueError, IndexError):
                    start, end = 0, file_size - 1
            length = end - start + 1
            self.send_response(206 if range_header else 200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", length)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            if range_header:
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.end_headers()
            try:
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(65536, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        self.send_error(404)

    def do_POST(self):
        path   = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

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

            cfg_path = DATA_DIR / "tg_audio_config.json"
            cfg = {}
            if cfg_path.exists():
                try:
                    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            cfg["api_id"]      = int(api_id)
            cfg["api_hash"]    = api_hash
            cfg["bot_username"] = bot_username
            try:
                cfg_path.write_text(
                    json.dumps(cfg, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
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
            self._send_json(200, {"ok": True})
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
                    import shutil as _shutil
                    _shutil.rmtree(str(artist_dir))
                    logger.info("Deleted artist folder: %s", artist_dir)
                    self._send_json(200, {"ok": True})
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
                    import shutil as _shutil
                    _shutil.rmtree(str(album_dir))
                    logger.info("Deleted album folder: %s", album_dir)
                    self._send_json(200, {"ok": True})
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

        # ── Toggle a song's "liked" state ─────────────────────────────────
        if path == "/toggle-like":
            try:
                result = _toggle_liked(body or {})
                self._send_json(400 if result.get("error") else 200, result)
            except Exception as exc:
                logger.exception("Error in /toggle-like")
                self._send_json(500, {"error": str(exc)})
            return

        # ── Native Windows folder picker ──────────────────────────────────
        if path == "/browse-folder":
            current = body.get("current", "")
            chosen  = _pick_folder_native(current)
            self._send_json(200, {"path": chosen})
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
                session_path = DATA_DIR / "tg_audio_session.session"
                try:
                    session_path.unlink(missing_ok=True)
                    # Also wipe any .session-journal
                    for f in DATA_DIR.glob("tg_audio_session*"):
                        f.unlink(missing_ok=True)
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
    """On startup, reuse an existing SQLite session without any prompts.
    Populates _quality_session_string and _auth_state so the quality badge
    works immediately without pressing the TG button."""
    db = DATA_DIR / "tg_audio_session.session"
    if not db.exists():
        return
    try:
        if str(BUNDLE_DIR) not in sys.path:
            sys.path.insert(0, str(BUNDLE_DIR))
        from telethon import TelegramClient as _TGC
        from telethon.sessions import StringSession as _SS
        _api_id, _api_hash = _load_api_credentials()
        if not _api_id:
            return
        client = _TGC(str(DATA_DIR / "tg_audio_session"), _api_id, _api_hash)
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

    url = f"http://127.0.0.1:{HTTP_PORT}/"
    logger.info("Server listening on %s", url)
    print(f"TGDownloader GUI  →  {url}")
    print("Ctrl+C to quit.\n")
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