"""Tests for the pure helpers in TGDownloader.py (no Telegram, no network)."""

import TGDownloader as tgd


# ── Filename / path sanitising ────────────────────────────────────────────────

def test_sanitise_path_replaces_forbidden_chars():
    assert tgd._sanitise_path(r'AC/DC: "High"?') == "AC_DC_ _High__"


def test_sanitise_path_strips_trailing_dots_and_spaces():
    assert tgd._sanitise_path("Album. ") == "Album"
    assert tgd._sanitise_path("...") == "_unnamed"


def test_sanitise_filename_collapses_runs():
    assert tgd._sanitise_filename("a  b___c.mp3") == "a b c.mp3"
    assert tgd._sanitise_filename("") == "audio"


# ── Bot message parsing ───────────────────────────────────────────────────────

def test_parse_total_tracks_variants():
    assert tgd._parse_total_tracks("Total tracks: 12") == 12
    assert tgd._parse_total_tracks("total track : 3") == 3
    assert tgd._parse_total_tracks("Sending track 2 of 9 …") == 9
    assert tgd._parse_total_tracks("no numbers here") is None


def test_is_bot_busy():
    assert tgd._is_bot_busy("Please WAIT for the current download")
    assert tgd._is_bot_busy("I'm busy right now")
    assert not tgd._is_bot_busy("Here are your files")


# ── Fuzzy matching ────────────────────────────────────────────────────────────

def test_fuzzy_score_symmetric_and_case_insensitive():
    assert tgd._fuzzy_score("AC DC", "ac dc") == 1.0
    assert tgd._fuzzy_score("abc", "xyz") < 0.5


def test_fuzzy_match_dir_exact_sanitised(tmp_path):
    (tmp_path / "AC_DC").mkdir()
    assert tgd._fuzzy_match_dir("AC/DC", tmp_path).name == "AC_DC"


def test_fuzzy_match_dir_fuzzy_variant(tmp_path):
    (tmp_path / "Taylor Swift").mkdir()
    match = tgd._fuzzy_match_dir("Taylor_Swift", tmp_path)
    assert match is not None and match.name == "Taylor Swift"


def test_fuzzy_match_dir_rejects_different_names(tmp_path):
    (tmp_path / "Radiohead").mkdir()
    assert tgd._fuzzy_match_dir("Aphex Twin", tmp_path) is None


def test_fuzzy_match_dir_missing_parent(tmp_path):
    assert tgd._fuzzy_match_dir("Anyone", tmp_path / "nope") is None


# ── ffmpeg conversion decisions ───────────────────────────────────────────────

def test_needs_conversion(tmp_path):
    from pathlib import Path
    assert tgd._needs_conversion(Path("x.mp3"), "FLAC") is True
    assert tgd._needs_conversion(Path("x.flac"), "FLAC") is False
    assert tgd._needs_conversion(Path("x.flac"), "MP3 320") is True
    # mp3 → mp3 is passed through regardless of bitrate target
    assert tgd._needs_conversion(Path("x.mp3"), "MP3 128") is False


# ── Manifest ──────────────────────────────────────────────────────────────────

def _make_library(tmp_path, artist="Artist", files=("01 - a.flac", "02 - b.flac")):
    adir = tgd.artists_root(tmp_path) / artist / "Album"
    adir.mkdir(parents=True)
    for f in files:
        (adir / f).write_bytes(b"x")
    return adir


def test_manifest_round_trip_complete(tmp_path):
    files = ["01 - a.flac", "02 - b.flac"]
    _make_library(tmp_path, "Artist", files)
    manifest = {}
    tgd.mark_url_complete(tmp_path, manifest, "http://u", "Artist", files)
    assert tgd.is_url_complete(manifest, "http://u", tmp_path) is True
    # And it persisted to disk
    assert tgd.load_manifest(tmp_path)["http://u"]["status"] == "complete"


def test_manifest_incomplete_when_files_missing(tmp_path):
    _make_library(tmp_path, "Artist", ["01 - a.flac"])
    manifest = {}
    tgd.mark_url_complete(tmp_path, manifest, "http://u", "Artist",
                          ["01 - a.flac", "02 - missing.flac"])
    assert tgd.is_url_complete(manifest, "http://u", tmp_path) is False


def test_manifest_unknown_url(tmp_path):
    assert tgd.is_url_complete({}, "http://unknown", tmp_path) is False


def test_load_manifest_corrupt_returns_empty(tmp_path):
    mp = tmp_path / ".tgdownloader"
    mp.mkdir()
    (mp / "manifest.json").write_text("{broken", encoding="utf-8")
    assert tgd.load_manifest(tmp_path) == {}


# ── Playlist ordering helpers ─────────────────────────────────────────────────

def test_norm_track_title():
    assert tgd._norm_track_title("Hey, Jude!") == "heyjude"
    assert tgd._norm_track_title("") == ""
    assert tgd._norm_track_title(None) == ""


# ── Artists/ layout migration ─────────────────────────────────────────────────

def test_migrate_artists_layout_moves_audio_dirs(tmp_path):
    (tmp_path / "Some Artist" / "Album").mkdir(parents=True)
    (tmp_path / "Some Artist" / "Album" / "t.mp3").write_bytes(b"x")
    (tmp_path / "Random Docs").mkdir()          # no audio → must stay put
    moved = tgd.migrate_artists_layout(tmp_path)
    assert moved == 1
    assert (tgd.artists_root(tmp_path) / "Some Artist" / "Album" / "t.mp3").exists()
    assert (tmp_path / "Random Docs").exists()


def test_migrate_artists_layout_idempotent(tmp_path):
    (tmp_path / "A" / "B").mkdir(parents=True)
    (tmp_path / "A" / "B" / "t.flac").write_bytes(b"x")
    assert tgd.migrate_artists_layout(tmp_path) == 1
    assert tgd.migrate_artists_layout(tmp_path) == 0
