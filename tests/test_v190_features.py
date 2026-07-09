"""Tests for v1.9.0: the backend stdin builder shared by immediate and
scheduled runs, and the scheduled-queue-run arm/status/cancel lifecycle."""

import time

import TGDownloader_GUI as gui


# ── Backend stdin payload ─────────────────────────────────────────────────────

def test_build_backend_stdin_albums_and_playlists():
    entries = [
        {"url": "https://deezer.com/album/1", "artist": "A"},
        {"url": "https://deezer.com/playlist/2", "artist": "B",
         "isPlaylist": True, "playlistName": "My\nMix "},
    ]
    out = gui._build_backend_stdin(entries)
    assert out.split("\n") == [
        "1", "2",
        "https://deezer.com/album/1", "A", "",
        "https://deezer.com/playlist/2", "B", "My Mix",
        "Y", "",
    ]


def test_build_backend_stdin_playlist_name_ignored_without_flag():
    out = gui._build_backend_stdin(
        [{"url": "u", "artist": "a", "playlistName": "Sneaky"}])
    assert out.split("\n")[4] == ""      # 3rd entry line stays empty


# ── Schedule lifecycle ────────────────────────────────────────────────────────

def _cleanup():
    gui._schedule_cancel()


def test_schedule_set_validation():
    try:
        assert "error" in gui._schedule_set(time.time() + 3600, [])
        assert "error" in gui._schedule_set(time.time() - 10, [{"url": "u"}])
        assert "error" in gui._schedule_set(time.time() + 8 * 86400, [{"url": "u"}])
        assert gui._schedule_status() == {"scheduled": False}
    finally:
        _cleanup()


def test_schedule_set_status_and_cancel():
    try:
        at = time.time() + 3600
        d = gui._schedule_set(at, [{"url": "u1"}, {"url": "u2"}], home="/music")
        assert d["scheduled"] is True
        assert d["entries"] == 2
        assert 3590 <= d["in_secs"] <= 3600
        assert d["at_str"]

        s = gui._schedule_status()
        assert s["scheduled"] is True and s["entries"] == 2

        c = gui._schedule_cancel()
        assert c == {"ok": True, "scheduled": False}
        assert gui._schedule_status() == {"scheduled": False}
    finally:
        _cleanup()


def test_schedule_rearm_replaces_previous():
    try:
        gui._schedule_set(time.time() + 3600, [{"url": "first"}])
        d = gui._schedule_set(time.time() + 7200, [{"url": "a"}, {"url": "b"}, {"url": "c"}])
        assert d["entries"] == 3                 # one schedule at a time
        assert d["in_secs"] > 3600
    finally:
        _cleanup()
