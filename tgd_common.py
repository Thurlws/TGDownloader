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
Shared runtime helpers for TGDownloader.

Single source of truth for things both processes (the GUI server and the
backend downloader) need: the data directory, config load/save, Telegram API
credentials, ffmpeg detection, and the app version.

Secrets & the OS keyring
------------------------
By default all settings — including api_hash and the various service tokens —
live in plaintext in tg_audio_config.json.  Setting ``"use_keyring": true`` in
the config (and ``pip install keyring``) moves every key listed in
:data:`SECRET_KEYS` into the operating-system keyring on the next save; the
JSON file then only holds empty placeholders.  The overlay is transparent:
``load_config()`` returns the merged view either way, so callers never care
where a secret physically lives.  If keyring is enabled but the package is
missing, secrets stay in the JSON file and a warning is logged — nothing is
ever lost.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

__version__ = "1.2.0"   # single source — bump this when you cut a new release

logger = logging.getLogger("tgd_common")


# ── Data directory ────────────────────────────────────────────────────────────
# When bundled with PyInstaller, TGD_DATA_DIR points to the folder containing
# the .exe so user data doesn't end up inside the read-only bundle.

if getattr(sys, "frozen", False):
    DATA_DIR = Path(os.environ.get("TGD_DATA_DIR", Path(sys.executable).parent))
else:
    DATA_DIR = Path(os.environ.get("TGD_DATA_DIR", Path(__file__).parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "tg_audio_config.json"


# ── Default config ────────────────────────────────────────────────────────────

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
    # ── Scrobbling (opt-in; off unless a token/key is provided) ──
    "scrobble_enabled":        False,
    "scrobble_service":        "listenbrainz",  # "listenbrainz" | "lastfm"
    "listenbrainz_token":      "",
    "lastfm_api_key":          "",
    "lastfm_secret":           "",
    "lastfm_session_key":      "",
    # ── Artist watchlist / new-release radar ──
    "watchlist_autocheck":     True,
    # ── UI preferences ──
    "theme":                   "dark",   # "dark" | "light"
    # ── Secrets storage: keep tokens in the OS keyring instead of the JSON
    #    file.  Requires `pip install keyring`; see module docstring. ──
    "use_keyring":             False,
}


# ── Optional keyring secret storage ──────────────────────────────────────────

# Config keys treated as secrets when use_keyring is enabled.
# api_id is deliberately excluded: it is a non-secret numeric identifier and
# keeping it in the JSON lets the setup wizard detect a configured install.
SECRET_KEYS = (
    "api_hash",
    "listenbrainz_token",
    "lastfm_api_key",
    "lastfm_secret",
    "lastfm_session_key",
    "spotify_client_secret",
)
_KEYRING_SERVICE = "TGDownloader"
_keyring_warned = False


def _keyring():
    """Return the keyring module, or None when unavailable/broken."""
    try:
        import keyring  # type: ignore
        return keyring
    except Exception:
        return None


def _warn_keyring_missing() -> None:
    global _keyring_warned
    if not _keyring_warned:
        _keyring_warned = True
        logger.warning(
            "use_keyring is enabled but the 'keyring' package is not available "
            "— secrets remain in tg_audio_config.json. Fix: pip install keyring"
        )


# ── Config load / save ────────────────────────────────────────────────────────

def load_config(path: "Path | None" = None) -> dict:
    """Defaults merged with the saved config, plus the keyring secret overlay.

    A corrupt config file is not silently discarded any more: a warning is
    logged so the user can tell why their settings reverted to defaults.
    """
    cfg_path = path or CONFIG_FILE
    cfg = dict(DEFAULT_CONFIG)
    if cfg_path.exists():
        try:
            saved = json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg.update(saved)
        except Exception as exc:
            logger.warning(
                "Could not parse %s (%s) — falling back to defaults. "
                "Fix or delete the file to silence this warning.",
                cfg_path.name, exc,
            )

    if cfg.get("use_keyring"):
        kr = _keyring()
        if kr is None:
            _warn_keyring_missing()
        else:
            for key in SECRET_KEYS:
                if not cfg.get(key):
                    try:
                        val = kr.get_password(_KEYRING_SERVICE, key)
                    except Exception as exc:
                        logger.warning("Keyring read failed for %s: %s", key, exc)
                        break
                    if val:
                        cfg[key] = val
    return cfg


def save_config(cfg: dict, path: "Path | None" = None) -> None:
    """Persist the config.  With use_keyring enabled, secrets go to the OS
    keyring and only empty placeholders are written to the JSON file."""
    cfg_path = path or CONFIG_FILE
    disk_cfg = dict(cfg)

    if disk_cfg.get("use_keyring"):
        kr = _keyring()
        if kr is None:
            _warn_keyring_missing()
        else:
            for key in SECRET_KEYS:
                val = str(disk_cfg.get(key) or "")
                if not val:
                    continue
                try:
                    kr.set_password(_KEYRING_SERVICE, key, val)
                    disk_cfg[key] = ""
                except Exception as exc:
                    # Keep the value in the JSON rather than losing it.
                    logger.warning("Keyring write failed for %s: %s", key, exc)

    try:
        cfg_path.write_text(
            json.dumps(disk_cfg, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Could not save config to %s: %s", cfg_path, exc)


# ── Telegram API credentials ──────────────────────────────────────────────────

def get_api_credentials() -> "tuple[int | None, str | None]":
    """(api_id, api_hash) from the config, or (None, None) when unset."""
    try:
        cfg = load_config()
        api_id   = cfg.get("api_id")
        api_hash = cfg.get("api_hash", "")
        if api_id and api_hash:
            return int(api_id), str(api_hash)
    except Exception as exc:
        logger.warning("Could not read API credentials: %s", exc)
    return None, None


def require_api_credentials() -> "tuple[int, str]":
    """Like get_api_credentials() but raises when credentials are missing."""
    api_id, api_hash = get_api_credentials()
    if api_id and api_hash:
        return api_id, api_hash
    raise RuntimeError(
        "Telegram API credentials not found.\n"
        "Run TGDownloader and complete the setup wizard first."
    )


# ── ffmpeg detection ──────────────────────────────────────────────────────────

_FFMPEG_PATH: "str | None | bool" = False   # False = not looked up yet


def ffmpeg_exe() -> "str | None":
    """Path to a usable ffmpeg binary, or None.  Cached after first lookup."""
    global _FFMPEG_PATH
    if _FFMPEG_PATH is False:
        _FFMPEG_PATH = shutil.which("ffmpeg")
    return _FFMPEG_PATH


def ffmpeg_available() -> bool:
    return ffmpeg_exe() is not None
