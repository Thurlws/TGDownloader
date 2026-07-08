"""Tests for the 1.3.0 features: play tracking, backup restore validation,
and the trash helper's permanent-delete fallback."""

import io
import json
import zipfile

import tgd_common
import TGDownloader_GUI as gui


# ── Play tracking ─────────────────────────────────────────────────────────────

def test_record_play_event_appends_jsonl(tmp_path):
    p = tmp_path / "plays.jsonl"
    assert gui._record_play_event(
        {"title": "Song A", "artist": "Artist X", "album": "Alb"}, path=p) is True
    assert gui._record_play_event(
        {"title": "Song A", "artist": "Artist X"}, path=p) is True
    events = gui._load_play_events(p)
    assert len(events) == 2
    assert events[0]["title"] == "Song A"
    assert events[0]["t"] > 0 and events[0]["ts"]


def test_record_play_event_rejects_incomplete(tmp_path):
    p = tmp_path / "plays.jsonl"
    assert gui._record_play_event({"title": "", "artist": "X"}, path=p) is False
    assert gui._record_play_event({"title": "T", "artist": " "}, path=p) is False
    assert not p.exists()


def test_load_play_events_tolerates_torn_line(tmp_path):
    p = tmp_path / "plays.jsonl"
    p.write_text('{"t":1,"title":"a","artist":"b"}\n{"t":2,"tit', encoding="utf-8")
    events = gui._load_play_events(p)
    assert len(events) == 1


def test_aggregate_play_stats():
    now = 1_000_000_000
    day = 86_400
    events = [
        {"t": now - 1 * day,  "ts": "x", "title": "S1", "artist": "A", "album": ""},
        {"t": now - 2 * day,  "ts": "x", "title": "S1", "artist": "A", "album": ""},
        {"t": now - 10 * day, "ts": "x", "title": "S2", "artist": "B", "album": ""},
        {"t": now - 40 * day, "ts": "x", "title": "S3", "artist": "A", "album": ""},
    ]
    st = gui._aggregate_play_stats(events, now=now)
    assert st["total"] == 4
    assert st["last7"] == 2
    assert st["last30"] == 3
    assert st["top_tracks"][0] == {"artist": "A", "title": "S1", "plays": 2}
    assert st["top_artists"][0] == {"artist": "A", "plays": 3}
    assert st["recent"][0]["title"] == "S3"      # most recent line last → first


def test_aggregate_play_stats_empty():
    st = gui._aggregate_play_stats([])
    assert st["total"] == 0 and st["top_tracks"] == [] and st["recent"] == []


# ── Backup restore validation ─────────────────────────────────────────────────

def _make_zip(entries: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_validate_backup_zip_accepts_allowlisted_json():
    data = _make_zip({
        "liked_songs.json": json.dumps({"songs": []}),
        "watchlist.json":   json.dumps({}),
    })
    accepted, skipped = gui._validate_backup_zip(data)
    assert set(accepted) == {"liked_songs.json", "watchlist.json"}
    assert skipped == []


def test_validate_backup_zip_skips_unknown_and_traversal():
    data = _make_zip({
        "evil.exe":                     "MZ",
        "../../outside.json":           json.dumps({}),
        "nested/dir/tg_sessions.json":  json.dumps({"s": 1}),
    })
    accepted, skipped = gui._validate_backup_zip(data)
    # Traversal-y name is reduced to its basename and isn't allowlisted;
    # nested allowlisted basenames are accepted under the basename only.
    assert set(accepted) == {"tg_sessions.json"}
    assert "evil.exe" in skipped and "../../outside.json" in skipped


def test_validate_backup_zip_skips_invalid_json():
    data = _make_zip({"liked_songs.json": "{not json"})
    accepted, skipped = gui._validate_backup_zip(data)
    assert accepted == {} and skipped == ["liked_songs.json"]


def test_validate_backup_zip_rejects_garbage():
    try:
        gui._validate_backup_zip(b"this is not a zip")
        assert False, "expected BadZipFile"
    except Exception:
        pass


# ── Trash helper ──────────────────────────────────────────────────────────────

def test_send_to_trash_permanent_fallback_file(tmp_path, monkeypatch):
    # Simulate: no send2trash package, non-Windows platform → permanent delete.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "send2trash":
            raise ImportError("not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(tgd_common.sys, "platform", "linux", raising=False)

    f = tmp_path / "t.mp3"
    f.write_bytes(b"x")
    assert tgd_common.send_to_trash(f) == "permanent"
    assert not f.exists()


def test_send_to_trash_permanent_fallback_dir(tmp_path, monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "send2trash":
            raise ImportError("not installed")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(tgd_common.sys, "platform", "linux", raising=False)

    d = tmp_path / "album"
    d.mkdir()
    (d / "t.mp3").write_bytes(b"x")
    assert tgd_common.send_to_trash(d) == "permanent"
    assert not d.exists()


# ── Cover detection ───────────────────────────────────────────────────────────

def test_dir_has_cover(tmp_path):
    assert gui._dir_has_cover(tmp_path) is False
    (tmp_path / "track.mp3").write_bytes(b"x")
    assert gui._dir_has_cover(tmp_path) is False
    (tmp_path / "cover.jpg").write_bytes(b"x")
    assert gui._dir_has_cover(tmp_path) is True
