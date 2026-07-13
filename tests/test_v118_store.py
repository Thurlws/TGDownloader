"""Tests for the SQLite state store.

Covers the KV round-trip + file fallback, the play-events table, and the one-time
non-destructive migration from the legacy JSON/JSONL files.
"""

import json

import pytest

import tgd_store


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(tgd_store, "DB_PATH", tmp_path / "state.db")
    tgd_store.close()          # drop any cached connection from another test/session
    yield tgd_store
    tgd_store.close()


# ── Key/value blobs ───────────────────────────────────────────────────────────

def test_kv_round_trip(store):
    assert store.get_json("ratings", "DEFAULT") == "DEFAULT"     # missing → default
    store.set_json("ratings", {"k": {"rating": 5}})
    assert store.get_json("ratings") == {"k": {"rating": 5}}
    assert store.has_key("ratings") is True
    store.set_json("ratings", {"k": {"rating": 3}})              # overwrite
    assert store.get_json("ratings")["k"]["rating"] == 3


def test_get_json_falls_back_to_file(store, tmp_path):
    legacy = tmp_path / "watchlist.json"
    legacy.write_text(json.dumps({"123": {"name": "Band"}}), encoding="utf-8")
    # DB has nothing yet → the legacy file is used.
    assert store.get_json("watchlist", {}, legacy) == {"123": {"name": "Band"}}
    # Once written to the DB, the DB wins.
    store.set_json("watchlist", {"999": {"name": "Other"}})
    assert store.get_json("watchlist", {}, legacy) == {"999": {"name": "Other"}}


# ── Play events ───────────────────────────────────────────────────────────────

def test_play_events_append_and_read(store):
    assert store.play_event_count() == 0
    for i in range(5):
        store.append_play_event({"t": i, "title": f"T{i}", "artist": "A"})
    assert store.play_event_count() == 5
    allev = store.read_play_events()
    assert [e["title"] for e in allev] == ["T0", "T1", "T2", "T3", "T4"]      # oldest→newest
    recent = store.read_play_events(limit=2)
    assert [e["title"] for e in recent] == ["T3", "T4"]                       # last 2, still ordered


# ── Migration ─────────────────────────────────────────────────────────────────

def test_migrate_imports_and_backs_up(store, tmp_path):
    (tmp_path / "ratings.json").write_text(json.dumps({"a": {"rating": 4}}), encoding="utf-8")
    (tmp_path / "liked_songs.json").write_text(json.dumps([{"name": "x"}]), encoding="utf-8")
    (tmp_path / "watchlist.json").write_text(json.dumps({"7": {"name": "B"}}), encoding="utf-8")
    (tmp_path / "play_history.jsonl").write_text(
        json.dumps({"t": 1, "title": "One", "artist": "A"}) + "\n" +
        json.dumps({"t": 2, "title": "Two", "artist": "A"}) + "\n", encoding="utf-8")

    assert store.migrate(tmp_path) is True

    assert store.get_json("ratings") == {"a": {"rating": 4}}
    assert store.get_json("liked_songs") == [{"name": "x"}]
    assert store.get_json("watchlist") == {"7": {"name": "B"}}
    assert store.play_event_count() == 2

    # Originals renamed to .pre-sqlite.bak (non-destructive), not left in place.
    assert not (tmp_path / "ratings.json").exists()
    assert (tmp_path / "ratings.json.pre-sqlite.bak").exists()
    assert (tmp_path / "play_history.jsonl.pre-sqlite.bak").exists()


def test_migrate_is_idempotent(store, tmp_path):
    (tmp_path / "ratings.json").write_text(json.dumps({"a": {"rating": 4}}), encoding="utf-8")
    assert store.migrate(tmp_path) is True
    # Second run does nothing (and doesn't wipe the already-migrated data).
    assert store.migrate(tmp_path) is False
    assert store.get_json("ratings") == {"a": {"rating": 4}}


def test_migrate_no_files_is_noop(store, tmp_path):
    assert store.migrate(tmp_path) is False
    assert store.get_json("ratings", "none") == "none"
