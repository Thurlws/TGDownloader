"""Tests for the natural-language smart-playlist parser.

Covers the local pattern parser (the tier that runs with no key and no network),
the coercion applied to whatever Claude returns, and the orchestration that
decides whether the Claude tier is worth a call at all.
"""

import pytest

import tgd_nlrules as nl


def rules(query):
    return nl.parse_local(query)["rules"]


# ── Durations: added vs not-played ───────────────────────────────────────────

@pytest.mark.parametrize("query, days", [
    ("added in the last 3 days",        3),
    ("downloaded this week",            7),
    ("stuff I grabbed in the past 2 weeks", 14),
    ("added in the last month",         30),
    ("imported in the last 6 months",   180),
    ("added in the last year",          365),
])
def test_added_days(query, days):
    assert rules(query)["added_days"] == days
    assert rules(query)["not_played_days"] == 0


@pytest.mark.parametrize("query, days", [
    ("not played in 30 days",                    30),
    ("I haven't played this in a month",         30),
    ("haven't listened to in 6 months",          180),
    ("stuff I haven't heard in two weeks",       14),
])
def test_not_played_days(query, days):
    assert rules(query)["not_played_days"] == days
    assert rules(query)["added_days"] == 0


def test_duration_without_a_verb_reads_as_added():
    # "from the last 2 weeks" is about when it arrived, which is how the field
    # is labelled in the UI too.
    assert rules("flac from the last 2 weeks")["added_days"] == 14


def test_bare_unit_needs_a_qualifier():
    # "days of thunder" must not become a 1-day rule.
    r = rules("days of thunder")
    assert r["added_days"] == 0 and r["not_played_days"] == 0


# ── Ratings ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("query, want", [
    ("5 star tracks",        5),
    ("four star and up",     4),
    ("3-star or better",     3),
    ("★★★★",                 4),
    ("top rated stuff",      4),
    ("my favourites",        5),
    ("my favorites",         5),
])
def test_min_rating(query, want):
    assert rules(query)["min_rating"] == want


def test_rating_is_clamped():
    assert rules("9 star tracks")["min_rating"] == 5


def test_five_star_only_is_flagged_as_a_floor():
    out = nl.parse_local("5 star only")
    assert out["rules"]["min_rating"] == 5
    assert any("minimum" in n for n in out["notes"])


# ── Tempo ────────────────────────────────────────────────────────────────────

def test_bpm_range():
    r = rules("workout mix 120-140 bpm")
    assert (r["bpm_min"], r["bpm_max"]) == (120, 140)


def test_bpm_range_reversed_is_sorted():
    r = rules("140 to 120 bpm")
    assert (r["bpm_min"], r["bpm_max"]) == (120, 140)


def test_bpm_bounds():
    assert rules("over 160 bpm")["bpm_min"] == 160
    assert rules("over 160 bpm")["bpm_max"] == 0
    assert rules("under 90 bpm")["bpm_max"] == 90
    assert rules("under 90 bpm")["bpm_min"] == 0


def test_single_bpm_becomes_a_window():
    r = rules("around 128 bpm")
    assert (r["bpm_min"], r["bpm_max"]) == (123, 133)


def test_tempo_words():
    assert rules("workout playlist")["bpm_min"] == 120
    assert rules("chill playlist")["bpm_max"] == 100


def test_explicit_bpm_beats_a_tempo_word():
    r = rules("chill playlist 60-80 bpm")
    assert (r["bpm_min"], r["bpm_max"]) == (60, 80)


def test_bpm_rules_warn_about_the_tempo_scan():
    # Tracks with no BPM data are dropped by the rule engine, so a tempo rule
    # on an unscanned library silently returns almost nothing.
    out = nl.parse_local("over 120 bpm")
    assert any("Tempo Analysis" in n for n in out["notes"])


def test_no_bpm_rule_means_no_warning():
    assert nl.parse_local("5 star flac")["notes"] == []


