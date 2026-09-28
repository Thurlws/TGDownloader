# SPDX-License-Identifier: MIT
"""Minimal, dependency-free Discord Rich Presence client.

Speaks Discord's local IPC protocol directly over the desktop client's named
pipe (Windows, via `_winapi`) or Unix domain socket (macOS/Linux). No
`pypresence`, no external packages, matching the app's stdlib-first style.

Everything here is best-effort: if Discord isn't running, the client id is
missing/invalid, or the pipe hiccups, calls quietly no-op and never raise into
the caller. Presence is opt-in (`discord_rich_presence` in the config) and needs
a Discord *application* client id (`discord_client_id`); create one for free at
https://discord.com/developers/applications (any name), copy its Application ID.
"""
from __future__ import annotations

import json
import logging
import os
import struct
import sys
import threading
import time
import uuid

logger = logging.getLogger("tgdownloader.discord")

_OP_HANDSHAKE = 0
_OP_FRAME     = 1
_OP_CLOSE     = 2


def _ipc_candidates() -> "list[str]":
    """Every path Discord might expose its IPC endpoint at (ids 0-9)."""
    if sys.platform == "win32":
        return [rf"\\.\pipe\discord-ipc-{i}" for i in range(10)]
    base = (os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR")
            or os.environ.get("TMP") or os.environ.get("TEMP") or "/tmp")
    # Discord (and Flatpak/snap variants) may nest the socket a level down.
    roots = [base, os.path.join(base, "app", "com.discordapp.Discord"),
             os.path.join(base, "snap.discord")]
    return [os.path.join(r, f"discord-ipc-{i}") for r in roots for i in range(10)]


class DiscordPresence:
    """Thread-safe single connection to the local Discord client."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handle = None          # Windows pipe HANDLE
        self._sock = None            # Unix socket
        self._connected = False
        self.client_id = ""

    # ── low-level framing ────────────────────────────────────────────────
    def _write(self, op: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        frame = struct.pack("<II", op, len(data)) + data
        if self._handle is not None:
            import _winapi
            _winapi.WriteFile(self._handle, frame)
        elif self._sock is not None:
            self._sock.sendall(frame)

    def _read_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            if self._handle is not None:
                import _winapi
                chunk, _ = _winapi.ReadFile(self._handle, n - len(buf))
            elif self._sock is not None:
                chunk = self._sock.recv(n - len(buf))
            else:
                return buf
            if not chunk:
                break
            buf += chunk
        return buf

    def _read_frame(self) -> "tuple[int|None, dict]":
        header = self._read_exact(8)
        if len(header) < 8:
            return None, {}
        op, length = struct.unpack("<II", header)
        body = self._read_exact(length) if length else b""
        try:
            return op, (json.loads(body.decode("utf-8")) if body else {})
        except Exception:
            return op, {}

    # ── connection lifecycle ─────────────────────────────────────────────
    def _open_endpoint(self, path: str) -> bool:
        if sys.platform == "win32":
            import _winapi
            access = 0x80000000 | 0x40000000            # GENERIC_READ|WRITE
            self._handle = _winapi.CreateFile(
                path, access, 0, 0, _winapi.OPEN_EXISTING, 0, 0)
        else:
            import socket
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(2.0)
            s.connect(path)
            self._sock = s
        return True

    def connect(self, client_id: str) -> bool:
        """Open the pipe and complete the handshake. Returns True only on a
        READY reply. Safe to call repeatedly, it reconnects cleanly."""
        with self._lock:
            self._close_locked()
            self.client_id = str(client_id or "").strip()
            if not self.client_id:
                return False
            for path in _ipc_candidates():
                try:
                    self._open_endpoint(path)
                except OSError:
                    self._close_locked()
                    continue
                try:
                    self._write(_OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
                    op, resp = self._read_frame()
                    if op == _OP_FRAME and resp.get("evt") == "READY":
                        self._connected = True
                        user = (resp.get("data") or {}).get("user") or {}
                        logger.info("Discord Rich Presence connected via %s (user %s)",
                                    path, user.get("username", "?"))
                        return True
                    # Reached Discord but it declined (e.g. bad client id); the
                    # protocol works, surface why and stop probing.
                    logger.warning("Discord declined presence handshake: %s",
                                   resp.get("message") or resp or f"op {op}")
                    self._close_locked()
                    return False
                except Exception as exc:
                    logger.debug("Discord handshake error on %s: %s", path, exc)
                    self._close_locked()
                    continue
            logger.info("Discord Rich Presence: no running Discord client found")
            return False

    def set_activity(self, activity: "dict|None") -> bool:
        """Push an activity dict (or None to clear it). Best-effort."""
        with self._lock:
            if not self._connected:
                return False
            try:
                self._write(_OP_FRAME, {
                    "cmd":   "SET_ACTIVITY",
                    "args":  {"pid": os.getpid(), "activity": activity},
                    "nonce": str(uuid.uuid4()),
                })
                self._read_frame()          # drain Discord's echo; ignore contents
                return True
            except Exception as exc:
                logger.debug("Discord set_activity failed: %s", exc)
                self._close_locked()
                return False

    def clear(self) -> bool:
        return self.set_activity(None)

    def _close_locked(self) -> None:
        self._connected = False
        if self._handle is not None:
            try:
                import _winapi
                _winapi.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    @property
    def connected(self) -> bool:
        return self._connected


def build_activity(title: str, artist: str, album: str = "",
                   position: float = 0.0, paused: bool = False) -> dict:
    """Shape a Discord activity from track metadata. Discord requires 2-128
    char strings; anything shorter is dropped rather than rejected."""
    def _clip(s: str) -> str:
        s = (s or "").strip()
        return s[:128] if len(s) >= 2 else ""

    state = artist
    if album:
        state = f"{artist} - {album}" if artist else album
    if paused:
        state = f"{state} (paused)" if state else "Paused"

    activity: dict = {}
    if _clip(title):
        activity["details"] = _clip(title)
    if _clip(state):
        activity["state"] = _clip(state)
    if not paused and position >= 0:
        activity["timestamps"] = {"start": int(time.time() - max(0.0, position))}
    return activity
