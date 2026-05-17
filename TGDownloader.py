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
Telegram Audio Downloader + Album Sorter  v6
---------------------------------------------
Run directly (CLI) or launched by TGDownloader_bundled.py / TGDownloader_GUI.py.

Changes in v6:
  • CONFIG_FILE and SESSION_FILE now respect the TGD_DATA_DIR environment
    variable so that, when running as a PyInstaller bundle, writable files
    are placed beside the .exe rather than inside the read-only bundle.
  • All other behaviour is identical to v5.

Requirements:
    pip install telethon mutagen cryptg
"""

import asyncio
import concurrent.futures
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import traceback
import threading
import tkinter
import tkinter.filedialog
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from mutagen import File as MutagenFile          # type: ignore
from telethon import TelegramClient, events       # type: ignore
from telethon.sessions import StringSession       # type: ignore
from telethon.tl.types import DocumentAttributeFilename  # type: ignore

# ──────────────────────────────────────────────
#  API CREDENTIALS  (read from config, not hardcoded)
# ──────────────────────────────────────────────

def _get_api_creds() -> "tuple[int, str]":
    """Read API_ID and API_HASH from tg_audio_config.json at call time.

    Raises RuntimeError if credentials are missing — complete the setup
    wizard first.
    """
    cfg_path = _DATA_DIR / "tg_audio_config.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        api_id   = cfg.get("api_id")
        api_hash = cfg.get("api_hash", "")
        if api_id and api_hash:
            return int(api_id), str(api_hash)
    except Exception:
        pass
    raise RuntimeError(
        "Telegram API credentials not found.\n"
        "Run TGDownloader and complete the setup wizard first."
    )

# ──────────────────────────────────────────────
#  DATA DIRECTORY  (v6 addition)
#  When bundled with PyInstaller the env-var points to the folder that
#  contains the .exe so user data doesn't end up in a read-only temp dir.
# ──────────────────────────────────────────────
_DATA_DIR = Path(os.environ.get("TGD_DATA_DIR", Path(__file__).parent))
_DATA_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────────────────────────────────────
#  DEFAULT CONFIG
# ──────────────────────────────────────────────
DEFAULT_CONFIG: dict = {
    "home_music_folder":       None,
    "bot_username":            "",  # must be set by user via setup wizard
    "reply_timeout":           30,
    "queue_wait_timeout":      120,
    # Idle timeout after the last received audio file.  8 s is generous enough
    # to handle a slow bot but short enough that the delay is barely noticeable.
    # With the count-match fix this timeout is only hit when the bot sends fewer
    # files than advertised (partial album), so it rarely fires at all.
    "inter_file_idle_timeout": 8,
    "idle_check_interval":     0.5,
    "bot_busy_wait":           10,
    "bot_busy_retries":        12,
    "max_queue":               10,
    "ui_scale":                1.0,
    "target_quality":          "FLAC",  # FLAC | MP3 320 | MP3 128
    # Maximum number of tracks downloaded in parallel per album.
    # Keeping this at 3 prevents ExportAuthorization flood-waits from
    # Telegram when many tracks on a non-home DC are authorised at once.
    "max_parallel_downloads":  3,
}

BOT_BUSY_PHRASES = [
    "please wait",
    "wait for the current",
    "download to complete",
    "already processing",
    "busy",
]

UNKNOWN_ALBUM    = "_Unknown Album"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg", ".opus", ".m4a", ".aac",
                    ".wav", ".aif", ".aiff", ".wma", ".ape", ".wv"}

# Minimum similarity ratio (0–1) for fuzzy artist/album folder matching.
# 0.82 catches common variants ("AC DC" ≈ "ACDC", "Taylor Swift" ≈ "Taylor_Swift")
# while avoiding false positives between different artists.
FUZZY_THRESHOLD = 0.82

# v6: use _DATA_DIR so these files sit beside the .exe when frozen
CONFIG_FILE      = _DATA_DIR / "tg_audio_config.json"
SESSION_FILE     = str(_DATA_DIR / "tg_audio_session")
_BOT_INIT_FLAG   = _DATA_DIR / "bot_initialized.flag"   # written once after first-run setup
_HASH_CACHE_FILE = _DATA_DIR / "hash_cache.json"        # path → {mtime, size, hash}

_print_lock = Lock()
_progress: dict[str, tuple[int, int]] = {}
_pause_event = __import__("threading").Event()  # set = paused

_TOTAL_TRACKS_RE   = re.compile(r"total\s+tracks?\s*:?\s*(\d+)", re.IGNORECASE)
_TRACK_PROGRESS_RE = re.compile(r"track\s+\d+\s+of\s+(\d+)",    re.IGNORECASE)


# ═════════════════════════════════════════════
#  DATA CLASSES
# ═════════════════════════════════════════════

@dataclass
class URLEntry:
    url:    str
    artist: str


@dataclass
class URLResult:
    url:           str
    artist:        str
    expected:      int | None
    downloaded:    int
    dupes_skipped: int
    dest:          Path | None
    status:        str        # "ok" | "partial" | "skipped" | "error"
    error:         str = ""


# ═════════════════════════════════════════════
#  CONFIG
# ═════════════════════════════════════════════

def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update(saved)
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_FILE.write_text(
            json.dumps(cfg, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        _log(f"WARNING: Could not save config: {e}")


def configure_settings(cfg: dict) -> dict:
    """Interactive CLI config editor."""
    print(f"\n  {'─'*44}")
    print("  CONFIGURE SETTINGS")
    print(f"  {'─'*44}")
    print("  Press Enter to keep the current value.\n")

    editable = [
        ("bot_username",            "Bot username",               str),
        ("reply_timeout",           "Reply timeout (s)",          int),
        ("queue_wait_timeout",      "Queue wait timeout (s)",     int),
        ("inter_file_idle_timeout", "Idle timeout after files (s)", int),
        ("idle_check_interval",     "Idle check interval (s)",    float),
        ("bot_busy_wait",           "Bot busy wait (s)",          int),
        ("bot_busy_retries",        "Bot busy retries",           int),
        ("max_queue",               "Max URLs per session",       int),
    ]

    for key, label, cast in editable:
        current = cfg.get(key, DEFAULT_CONFIG.get(key, ""))
        raw = input(f"  {label} [{current}]: ").strip()
        if raw:
            try:
                cfg[key] = cast(raw)
            except ValueError:
                print(f"    Invalid value — keeping {current}")

    hf = cfg.get("home_music_folder")
    print(f"\n  Home music folder: {hf or 'Not set'}")
    if input("  Change? (y/N): ").strip().lower() == "y":
        cfg["home_music_folder"] = None

    save_config(cfg)
    print("  Settings saved.\n")
    return cfg


# ═════════════════════════════════════════════
#  MANIFEST
# ═════════════════════════════════════════════

def _manifest_path(home: Path) -> Path:
    d = home / ".tgdownloader"
    d.mkdir(parents=True, exist_ok=True)
    return d / "manifest.json"


def load_manifest(home: Path) -> dict:
    mp = _manifest_path(home)
    if mp.exists():
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except Exception:
            pass

    old = home / "tg_download_manifest.json"
    if old.exists():
        try:
            data = json.loads(old.read_text(encoding="utf-8"))
            _log("  Migrating manifest to .tgdownloader/manifest.json …")
            save_manifest(home, data)
            old.unlink()
            return data
        except Exception:
            pass

    return {}


def save_manifest(home: Path, manifest: dict) -> None:
    try:
        _manifest_path(home).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        _log(f"WARNING: Could not save manifest: {e}")


def mark_url_complete(
    home: Path, manifest: dict,
    url: str, artist: str, files: list[str],
    albums: list[str] | None = None,
) -> None:
    manifest[url] = {
        "status":    "complete",
        "artist":    artist,
        "files":     files,
        "albums":    albums or [],
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    save_manifest(home, manifest)


def is_url_complete(
    manifest: dict,
    url: str,
    home: Path | None = None,
) -> bool:
    entry = manifest.get(url, {})
    if entry.get("status") != "complete":
        return False

    if home is None:
        return True

    files  = entry.get("files", [])
    artist = entry.get("artist", "")

    if not files:
        return True

    artist_dir = home / _sanitise_path(artist)
    if not artist_dir.exists():
        _log(
            f"  Manifest: artist folder '{artist_dir.name}' not found — "
            "treating URL as incomplete."
        )
        return False

    missing = [f for f in files if not _exists_in_tree(f, artist_dir)]
    if missing:
        _log(
            f"  Manifest: {len(missing)}/{len(files)} file(s) missing on disk "
            f"for '{artist}' — will re-download."
        )
        return False

    return True


# ═════════════════════════════════════════════
#  FOLDER PICKER
# ═════════════════════════════════════════════

def pick_folder(title: str = "Select folder") -> Path:
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    chosen = tkinter.filedialog.askdirectory(title=title)
    root.destroy()
    if not chosen:
        sys.exit("No folder selected — exiting.")
    return Path(chosen)


def get_home_music_folder(cfg: dict) -> Path:
    saved = cfg.get("home_music_folder")
    if saved:
        home = Path(saved)
        if home.exists():
            _log(f"Home music folder: {home}")
            return home
        _log(f"WARNING: Saved home folder no longer exists: {home}")

    _log("No home music folder configured.")
    _log("A folder picker will open — select your HOME MUSIC folder.")
    home = pick_folder("Select your Home Music Folder")
    cfg["home_music_folder"] = str(home)
    save_config(cfg)
    _log(f"Home music folder saved: {home}")
    return home


# ═════════════════════════════════════════════
#  LOGGING
# ═════════════════════════════════════════════

def _log(msg: str) -> None:
    print(msg, flush=True)


def _check_pause_flag() -> None:
    """Pause if DATA_DIR/pause.flag exists; emit ##PAUSED## / ##RESUMED## signals."""
    flag = _DATA_DIR / "pause.flag"
    was_paused = _pause_event.is_set()
    if flag.exists():
        if not was_paused:
            _pause_event.set()
            print("##PAUSED##", flush=True)
    else:
        if was_paused:
            _pause_event.clear()
            print("##RESUMED##", flush=True)