# ── Limits, formats, sort ────────────────────────────────────────────────────

@pytest.mark.parametrize("query, want", [
    ("top 50 rock",        50),
    ("limit 25",           25),
    ("just 10 tracks",     10),
    ("no more than 5",      5),
    ("30 songs",           30),
])
def test_limit(query, want):
    assert rules(query)["limit"] == want


def test_duration_is_not_read_as_a_limit():
    r = rules("added in the last 30 days")
    assert r["added_days"] == 30 and r["limit"] == 0


def test_bpm_is_not_read_as_a_limit():
    assert rules("over 120 bpm")["limit"] == 0


def test_rating_is_not_read_as_a_limit():
    assert rules("5 star tracks")["limit"] == 0


@pytest.mark.parametrize("query, want", [
    ("in flac",     "flac"),
    ("mp3 only",    "mp3"),
    ("lossless",    "flac"),
    ("aac files",   "m4a"),
])
def test_format(query, want):
    assert rules(query)["format"] == want


def test_sort():
    assert rules("newest first")["sort"] == "recent"
    assert rules("recently added flac")["sort"] == "recent"
    assert rules("rock")["sort"] == "az"


# ── Genre and artist ─────────────────────────────────────────────────────────

def test_genre_prefers_the_longest_match():
    assert rules("drum and bass")["genre"] == "drum and bass"
    assert rules("hip hop tracks")["genre"] == "hip hop"


def test_artist():
    assert rules("top 50 rock songs by Radiohead")["artist"] == "Radiohead"
    assert rules("songs from Kanye that I rated 4 stars")["artist"] == "Kanye"
    assert rules("artist: Aphex Twin")["artist"] == "Aphex Twin"


def test_by_the_last_week_is_not_an_artist():
    r = rules("added by the last week")
    assert r["artist"] == ""


def test_sort_phrase_is_not_an_artist():
    assert rules("sorted by artist")["artist"] == ""


# ── Names ────────────────────────────────────────────────────────────────────

def test_explicit_name_wins():
    assert rules("drum and bass over 160 bpm called Speed Run")["name"] == "Speed Run"


def test_name_is_derived_from_the_matched_rules():
    assert rules("top 50 rock songs by Radiohead")["name"] == "Rock by Radiohead"
    assert rules("four star metal")["name"] == "4 Star Metal"
    assert rules("stuff I haven't listened to in 6 months")["name"] == "Rediscover"
    assert rules("everything added this week")["name"] == "Recently Added"


def test_name_falls_back_to_the_request():
    assert rules("something moody for a rainy tuesday")["name"] == "Something Moody"


def test_name_is_never_empty():
    assert rules("")["name"]


# ── Reporting ────────────────────────────────────────────────────────────────

def test_filled_lists_the_fields_that_were_set():
    out = nl.parse_local("5 star flac added this week")
    assert set(out["filled"]) == {"min_rating", "format", "added_days"}
    assert "name" not in out["filled"]         # derived, not a filter


def test_rule_vocabulary_is_not_reported_as_unparsed():
    # These words were consumed by patterns, so calling them "not understood"
    # would be wrong and would send handled queries to Claude.
    out = nl.parse_local("5 star flac I haven't played in 30 days, top 20")
    assert out["unparsed"] == []


def test_real_leftovers_are_reported():
    assert set(nl.parse_local("something moody for a rainy tuesday")["unparsed"]) == {
        "moody", "rainy", "tuesday"}


def test_tempo_word_beaten_by_a_number_is_not_a_leftover():
    assert "workout" not in nl.parse_local("workout mix 120-140 bpm")["unparsed"]


def test_combined_query():
    r = rules("energetic hip hop I haven't played in a month, 30 tracks max")
    assert r["genre"] == "hip hop"
    assert r["not_played_days"] == 30
    assert r["limit"] == 30
    assert r["bpm_min"] == 120
    assert r["added_days"] == 0


