"""Tests for v1.7.0 stats & curation helpers: star ratings, the last-played
map behind the "not played in N days" smart-playlist rule, the play-history
filter, and the year-end Wrapped aggregation."""

import time

import TGDownloader_GUI as gui


# ── Star ratings ──────────────────────────────────────────────────────────────

def test_set_rating_add_update_clear(tmp_path):
    store = tmp_path / "ratings.json"
    entry = {"path_hash": "ab12", "name": "01 - Song.flac",
             "title": "Song", "artist": "Artist", "album": "Album"}

    r = gui._set_rating({**entry, "rating": 4}, path=store)
    assert r["ok"] and r["rating"] == 4 and r["count"] == 1

    key = gui._liked_key("ab12", "01 - Song.flac")
    saved = gui._load_ratings(store)
    assert saved[key]["rating"] == 4
    assert saved[key]["artist"] == "Artist"

    r = gui._set_rating({**entry, "rating": 2}, path=store)
    assert r["rating"] == 2
    assert gui._load_ratings(store)[key]["rating"] == 2

    r = gui._set_rating({**entry, "rating": 0}, path=store)
    assert r["ok"] and r["count"] == 0
    assert gui._load_ratings(store) == {}


def test_set_rating_rejects_bad_input(tmp_path):
    store = tmp_path / "ratings.json"
    assert "error" in gui._set_rating({"path_hash": "", "name": "x", "rating": 3}, path=store)
    assert "error" in gui._set_rating({"path_hash": "ab", "name": "x", "rating": 6}, path=store)
    assert "error" in gui._set_rating({"path_hash": "ab", "name": "x", "rating": -1}, path=store)
    assert "error" in gui._set_rating({"path_hash": "ab", "name": "x", "rating": "lots"}, path=store)


def test_load_ratings_tolerates_missing_and_corrupt(tmp_path):
    assert gui._load_ratings(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert gui._load_ratings(bad) == {}


# ── Last-played map ("not played in N days" rule) ─────────────────────────────

def test_last_played_map_keeps_most_recent_and_title_fallback():
    events = [
        {"t": 100.0, "title": "Song A", "artist": "Artist X"},
        {"t": 500.0, "title": "Song A", "artist": "Artist X"},
        {"t": 300.0, "title": "Song B", "artist": "Artist Y"},
    ]
    m = gui._last_played_map(events)
    assert m[("artist x", "song a")] == 500.0
    assert m[("", "song a")] == 500.0          # title-only fallback key
    assert m[("artist y", "song b")] == 300.0


def test_last_played_map_skips_broken_events():
    m = gui._last_played_map([
        {"t": "not-a-number", "title": "X", "artist": "Y"},
        {"t": 10.0, "title": "", "artist": "Y"},      # no title → skipped
        {"t": 10.0, "title": "Kept", "artist": ""},   # empty artist is fine
    ])
    assert m == {("", "kept"): 10.0}


# ── Play-history filter (history browser) ────────────────────────────────────

_EVENTS = [
    {"t": 1.0, "ts": "2026-01-01T10:00:00", "title": "Alpha", "artist": "One",  "album": "LP1"},
    {"t": 2.0, "ts": "2026-01-02T10:00:00", "title": "Beta",  "artist": "Two",  "album": "LP2"},
    {"t": 3.0, "ts": "2026-01-03T10:00:00", "title": "Gamma", "artist": "One",  "album": "LP1"},
]


def test_filter_play_events_newest_first_and_paged():
    d = gui._filter_play_events(_EVENTS, limit=2, offset=0)
    assert d["total"] == 3
    assert [e["title"] for e in d["events"]] == ["Gamma", "Beta"]
    d2 = gui._filter_play_events(_EVENTS, limit=2, offset=2)
    assert [e["title"] for e in d2["events"]] == ["Alpha"]


def test_filter_play_events_substring_search():
    assert gui._filter_play_events(_EVENTS, q="one")["total"] == 2      # artist match
    assert gui._filter_play_events(_EVENTS, q="lp2")["total"] == 1      # album match
    assert gui._filter_play_events(_EVENTS, q="beta")["total"] == 1     # title match
    assert gui._filter_play_events(_EVENTS, q="zzz")["total"] == 0


# ── Wrapped ───────────────────────────────────────────────────────────────────

def _mk(ts_str, title, artist, album=""):
    t = time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M"))
    return {"t": t, "ts": ts_str.replace(" ", "T"),
            "title": title, "artist": artist, "album": album}


def test_wrapped_stats_totals_and_tops():
    events = [
        _mk("2026-01-05 09:00", "Hit", "Star", "Debut"),
        _mk("2026-01-05 10:00", "Hit", "Star", "Debut"),
        _mk("2026-01-06 10:00", "Deep Cut", "Star", "Debut"),
        _mk("2026-03-10 10:00", "Other", "Someone Else"),
        _mk("2025-12-31 23:00", "Old Year", "Star"),      # outside 2026
    ]
    w = gui._wrapped_stats(events, 2026)
    assert w["total_plays"] == 4
    assert w["unique_tracks"] == 3
    assert w["unique_artists"] == 2
    assert w["top_artists"][0] == {"artist": "Star", "plays": 3}
    assert w["top_tracks"][0] == {"artist": "Star", "title": "Hit", "plays": 2}
    assert w["top_albums"][0] == {"artist": "Star", "album": "Debut", "plays": 3}
    assert w["by_month"][0] == 3 and w["by_month"][2] == 1
    assert sorted(w["years"], reverse=True) == w["years"]
    assert set(w["years"]) == {2025, 2026}


def test_wrapped_stats_streak_and_busiest_day():
    events = [
        _mk("2026-06-01 09:00", "A", "X"),
        _mk("2026-06-02 09:00", "A", "X"),
        _mk("2026-06-03 09:00", "A", "X"),
        _mk("2026-06-03 10:00", "B", "X"),
        _mk("2026-06-10 09:00", "A", "X"),
    ]
    w = gui._wrapped_stats(events, 2026)
    assert w["longest_streak_days"] == 3
    assert w["busiest_day"] == {"date": "2026-06-03", "plays": 2}
    assert w["listening_days"] == 4


def test_wrapped_stats_empty_year():
    w = gui._wrapped_stats([_mk("2024-01-01 09:00", "A", "X")], 2026)
    assert w["total_plays"] == 0
    assert w["busiest_day"] is None
    assert w["first_play"] is None
    assert w["longest_streak_days"] == 0