# ═════════════════════════════════════════════
#  STRUCTURED RESULT OUTPUT  (parsed by GUI)
# ═════════════════════════════════════════════

def _emit_result(
    url: str,
    artist: str,
    status: str,
    downloaded: int = 0,
    expected: int | None = None,
    dupes_skipped: int = 0,
    error: str = "",
) -> None:
    payload = {
        "url":           url,
        "artist":        artist,
        "status":        status,
        "downloaded":    downloaded,
        "expected":      expected,
        "dupes_skipped": dupes_skipped,
        "error":         error,
    }
    print(f"##RESULT## {json.dumps(payload)}", flush=True)


# ═════════════════════════════════════════════
#  PROGRESS DISPLAY
# ═════════════════════════════════════════════

def _render_progress(start_time: float, total_files: int, done_files: int) -> str:
    elapsed     = max(time.monotonic() - start_time, 0.001)
    total_bytes = sum(t for _, t in _progress.values())
    done_bytes  = sum(d for d, _ in _progress.values())
    speed_mb    = done_bytes / elapsed / 1_048_576
    pct         = done_bytes / total_bytes if total_bytes > 0 else 0
    bar         = "█" * int(pct * 28) + "░" * (28 - int(pct * 28))
    eta         = int((1 - pct) / (pct / elapsed)) if 0 < pct < 1 else 0
    eta_str     = f"ETA ~{eta}s" if done_files < total_files else "done"
    return (
        f"\r  [{bar}] {done_files}/{total_files} files  "
        f"{done_bytes/1_048_576:.1f}/{total_bytes/1_048_576:.1f} MB  "
        f"{speed_mb:.2f} MB/s  {eta_str}  "
    )


# ═════════════════════════════════════════════
#  HELPERS
# ═════════════════════════════════════════════

def _get_filename(message) -> str:
    for attr in message.document.attributes:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return f"audio_{message.document.id}.mp3"


def _sanitise_path(name: str) -> str:
    cleaned = "".join(c if c not in r'\/:*?"<>|' else "_" for c in name)
    return cleaned.strip(". ") or "_unnamed"


def _sanitise_filename(name: str) -> str:
    cleaned = "".join(c if c not in r'\/:*?"<>|' else "_" for c in name)
    cleaned = re.sub(r"[ _]{2,}", " ", cleaned)
    return cleaned.strip(". ") or "audio"


def _parse_total_tracks(text: str) -> int | None:
    for pattern in (_TOTAL_TRACKS_RE, _TRACK_PROGRESS_RE):
        m = pattern.search(text)
        if m:
            return int(m.group(1))
    return None


def _is_bot_busy(text: str) -> bool:
    return any(phrase in text.lower() for phrase in BOT_BUSY_PHRASES)


def _check_cryptg() -> None:
    try:
        import cryptg  # noqa: F401  # type: ignore
    except ImportError:
        _log(
            "\nWARNING: `cryptg` is not installed.\n"
            "   Telethon falls back to pure-Python AES (pyaes):\n"
            "     - Crashes on Python 3.14\n"
            "     - Is 50-100x slower\n"
            "   Fix:  pip install cryptg\n"
        )


def _exists_in_tree(filename: str, root: Path) -> bool:
    return any(True for _ in root.rglob(filename))


# ═════════════════════════════════════════════
#  DUPLICATE DETECTION
# ═════════════════════════════════════════════

def _fuzzy_score(a: str, b: str) -> float:
    """SequenceMatcher similarity ratio of two strings (case-insensitive)."""
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _fuzzy_match_dir(name: str, parent: Path,
                     threshold: float = FUZZY_THRESHOLD) -> Path | None:
    """Return the best-matching child directory of *parent* for *name*.

    Checks sanitised-exact match first, then falls back to fuzzy scoring.
    Returns None when no directory scores above *threshold*.
    """
    if not parent.is_dir():
        return None
    san = _sanitise_path(name)
    best_score, best = 0.0, None
    for d in parent.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        if d.name == san:                          # exact sanitised match — done
            return d
        score = max(
            _fuzzy_score(d.name, name),
            _fuzzy_score(d.name, san),
        )
        if score > best_score:
            best_score, best = score, d
    return best if (best and best_score >= threshold) else None


