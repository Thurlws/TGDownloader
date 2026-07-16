# SPDX-License-Identifier: MIT
"""SQLite-backed store for TGDownloader's non-regenerable user data.

Consolidates the small JSON state files that hold data you can't just rebuild
(star ratings, liked songs, the artist watchlist) plus the play-history log, into
one SQLite database (`tgd_state.db`). Writes are transactional (no more half-
written JSON on a crash) and the play history becomes a real table you can query
instead of a growing .jsonl you have to scan line by line.

Scope on purpose: the *regenerable* caches (album-id, BPM, library-stats, the
backend's audio-hash cache) stay as plain JSON: losing one just means a re-scan,
so they don't need the ceremony.

Migration is one-time and non-destructive: the legacy files are imported and then
renamed to `<name>.pre-sqlite.bak`, so the originals are still on disk if anything
ever looks wrong. Every read also falls back to the legacy file, so nothing breaks
in the window before migration runs.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

import tgd_common

logger = logging.getLogger("tgd_store")

DB_PATH = tgd_common.DATA_DIR / "tgd_state.db"

_lock = threading.RLock()
_conn: "sqlite3.Connection | None" = None
_MIGRATION_KEY = "__migrated_v1"

# Legacy files this store owns: KV blobs {store key: filename} + the play log.
KV_FILES = {
    "ratings":     "ratings.json",
    "liked_songs": "liked_songs.json",
    "watchlist":   "watchlist.json",
}
PLAY_HISTORY_FILE = "play_history.jsonl"


def _connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=15)
        _conn.execute("PRAGMA journal_mode=WAL")     # concurrent reads, atomic commits
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        _conn.execute(
            "CREATE TABLE IF NOT EXISTS play_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, t REAL NOT NULL, data TEXT NOT NULL)")
        _conn.commit()
    return _conn


# ── Key/value JSON blobs ──────────────────────────────────────────────────────

def get_json(key: str, default=None, fallback_file: "Path | None" = None):
    """The stored object for `key`. Falls back to `fallback_file` (a legacy JSON
    file) when the DB has nothing yet, then to `default`."""
    with _lock:
        try:
            row = _connect().execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        except Exception as exc:
            logger.warning("store read failed for %s: %s", key, exc)
            row = None
    if row is not None:
        try:
            return json.loads(row[0])
        except Exception:
            pass
    if fallback_file is not None:
        try:
            fp = Path(fallback_file)
            if fp.exists():
                return json.loads(fp.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("fallback read failed for %s: %s", key, exc)
    return default


def set_json(key: str, obj) -> None:
    """Persist `obj` under `key` in a single transaction."""
    blob = json.dumps(obj, ensure_ascii=False)
    with _lock:
        c = _connect()
        c.execute("INSERT INTO kv(key, value) VALUES(?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, blob))
        c.commit()


def has_key(key: str) -> bool:
    with _lock:
        return _connect().execute("SELECT 1 FROM kv WHERE key=?", (key,)).fetchone() is not None


# ── Play history (a real table, not a growing .jsonl) ─────────────────────────

def append_play_event(evt: dict) -> None:
    with _lock:
        c = _connect()
        c.execute("INSERT INTO play_events(t, data) VALUES(?, ?)",
                  (float(evt.get("t") or time.time()), json.dumps(evt, ensure_ascii=False)))
        c.commit()


def _bulk_append(evts: "list[dict]") -> None:
    with _lock:
        c = _connect()
        c.executemany("INSERT INTO play_events(t, data) VALUES(?, ?)",
                      [(float(e.get("t") or 0), json.dumps(e, ensure_ascii=False)) for e in evts])
        c.commit()


def read_play_events(limit: "int | None" = None) -> "list[dict]":
    """Play events oldest→newest. `limit` returns the most recent N (still ordered
    oldest→newest) via a real SQL query instead of scanning a whole file."""
    with _lock:
        if limit:
            rows = _connect().execute(
                "SELECT data FROM play_events ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
            rows = list(reversed(rows))
        else:
            rows = _connect().execute("SELECT data FROM play_events ORDER BY id").fetchall()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r[0]))
        except Exception:
            pass
    return out


def play_event_count() -> int:
    with _lock:
        return _connect().execute("SELECT COUNT(*) FROM play_events").fetchone()[0]


# ── One-time migration from the legacy JSON files ─────────────────────────────

def migrate(data_dir: "Path | None" = None) -> bool:
    """Import the legacy JSON/JSONL user-data files into the DB once, renaming
    each original to `<name>.pre-sqlite.bak`. Safe to call every startup, it
    no-ops after the first run. Returns True if it migrated anything."""
    data_dir = data_dir or tgd_common.DATA_DIR
    did = False
    with _lock:
        if has_key(_MIGRATION_KEY):
            return False
        for key, fname in KV_FILES.items():
            f = data_dir / fname
            if not has_key(key) and f.exists():
                try:
                    set_json(key, json.loads(f.read_text(encoding="utf-8")))
                    f.rename(f.with_name(f.name + ".pre-sqlite.bak"))
                    did = True
                except Exception as exc:
                    logger.warning("migrate %s failed: %s", fname, exc)
        ph = data_dir / PLAY_HISTORY_FILE
        if play_event_count() == 0 and ph.exists():
            try:
                evts = []
                for line in ph.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line:
                        try:
                            evts.append(json.loads(line))
                        except Exception:
                            continue        # tolerate a torn final line
                if evts:
                    _bulk_append(evts)
                ph.rename(ph.with_name(ph.name + ".pre-sqlite.bak"))
                did = True
            except Exception as exc:
                logger.warning("migrate play history failed: %s", exc)
        set_json(_MIGRATION_KEY, {"at": time.time()})
    if did:
        logger.info("Migrated legacy state files into %s", DB_PATH.name)
    return did


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