# ── Coercion of the model's output ───────────────────────────────────────────

def test_coerce_fills_missing_fields():
    out = nl._coerce({})
    assert out["format"] == "" and out["limit"] == 0 and out["sort"] == "az"


def test_coerce_rejects_a_bad_format():
    assert nl._coerce({"format": "wav"})["format"] == ""
    assert nl._coerce({"format": ".FLAC"})["format"] == "flac"


def test_coerce_clamps_and_orders():
    assert nl._coerce({"min_rating": 9})["min_rating"] == 5
    assert nl._coerce({"limit": -5})["limit"] == 0
    out = nl._coerce({"bpm_min": 160, "bpm_max": 120})
    assert (out["bpm_min"], out["bpm_max"]) == (120, 160)


def test_coerce_survives_junk_types():
    out = nl._coerce({"limit": "abc", "added_days": None, "genre": 12})
    assert out["limit"] == 0 and out["added_days"] == 0 and out["genre"] == "12"


def test_coerce_normalises_sort():
    assert nl._coerce({"sort": "banana"})["sort"] == "az"
    assert nl._coerce({"sort": "recent"})["sort"] == "recent"


# ── Orchestration ────────────────────────────────────────────────────────────

def test_llm_is_skipped_when_the_local_parse_is_complete(monkeypatch):
    monkeypatch.setattr(nl, "parse_llm", _boom)
    out = nl.translate("top 50 rock songs by Radiohead", {"nl_playlist_llm": True,
                                                          "anthropic_api_key": "k"})
    assert out["source"] == "local"


def test_llm_is_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(nl, "parse_llm", _boom)
    out = nl.translate("something moody for a rainy tuesday", {"nl_playlist_llm": False})
    assert out["source"] == "local"
    assert out["unparsed"]


def test_llm_failure_falls_back_to_the_local_parse(monkeypatch):
    monkeypatch.setattr(nl, "llm_available", lambda cfg=None: True)
    monkeypatch.setattr(nl, "parse_llm", _boom)
    out = nl.translate("something moody for a rainy tuesday", {})
    assert out["source"] == "local"
    assert any("failed" in n for n in out["notes"])


def test_llm_result_is_used_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(nl, "llm_available", lambda cfg=None: True)
    monkeypatch.setattr(nl, "parse_llm", lambda q, c=None: nl._coerce(
        {"genre": "ambient", "bpm_max": 90, "name": "Rainy Day"}))
    out = nl.translate("something moody for a rainy tuesday", {})
    assert out["source"] == "claude"
    assert out["rules"]["genre"] == "ambient"
    assert set(out["filled"]) == {"genre", "bpm_max"}
    assert any("Tempo Analysis" in n for n in out["notes"])


def test_llm_result_gets_a_name_when_the_model_omits_one(monkeypatch):
    monkeypatch.setattr(nl, "llm_available", lambda cfg=None: True)
    monkeypatch.setattr(nl, "parse_llm", lambda q, c=None: nl._coerce({"genre": "jazz"}))
    out = nl.translate("something moody", {})
    assert out["rules"]["name"] == "Jazz"


def test_empty_query():
    out = nl.translate("   ")
    assert out["source"] == "empty" and out["filled"] == []


def test_llm_unavailable_without_a_key(monkeypatch):
    # The key resolver falls back to the environment, so clear it or this
    # depends on the machine running the suite.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert nl.llm_available({"nl_playlist_llm": True, "anthropic_api_key": ""}) is False
    assert nl.llm_available({"nl_playlist_llm": False, "anthropic_api_key": "k"}) is False


def test_llm_reads_the_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")
    assert nl._api_key({}) == "env-key"
    assert nl._api_key({"anthropic_api_key": "cfg-key"}) == "cfg-key"   # config wins


def _boom(*a, **k):
    raise RuntimeError("should not be called")