def _audio_hash(path: Path) -> str:
    """MD5 of the raw file bytes — fast, catches byte-level duplicates."""
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_library_hash_index(home: Path) -> set[str]:
    """Scan every audio file under *home* and return the set of MD5 hashes.

    Uses a persistent disk cache keyed by (path, mtime, size) so that only
    new or modified files are re-hashed.  On a library that has not changed
    since the last run this returns in milliseconds regardless of library size.

    The cache is stored at _HASH_CACHE_FILE next to tg_audio_config.json.
    The resulting set is passed to :func:`sort_by_album` so newly downloaded
    files can be skipped when their hash is already present.
    """
    # Load existing cache: str(path) -> {"mtime": float, "size": int, "hash": str}
    old_cache: dict = {}
    if _HASH_CACHE_FILE.exists():
        try:
            old_cache = json.loads(_HASH_CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            old_cache = {}

    hashes:    set[str] = set()
    new_cache: dict     = {}
    total    = 0
    rehashed = 0

    for p in home.rglob("*"):
        if not (p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS):
            continue
        key = str(p)
        try:
            st    = p.stat()
            mtime = st.st_mtime
            size  = st.st_size
        except OSError:
            continue

        cached = old_cache.get(key)
        if (cached
                and cached.get("mtime") == mtime
                and cached.get("size")  == size):
            h = cached["hash"]          # cache hit — no disk read needed
        else:
            try:
                h = _audio_hash(p)      # cache miss — read and hash the file
                rehashed += 1
            except OSError:
                continue

        hashes.add(h)
        new_cache[key] = {"mtime": mtime, "size": size, "hash": h}
        total += 1

    # Persist refreshed cache for next run
    try:
        _HASH_CACHE_FILE.write_text(
            json.dumps(new_cache, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        _log(f"  WARNING: Could not save hash cache: {e}")

    _log(
        f"  Library index: {total} file(s) — "
        f"{total - rehashed} from cache, {rehashed} re-hashed, "
        f"{len(hashes)} unique hash(es)."
    )
    return hashes


def check_pre_download(entry: "URLEntry", home: Path) -> None:
    """Log warnings about potential duplicates *before* downloading.

    Two checks are performed:
    1. Fuzzy artist folder match — warns when an existing folder is very
       similar to the requested artist name (e.g. "AC_DC" vs "AC DC").
    2. Fuzzy album folder match — if the matched artist folder already
       contains an album whose name closely resembles the URL's Deezer
       album title (extracted from the URL path, best-effort).
    """
    artist_dir = _fuzzy_match_dir(entry.artist, home)
    if artist_dir is None:
        return                                     # no match — nothing to warn about

    san = _sanitise_path(entry.artist)
    if artist_dir.name != san:
        _log(
            f"  ⚠  Fuzzy artist match: '{entry.artist}' ≈ existing folder "
            f"'{artist_dir.name}' — new files will be merged into it."
        )

    # Best-effort: extract album title slug from Deezer URL (e.g. /album/12345)
    # Deezer URLs don't carry the album title in the path, so we can only
    # check when the user has embedded a human-readable slug (rare).
    # We still scan album subdirs and report the count so the user is aware.
    existing_albums = [d.name for d in artist_dir.iterdir() if d.is_dir()]
    if existing_albums:
        _log(
            f"  ℹ  Artist folder '{artist_dir.name}' already contains "
            f"{len(existing_albums)} album folder(s): "
            + ", ".join(f"'{a}'" for a in existing_albums[:4])
            + (" …" if len(existing_albums) > 4 else "")
        )


# ═════════════════════════════════════════════
#  ALBUM SORTING
# ═════════════════════════════════════════════

def _get_album(path: Path) -> str:
    try:
        audio = MutagenFile(path, easy=True)
        if audio is None:
            return UNKNOWN_ALBUM
        tag = audio.get("album")
        if tag and str(tag[0]).strip():
            return str(tag[0]).strip()
    except Exception:
        pass
    return UNKNOWN_ALBUM


def sort_by_album(source: Path, dest: Path,
                  hash_index: set[str] | None = None) -> tuple[int, list[str]]:
    _log(f"\n{'─'*50}")
    _log("  SORTING BY ALBUM")
    _log(f"{'─'*50}\n")

    files: list[Path] = []
    for p in source.rglob("*"):
        if not (p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS):
            continue
        clean_name = _sanitise_filename(p.name)
        if clean_name != p.name:
            new_path = p.parent / clean_name
            p.rename(new_path)
            p = new_path
        files.append(p)

    if not files:
        _log("  No audio files found to sort.")
        return 0, []

    groups: dict[str, list[Path]] = {}
    for f in files:
        key = _sanitise_path(_get_album(f))
        groups.setdefault(key, []).append(f)

    _log(f"  {len(files)} file(s) across {len(groups)} album(s):\n")
    for album, tracks in sorted(groups.items()):
        marker = "  [no tag]" if album == UNKNOWN_ALBUM else ""
        _log(f"    {album}{marker}  ({len(tracks)} track(s))")

    _log("")
    dest.mkdir(parents=True, exist_ok=True)

    moved = errors = dupes = 0
    for album, tracks in sorted(groups.items()):
        # Fuzzy-match an existing album folder so tracks land in the right place
        # even when the bot returns a slightly different album name.
        album_dir_exact = dest / album
        album_dir = _fuzzy_match_dir(album, dest) or album_dir_exact
        if album_dir != album_dir_exact and album_dir.name != album:
            _log(f"    Fuzzy album match: '{album}' → existing folder '{album_dir.name}'")
        album_dir.mkdir(parents=True, exist_ok=True)

        for track in tracks:
            # ── 1. Filename-based duplicate check (fast, existing behaviour) ──
            if dest.exists() and _exists_in_tree(track.name, dest):
                _log(f"    SKIP (filename dupe)  {track.name}")
                dupes += 1
                track.unlink(missing_ok=True)
                continue

            # ── 2. Audio-hash duplicate check (catches renamed duplicates) ────
            if hash_index is not None:
                try:
                    file_hash = _audio_hash(track)
                    if file_hash in hash_index:
                        _log(f"    SKIP (hash dupe)      {track.name}")
                        dupes += 1
                        track.unlink(missing_ok=True)
                        continue
                except OSError as exc:
                    _log(f"    WARN  Could not hash {track.name}: {exc}")

            target  = album_dir / track.name
            counter = 1
            while target.exists():
                target = album_dir / f"{track.stem} ({counter}){track.suffix}"
                counter += 1
            try:
                shutil.move(str(track), str(target))
                # Register the newly moved file in the index so subsequent
                # tracks in the same batch can't collide with it.
                if hash_index is not None:
                    try:
                        hash_index.add(_audio_hash(target))
                    except OSError:
                        pass
                moved += 1
            except Exception as e:
                _log(f"    ERROR  {track.name}: {e}")
                errors += 1

    msg = f"  Sorted {moved} file(s)."
    if dupes:
        msg += f"  {dupes} duplicate(s) skipped."
    if errors:
        msg += f"  {errors} error(s)."
    _log(msg)

    real_albums = [a for a in groups.keys() if a != _sanitise_path(UNKNOWN_ALBUM)]
    return dupes, real_albums



# ═════════════════════════════════════════════
#  FFMPEG QUALITY CONVERSION
# ═════════════════════════════════════════════

def _check_ffmpeg() -> bool:
    """Return True if ffmpeg is available on PATH."""
    import shutil as _shutil
    return _shutil.which("ffmpeg") is not None


def _needs_conversion(path: Path, target_quality: str) -> bool:
    """Return True when *path* is not already in the target format/quality."""
    ext = path.suffix.lower()
    if target_quality == "FLAC":
        return ext != ".flac"
    else:
        # MP3 320 or MP3 128 — any non-mp3 needs conversion;
        # mp3 files are passed through without re-encoding to avoid quality loss.
        return ext != ".mp3"


def _ffmpeg_convert(src: Path, target_quality: str) -> "Path | None":
    """Convert *src* to *target_quality* using ffmpeg.  Returns the new path
    on success, None on failure.  The original file is deleted on success."""
    import subprocess as _sp

    if target_quality == "FLAC":
        dst = src.with_suffix(".flac")
        cmd = ["ffmpeg", "-y", "-i", str(src),
               "-c:a", "flac", "-compression_level", "8",
               # Copy all metadata tags
               "-map_metadata", "0",
               str(dst)]
    elif target_quality == "MP3 320":
        dst = src.with_suffix(".mp3")
        cmd = ["ffmpeg", "-y", "-i", str(src),
               "-c:a", "libmp3lame", "-b:a", "320k", "-q:a", "0",
               "-map_metadata", "0",
               str(dst)]
    elif target_quality == "MP3 128":
        dst = src.with_suffix(".mp3")
        cmd = ["ffmpeg", "-y", "-i", str(src),
               "-c:a", "libmp3lame", "-b:a", "128k", "-q:a", "2",
               "-map_metadata", "0",
               str(dst)]
    else:
        _log(f"  WARN  Unknown target quality '{target_quality}' — skipping conversion")
        return None

    # src and dst are the same path (e.g. mp3→mp3 same suffix) — skip
    if dst.resolve() == src.resolve():
        return src

    try:
        result = _sp.run(
            cmd,
            stdout=_sp.DEVNULL,
            stderr=_sp.PIPE,
            timeout=300,
        )
        if result.returncode != 0:
            err = result.stderr.decode("utf-8", errors="replace")[-300:]
            _log(f"  ERROR  ffmpeg failed for {src.name}: {err}")
            if dst.exists():
                dst.unlink(missing_ok=True)
            return None
        # Remove the original only after the destination exists and has size
        if dst != src and src.exists():
            src.unlink(missing_ok=True)
        return dst
    except FileNotFoundError:
        _log("  ERROR  ffmpeg not found — install ffmpeg and ensure it is on your PATH")
        return None
    except _sp.TimeoutExpired:
        _log(f"  ERROR  ffmpeg timed out converting {src.name}")
        return None


def convert_directory_quality(directory: Path, target_quality: str) -> tuple[int, int]:
    """Convert every audio file in *directory* (recursively) to *target_quality*.

    Returns (converted_count, error_count).
    No-ops when ffmpeg is unavailable or target_quality is already the source format.
    """
    if not _check_ffmpeg():
        _log("  WARNING: ffmpeg not found — audio quality conversion skipped.")
        _log("           Install ffmpeg and add it to PATH to enable conversion.")
        return 0, 0

    if not directory.is_dir():
        return 0, 0

    files = [
        p for p in directory.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    ]

    if not files:
        return 0, 0

    converted = errors = 0
    _log(f"\n  Converting {len(files)} file(s) → {target_quality} …")

    for f in files:
        if not _needs_conversion(f, target_quality):
            continue
        _log(f"  → {f.name}")
        result = _ffmpeg_convert(f, target_quality)
        if result is None:
            errors += 1
        else:
            converted += 1

    msg = f"  Conversion done: {converted} file(s) converted."
    if errors:
        msg += f"  {errors} error(s)."
    _log(msg)
    return converted, errors


# ═════════════════════════════════════════════
#  FIRST-RUN BOT SETUP
# ═════════════════════════════════════════════

async def _ensure_bot_initialized(client, cfg: dict) -> None:
    """First-run gate: if the bot requires channel membership, join the channel
    automatically, mute it permanently, and write a flag file so this logic is
    skipped on every subsequent launch.

    Flow (matches screenshot):
      1. Send /start to the bot.
      2. Scan the bot's reply for a "Join … Channel" URL button.
      3. Extract the t.me link, join the channel via JoinChannelRequest.
      4. Mute the channel permanently (mute_until = INT32_MAX).
      5. Touch _BOT_INIT_FLAG and log "Telegram bot ready to use".
    """
    if _BOT_INIT_FLAG.exists():
        # Already initialised on a previous launch — nothing to do.
        _log("  ✓ Telegram bot ready to use")
        return

    BOT_USERNAME = cfg["bot_username"]
    _log("\n  First-time setup — checking bot channel requirements…")

    try:
        bot_entity = await client.get_entity(BOT_USERNAME)

        # Trigger the bot's welcome / gate message
        await client.send_message(bot_entity, "/start")
        await asyncio.sleep(3)          # give the bot time to respond

        messages = await client.get_messages(bot_entity, limit=6)

        # Walk recent messages looking for the "Join … Channel" URL button
        join_url: str | None = None
        for msg in messages:
            if not msg.reply_markup:
                continue
            for row in msg.reply_markup.rows:
                for btn in row.buttons:
                    label = btn.text.lower()
                    if "join" in label and ("channel" in label or "update" in label):
                        url_attr = getattr(btn, "url", None)
                        if url_attr:
                            join_url = url_attr
                        break
                if join_url:
                    break
            if join_url:
                break

        if join_url is None:
            # Bot didn't present a join gate — already a member or gate absent
            _log("  No channel join required.")
        else:
            _log(f"  Bot requires channel membership → {join_url}")

            # Distinguish private invite links (t.me/+HASH  or  t.me/joinchat/HASH)
            # from public channel usernames (t.me/username).
            # Private links must use ImportChatInviteRequest(hash) — calling
            # get_entity() on the bare hash string raises "Cannot find any entity".
            _m_priv = re.search(
                r"t\.me/(?:joinchat/|\+)([^/?#]+)", join_url, re.IGNORECASE
            )
            _m_pub  = re.search(
                r"t\.me/([^/?#+][^/?#]*)", join_url, re.IGNORECASE
            )

            from telethon.tl.functions.account import UpdateNotifySettingsRequest
            from telethon.tl.types import InputNotifyPeer, InputPeerNotifySettings

            _joined_entity = None   # holds the channel object after a successful join

            if _m_priv:
                # ── Private invite link ──────────────────────────────────────
                invite_hash = _m_priv.group(1)
                _log(f"  Joining via private invite link …")
                try:
                    from telethon.tl.functions.messages import ImportChatInviteRequest
                    result = await client(ImportChatInviteRequest(invite_hash))
                    # result.chats[0] is the channel/group that was joined
                    if result.chats:
                        _joined_entity = result.chats[0]
                        _log(f"  ✓ Joined '{_joined_entity.title}'")
                    else:
                        _log("  ✓ Joined channel (no entity returned)")
                except Exception as join_exc:
                    err_str = str(join_exc).lower()
                    if "already" in err_str or "participant" in err_str:
                        # Already a member — still need to find the entity to mute
                        _log("  Already a member of the channel — re-fetching entity …")
                        try:
                            # CheckChatInvite returns info about the invite without joining
                            from telethon.tl.functions.messages import CheckChatInviteRequest
                            inv_info = await client(CheckChatInviteRequest(invite_hash))
                            if hasattr(inv_info, "chat"):
                                _joined_entity = inv_info.chat
                        except Exception:
                            pass
                    else:
                        _log(f"  WARNING: Could not join channel: {join_exc}")

            elif _m_pub:
                # ── Public channel username ──────────────────────────────────
                channel_ref = _m_pub.group(1)
                _log(f"  Joining @{channel_ref} …")
                try:
                    from telethon.tl.functions.channels import JoinChannelRequest
                    _joined_entity = await client.get_entity(channel_ref)
                    await client(JoinChannelRequest(_joined_entity))
                    _log(f"  ✓ Joined @{channel_ref}")
                except Exception as join_exc:
                    err_str = str(join_exc).lower()
                    if "already" in err_str or "participant" in err_str:
                        _log("  Already a member of the channel")
                    else:
                        _log(f"  WARNING: Could not join channel: {join_exc}")

            else:
                _log(f"  WARNING: Could not parse channel URL '{join_url}' — skipping join.")

            # ── Mute whichever entity we managed to resolve ──────────────────
            if _joined_entity is not None:
                try:
                    peer = await client.get_input_entity(_joined_entity)
                    await client(UpdateNotifySettingsRequest(
                        peer=InputNotifyPeer(peer),
                        settings=InputPeerNotifySettings(
                            mute_until=2_147_483_647,   # INT32_MAX — permanent
                            silent=True,
                        ),
                    ))
                    name = getattr(_joined_entity, "title",
                                   getattr(_joined_entity, "username", "channel"))
                    _log(f"  ✓ '{name}' muted permanently")
                except Exception as mute_exc:
                    _log(f"  WARNING: Could not mute channel: {mute_exc}")

            # Brief pause so the bot recognises our membership before we send URLs
            await asyncio.sleep(2)

    except Exception as exc:
        _log(f"  WARNING: First-time setup encountered an error: {exc}")
        # Non-fatal — write the flag anyway so we don't retry every launch

    try:
        _BOT_INIT_FLAG.touch()
    except Exception:
        pass

    _log(f"\n{'─'*52}")
    _log("  ✓ Telegram bot ready to use")
    _log(f"{'─'*52}\n")


# ═════════════════════════════════════════════
#  ASYNC DOWNLOAD
# ═════════════════════════════════════════════

async def download_all_async(
    client,
    pending_events: list,
    tmp_dir: Path,
    cfg: dict | None = None,
) -> list[Path]:
    total        = len(pending_events)
    done_counter = [0]
    _done_lock   = threading.Lock()
    start_time   = time.monotonic()

    _progress.clear()
    for ev in pending_events:
        fn = _get_filename(ev.message)
        _progress[fn] = (0, ev.message.document.size)

    _log(f"\n  Downloading {total} file(s) in parallel...\n")

    # 1 MB per GetFile request (Telethon effective ceiling).
    _CHUNK_SIZE = 1024 * 1024

    # ── Why one OS thread + one event loop per file ────────────────────────
    # When all downloads share one asyncio event loop every client's
    # iter_download loop fires its GetFile request in the same event-loop
    # tick.  The server replies in a batch; every coroutine wakes, writes,
    # and fires the next request together.  This produces perfect lock-step
    # synchronisation — the sawtooth seen on the NIC — regardless of how
    # clients were started.
    #
    # Giving each file its own OS thread AND its own asyncio event loop
    # breaks that lock-step permanently.  The OS scheduler runs threads
    # independently; each thread's event loop manages only one TCP stream.
    # Responses arrive at different times, writes are staggered, next
    # requests are staggered → smooth, fully-overlapping throughput.
    #
    # The 200 ms stagger on connect() spaces the ImportAuthorization
    # handshakes on non-home DCs so Telegram does not reject them.
    # ──────────────────────────────────────────────────────────────────────
    _AUTH_STAGGER_S  = 4.0   # seconds between successive connect() calls — wider gap prevents DC auth floods
    _MAX_AUTH_TRIES  = 8     # retries on auth failure
    _AUTH_RETRY_WAIT = 8.0   # seconds to wait between auth retries
    _MAX_FLOOD_WAIT  = 300   # honour flood waits up to 5 min; skip file only if longer than that
    session_string   = StringSession.save(client.session)

    def _emit_progress_ts() -> None:
        """Thread-safe progress — print() holds the GIL per call."""
        line = _render_progress(start_time, total, done_counter[0]).strip()
        print(f"##PROG##  {line}", flush=True)

    def _dl_one_threaded(ev, stagger_idx: int):
        """
        Blocking entry point run inside a ThreadPoolExecutor worker.
        Each call owns a private asyncio event loop so its TCP connection
        is completely independent of every other download thread.
        """
        async def _inner() -> Path:
            if stagger_idx > 0:
                await asyncio.sleep(stagger_idx * _AUTH_STAGGER_S)

            filename   = _sanitise_filename(_get_filename(ev.message))
            save_path  = tmp_dir / filename
            total_size = ev.message.document.size

            for attempt in range(1, _MAX_AUTH_TRIES + 1):
                received = 0
                _progress[filename] = (0, total_size)
                _api_id, _api_hash = _get_api_creds()
                dl_client = TelegramClient(StringSession(session_string), _api_id, _api_hash)
                try:
                    await dl_client.connect()

                    with open(save_path, "wb") as fh:
                        async for chunk in dl_client.iter_download(
                            ev.message,
                            request_size=_CHUNK_SIZE,
                        ):
                            fh.write(chunk)
                            received += len(chunk)
                            _progress[filename] = (received, total_size)
                            _emit_progress_ts()

                    break   # success; exit retry loop

                except Exception as exc:
                    exc_str = str(exc)
                    # FloodWaitError from ExportAuthorization / ImportAuthorization:
                    # Telegram rate-limits DC auth handshakes when many parallel
                    # connections hit the same non-home DC at once.
                    # Honour waits up to _MAX_FLOOD_WAIT seconds; skip the file
                    # (raise) if Telegram wants us to wait longer.
                    import re as _re
                    flood_match = _re.search(r"A wait of (\d+) seconds? is required", exc_str)
                    if flood_match:
                        wait_secs = int(flood_match.group(1))
                        if wait_secs <= _MAX_FLOOD_WAIT and attempt < _MAX_AUTH_TRIES:
                            _log(
                                f"  ⏳ Flood-wait {wait_secs}s for {filename} "
                                f"(attempt {attempt}/{_MAX_AUTH_TRIES}) — waiting…"
                            )
                            await asyncio.sleep(wait_secs + 1)
                        else:
                            raise   # wait too long or retries exhausted
                    elif ("ImportAuthorization" in exc_str or "ExportAuthorization" in exc_str) \
                            and attempt < _MAX_AUTH_TRIES:
                        _log(
                            f"  ⚠ Auth error for {filename} "
                            f"(attempt {attempt}/{_MAX_AUTH_TRIES}) — retrying in {_AUTH_RETRY_WAIT}s…"
                        )
                        await asyncio.sleep(_AUTH_RETRY_WAIT)
                    else:
                        raise   # non-auth error or retries exhausted
                finally:
                    await dl_client.disconnect()

            with _done_lock:
                done_counter[0] += 1
            _emit_progress_ts()
            return save_path

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_inner())
        finally:
            # Cancel every lingering Telethon background task before closing
            # the loop.  Without this step, MTProtoSender._send_loop/_recv_loop
            # and Connection._send_loop/_recv_loop are still scheduled when
            # loop.close() fires, producing "Task was destroyed but pending"
            # and "RuntimeError: Event loop is closed" in the output.
            try:
                pending = asyncio.all_tasks(loop)
                if pending:
                    for t in pending:
                        t.cancel()
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
            except Exception:
                pass
            finally:
                loop.close()
                asyncio.set_event_loop(None)

    # Limit concurrency to avoid hammering Telegram's ExportAuthorization endpoint.
    # The config key max_parallel_downloads defaults to 3 which stays safely under
    # Telegram's per-DC rate limit even for large albums.
    _max_parallel = cfg.get("max_parallel_downloads", 3) if cfg else 3
    main_loop = asyncio.get_running_loop()
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(total, _max_parallel)) as executor:
        futures = [
            main_loop.run_in_executor(executor, _dl_one_threaded, ev, i)
            for i, ev in enumerate(pending_events)
        ]
        results = await asyncio.gather(*futures, return_exceptions=True)

    downloaded = []
    for ev, result in zip(pending_events, results):
        if isinstance(result, Exception):
            _log(f"\n    ERROR  {_get_filename(ev.message)}: {result}")
        else:
            downloaded.append(result)

    elapsed  = time.monotonic() - start_time
    total_mb = sum(ev.message.document.size for ev in pending_events) / 1_048_576
    _log(
        f"\n\n  {len(downloaded)}/{total} file(s) in {elapsed:.1f}s "
        f"({total_mb / elapsed:.2f} MB/s avg)"
    )
    return downloaded


# ═════════════════════════════════════════════
#  FILE COLLECTION
# ═════════════════════════════════════════════

async def collect_files(
    message_queue:  asyncio.Queue,
    file_event:     asyncio.Event,
    expected_total: int | None,
    cfg:            dict,
) -> list:
    QUEUE_WAIT_TIMEOUT = cfg["queue_wait_timeout"]
    IDLE_TIMEOUT       = cfg["inter_file_idle_timeout"]
    IDLE_CHECK         = cfg["idle_check_interval"]

    pending_events: list  = []
    # last_file_time tracks when the last *audio document* arrived, not when
    # any message arrived.  Initialised to 0 so we never start the idle clock
    # until at least one file has actually been received.
    last_file_time: float = 0.0

    def _drain_queue(expected_total: int | None) -> tuple[int | None, bool]:
        nonlocal last_file_time
        done = False
        while not message_queue.empty():
            try:
                ev = message_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            msg  = ev.message
            text = (msg.message or "").strip()
            if msg.document is not None:
                pending_events.append(ev)
                last_file_time = time.monotonic()
                count_str = f"/{expected_total}" if expected_total is not None else ""
                _log(f"  [{len(pending_events)}{count_str}] {_get_filename(msg)}")
                if expected_total is not None and len(pending_events) >= expected_total:
                    done = True
            else:
                # Try to pick up a track count from any bot message, even the
                # "Downloading... Please wait." / queue-position messages that
                # arrive before the files.
                found = _parse_total_tracks(text)
                if found is not None and expected_total is None:
                    expected_total = found
                    _log(f"  Track count detected: {expected_total} tracks expected.")
                    # Re-check count immediately in case files arrived first.
                    if len(pending_events) >= expected_total:
                        done = True
                elif text:
                    first_line = text.splitlines()[0]
                    _log(f"  Bot: {first_line!r}")
        return expected_total, done

    # ── Wait for the bot to send *anything* after GET ALL is clicked ──────
    # We use QUEUE_WAIT_TIMEOUT here because the bot may queue the request
    # (image 2 in the flow) and take a while before sending the first file.
    try:
        await asyncio.wait_for(file_event.wait(), timeout=QUEUE_WAIT_TIMEOUT)
    except asyncio.TimeoutError:
        _log(f"\n  No files received after {QUEUE_WAIT_TIMEOUT}s — giving up.")
        return []

    file_event.clear()
    expected_total, done = _drain_queue(expected_total)
    if done:
        _log(f"\n  All {expected_total} track(s) received.")
        return pending_events

    while True:
        # ── Count-match: exit immediately when we have everything ──────────
        if expected_total is not None and len(pending_events) >= expected_total:
            _log(f"\n  All {expected_total} track(s) received.")
            break

        # ── Idle timeout: only start counting once the first file arrived ──
        # If last_file_time is still 0 no file has come yet — don't time out.
        if last_file_time > 0:
            idle = time.monotonic() - last_file_time
            if idle >= IDLE_TIMEOUT:
                n   = len(pending_events)
                exp = f"/{expected_total}" if expected_total else ""
                _log(f"\n  No new file for {idle:.1f}s — stopping at {n}{exp} track(s).")
                break

        try:
            await asyncio.wait_for(file_event.wait(), timeout=IDLE_CHECK)
            file_event.clear()
        except asyncio.TimeoutError:
            pass

        expected_total, done = _drain_queue(expected_total)
        if done:
            _log(f"\n  All {expected_total} track(s) received.")
            break

    return pending_events


# ═════════════════════════════════════════════
#  PROCESS ONE URL
# ═════════════════════════════════════════════

async def process_url(
    entry:          URLEntry,
    index:          int,
    total:          int,
    tmp_dir:        Path,
    session_string: str,
    cfg:            dict,
) -> tuple[list[Path], int | None]:
    BOT_USERNAME     = cfg["bot_username"]
    REPLY_TIMEOUT    = cfg["reply_timeout"]
    BOT_BUSY_WAIT    = cfg["bot_busy_wait"]
    BOT_BUSY_RETRIES = cfg["bot_busy_retries"]

    _log(f"\n{'='*52}")
    _log(f"  [{index}/{total}]  {entry.url}")
    _log(f"  Artist: {entry.artist}")
    _log(f"{'='*52}\n")

    _api_id, _api_hash = _get_api_creds()
    client = TelegramClient(StringSession(session_string), _api_id, _api_hash)
    await client.connect()
    bot_entity = await client.get_entity(BOT_USERNAME)

    message_queue: asyncio.Queue = asyncio.Queue()
    file_event: asyncio.Event    = asyncio.Event()

    @client.on(events.NewMessage(from_users=bot_entity, incoming=True))
    async def on_new(event):
        await message_queue.put(event)
        file_event.set()

    @client.on(events.MessageEdited(from_users=bot_entity, incoming=True))
    async def on_edit(event):
        await message_queue.put(event)
        file_event.set()

    async def cleanup():
        client.remove_event_handler(on_new)
        client.remove_event_handler(on_edit)
        await client.disconnect()

    album_msg      = None
    expected_total = None

    for attempt in range(1, BOT_BUSY_RETRIES + 1):
        while not message_queue.empty():
            message_queue.get_nowait()
        file_event.clear()

        await client.send_message(bot_entity, entry.url)
        _log(f"  Link sent (attempt {attempt}). Waiting for bot...")

        try:
            await asyncio.wait_for(file_event.wait(), timeout=REPLY_TIMEOUT)
            file_event.clear()
        except asyncio.TimeoutError:
            await cleanup()
            _log(f"  ERROR: No reply within {REPLY_TIMEOUT}s — skipping.")
            return [], None

        bot_reply = None
        while not message_queue.empty():
            try:
                bot_reply = message_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        if bot_reply is None:
            _log("  ERROR: Queue empty after event fired — skipping.")
            await cleanup()
            return [], None

        msg  = bot_reply.message
        text = (msg.message or "").strip()

        if msg.reply_markup:
            album_msg      = msg
            expected_total = _parse_total_tracks(text)
            if expected_total:
                _log(f"  Album info received. Total tracks: {expected_total}")
            else:
                _log("  Album info received. (Track count not in message)")
            break

        if _is_bot_busy(text):
            if attempt < BOT_BUSY_RETRIES:
                _log(f"  Bot busy: {text.splitlines()[0]!r}")
                _log(f"  Waiting {BOT_BUSY_WAIT}s before retry ({attempt}/{BOT_BUSY_RETRIES})...")
                await asyncio.sleep(BOT_BUSY_WAIT)
            else:
                _log(f"  ERROR: Bot still busy after {BOT_BUSY_RETRIES} retries — skipping.")
                await cleanup()
                return [], None
        else:
            _log(f"  WARNING: Unexpected bot reply: {text.splitlines()[0]!r}")
            await cleanup()
            return [], None

    if album_msg is None:
        await cleanup()
        return [], None

    target_button = None
    button_label  = None
    # Match any button containing "ALL" with a download-intent keyword.
    # Covers: "GET ALL", "DOWNLOAD ALL", "⬇ Get All", etc.
    _ALL_KEYWORDS = {"get", "download", "dl", "télécharger", "descargar"}
    for row in album_msg.reply_markup.rows:
        for btn in row.buttons:
            upper = btn.text.upper()
            has_all = "ALL" in upper
            has_kw  = any(kw in upper for kw in {k.upper() for k in _ALL_KEYWORDS})
            if has_all and has_kw:
                target_button, button_label = btn, btn.text
                break
        if target_button:
            break

    if target_button is None:
        all_buttons = [b for row in album_msg.reply_markup.rows for b in row.buttons]
        _log("\n  WARNING: No download-all button found. Available buttons:")
        for i, btn in enumerate(all_buttons, 1):
            _log(f"    {i}. {btn.text}")
        choice = input("  Enter button number to click (or Enter to skip): ").strip()
        if not choice.isdigit() or not (1 <= int(choice) <= len(all_buttons)):
            await cleanup()
            return [], None
        target_button = all_buttons[int(choice) - 1]
        button_label  = target_button.text

    _log(f"\n  Collecting files...")
    file_event.clear()
    collect_task = asyncio.ensure_future(
        collect_files(message_queue, file_event, expected_total, cfg)
    )

    _log(f"  Clicking: [{button_label}]")
    # Fire the click without awaiting its callback acknowledgement.
    # The bot sends all audio files first and only acknowledges the callback
    # query 11-12 seconds later; awaiting click() would block collect_task
    # for that entire delay.  Scheduling it as a background task lets file
    # collection proceed immediately while the acknowledgement arrives whenever
    # it wants.
    asyncio.ensure_future(album_msg.click(data=target_button.data))

    pending_events = await collect_task

    if not pending_events:
        _log("  WARNING: No files received for this URL.")
        await cleanup()
        return [], expected_total

    downloaded = await download_all_async(client, pending_events, tmp_dir, cfg)
    await cleanup()
    return downloaded, expected_total


# ═════════════════════════════════════════════
#  URL ENTRY  (CLI)
# ═════════════════════════════════════════════

def collect_url_entries(max_queue: int) -> list[URLEntry]:
    _is_gui = not sys.stdin.isatty()
    while True:
        try:
            count = int(input("" if _is_gui else f"\n  How many URLs? (1–{max_queue}): ").strip())
            if 1 <= count <= max_queue:
                break
            if not _is_gui:
                print(f"  Please enter a number between 1 and {max_queue}.")
        except ValueError:
            if not _is_gui:
                print("  Please enter a valid number.")

    entries: list[URLEntry] = []
    last_artist = ""

    for i in range(1, count + 1):
        url = input("" if _is_gui else f"\n  URL {i}/{count}: ").strip()
        if not url:
            if not _is_gui:
                print("  Empty URL — skipped.")
            continue

        if last_artist:
            artist = input("" if _is_gui else f"  Artist (Enter to reuse '{last_artist}'): ").strip() or last_artist
        else:
            artist = ""
            while not artist:
                artist = input("" if _is_gui else "  Artist: ").strip()
                if not artist and not _is_gui:
                    print("  Artist name is required.")

        last_artist = artist
        entries.append(URLEntry(url=url, artist=artist))

    if not entries:
        sys.exit("No URLs provided — exiting.")
    return entries


# ═════════════════════════════════════════════
#  SESSION SUMMARY
# ═════════════════════════════════════════════

def show_summary(results: list[URLResult]) -> None:
    icons = {"ok": "✓", "partial": "~", "skipped": "↷", "error": "✗"}

    _log(f"\n{'═'*62}")
    _log("  SESSION SUMMARY")
    _log(f"{'═'*62}\n")

    for r in results:
        icon    = icons.get(r.status, "?")
        exp_str = str(r.expected) if r.expected is not None else "?"
        dup_str = f"  ({r.dupes_skipped} dupe(s) skipped)" if r.dupes_skipped else ""
        dest_str = str(r.dest.resolve()) if r.dest else "—"

        _log(f"  [{icon}] {r.url[:62]}")
        _log(f"       Artist  : {r.artist}")
        _log(f"       Tracks  : {r.downloaded}/{exp_str}{dup_str}")
        _log(f"       Dest    : {dest_str}")
        if r.error:
            _log(f"       Error   : {r.error}")
        _log("")

    total_dl   = sum(r.downloaded    for r in results)
    total_dup  = sum(r.dupes_skipped for r in results)
    ok_count   = sum(1 for r in results if r.status == "ok")
    skip_count = sum(1 for r in results if r.status == "skipped")
    err_count  = sum(1 for r in results if r.status == "error")

    _log(f"  {ok_count}/{len(results)} URL(s) completed successfully")
    if skip_count:
        _log(f"  {skip_count} URL(s) skipped (already in manifest)")
    if err_count:
        _log(f"  {err_count} URL(s) failed")
    _log(f"  {total_dl} track(s) downloaded total")
    if total_dup:
        _log(f"  {total_dup} duplicate(s) skipped")
    _log(f"\n{'═'*62}\n")


# ═════════════════════════════════════════════
#  MAIN
# ═════════════════════════════════════════════

async def main() -> None:
    # Clean up any leftover pause flag from crashed previous runs
    try:
        (_DATA_DIR / "pause.flag").unlink(missing_ok=True)
    except Exception:
        pass
    _pause_event.clear()

    _check_cryptg()

    _log("\n+--------------------------------------+")
    _log("|  Telegram Audio Downloader + Sorter  |")
    _log("+--------------------------------------+\n")

    cfg = load_config()
    _gui_mode = not sys.stdin.isatty()
    if _gui_mode:
        input("")   # consume the mode token the GUI sends
    else:
        print("  ────────────────────────────────────────────")
        print("  Enter to start  ·  s → settings  ·  q → quit")
        print("  ────────────────────────────────────────────")
        _raw = input("  > ").strip().lower()
        if _raw in ("s", "settings", "2"):
            cfg = configure_settings(cfg)
        elif _raw in ("q", "quit", "exit", "3"):
            sys.exit("Bye!")

    home     = get_home_music_folder(cfg)
    manifest = load_manifest(home)
    entries  = collect_url_entries(cfg["max_queue"])

    # ── Build library hash index (once per session) ───────────────────────
    _log("\n  Scanning library for existing audio hashes…")
    hash_index = build_library_hash_index(home)

    already_done = [e for e in entries if is_url_complete(manifest, e.url, home)]
    skip_completed = True
    if already_done:
        _log(f"\n  {len(already_done)} URL(s) already in manifest (files verified):")
        for e in already_done:
            info = manifest[e.url]
            _log(f"    {e.url[:60]}  —  {info.get('artist','')}  @  {info.get('timestamp','')}")
        ans = input("" if _gui_mode else "\n  Skip these? (Y/n): ").strip().lower()
        skip_completed = ans != "n"

    _log("\n  Connecting to Telegram...")
    _api_id, _api_hash = _get_api_creds()
    main_client = TelegramClient(SESSION_FILE, _api_id, _api_hash)
    await main_client.connect()
    if not await main_client.is_user_authorized():
        _log("\n  ERROR: Not logged in to Telegram.")
        _log("  → Click the 'TG: ○' button in the TGDownloader toolbar to log in,")
        _log("    then try downloading again.")
        _log("")
        await main_client.disconnect()
        for entry in entries:
            _emit_result(entry.url, entry.artist, "error",
                         error="Not logged in to Telegram — use the TG button in the GUI")
        return
    me = await main_client.get_me()
    _log(f"  Logged in as: {me.first_name}")
    session_string = StringSession.save(main_client.session)

    # ── First-run gate: join + mute the bot's required channel if needed ──
    await _ensure_bot_initialized(main_client, cfg)

    await main_client.disconnect()

    results: list[URLResult] = []

    for i, entry in enumerate(entries, 1):
        # ── Fuzzy artist-directory resolution ─────────────────────────────
        # Prefer an existing folder whose name closely matches the artist
        # (handles typos, spacing differences, etc.) over creating a new one.
        artist_dir_exact = home / _sanitise_path(entry.artist)
        artist_dir = _fuzzy_match_dir(entry.artist, home) or artist_dir_exact

        if skip_completed and is_url_complete(manifest, entry.url, home):
            _log(f"\n  [{i}/{len(entries)}] Skipping (manifest + files verified): {entry.url[:60]}")
            results.append(URLResult(
                url=entry.url, artist=entry.artist,
                expected=None, downloaded=0, dupes_skipped=0,
                dest=artist_dir, status="skipped",
            ))
            _emit_result(entry.url, entry.artist, "skipped")
            continue

        # ── Pre-download duplicate warning ─────────────────────────────────
        check_pre_download(entry, home)

        url_tmp = Path("./tg_tmp_downloads") / f"url_{i}"
        url_tmp.mkdir(parents=True, exist_ok=True)

        try:
            downloaded, expected = await process_url(
                entry, i, len(entries), url_tmp, session_string, cfg,
            )
        except Exception as e:
            _log(f"\n  ERROR processing {entry.url}: {e}")
            traceback.print_exc()
            results.append(URLResult(
                url=entry.url, artist=entry.artist,
                expected=None, downloaded=0, dupes_skipped=0,
                dest=artist_dir, status="error", error=str(e),
            ))
            _emit_result(entry.url, entry.artist, "error", error=str(e))
            shutil.rmtree(url_tmp, ignore_errors=True)
            continue

        if not downloaded:
            results.append(URLResult(
                url=entry.url, artist=entry.artist,
                expected=expected, downloaded=0, dupes_skipped=0,
                dest=artist_dir, status="error", error="No files received",
            ))
            _emit_result(entry.url, entry.artist, "error",
                         expected=expected, error="No files received")
        else:
            dupes, albums = sort_by_album(url_tmp, artist_dir, hash_index)
            # ── On-device quality conversion ──────────────────────────
            _tgt_q = cfg.get('target_quality', 'FLAC')
            convert_directory_quality(artist_dir, _tgt_q)
            mark_url_complete(home, manifest, entry.url, entry.artist,
                              [f.name for f in downloaded], albums)
            dl_count = len(downloaded)
            status   = "ok" if (expected is None or dl_count >= expected) else "partial"
            results.append(URLResult(
                url=entry.url, artist=entry.artist,
                expected=expected, downloaded=dl_count, dupes_skipped=dupes,
                dest=artist_dir, status=status,
            ))
            _emit_result(entry.url, entry.artist, status,
                         downloaded=dl_count, expected=expected,
                         dupes_skipped=dupes)

        shutil.rmtree(url_tmp, ignore_errors=True)

        if i < len(entries):
            _log("\n  Pausing 2s before next URL...")
            await asyncio.sleep(2)
            # Honour pause signal from GUI
            _check_pause_flag()
            while _pause_event.is_set():
                await asyncio.sleep(0.5)
                _check_pause_flag()

    try:
        Path("./tg_tmp_downloads").rmdir()
    except OSError:
        pass

    show_summary(results)
    _log(f"  Music library: {home.resolve()}\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _log("\n\nInterrupted.")
    except Exception:
        _log("\n--- ERROR -------------------------------------------")
        traceback.print_exc()
        _log("-----------------------------------------------------")
        input("\nPress Enter to close...")