"""Tests for v1.6.0 library-intelligence helpers: tag normalisation (pure),
genre canonicalisation, and the duplicate quality score / keep-best ordering."""

from pathlib import Path

import tgd_common
import TGDownloader as tgd
import TGDownloader_GUI as gui


# ── Featuring normalisation ───────────────────────────────────────────────────

def test_normalise_featuring_inline_forms():
    assert tgd_common.normalise_featuring("Song ft Drake") == "Song (feat. Drake)"
    assert tgd_common.normalise_featuring("Song feat. Drake") == "Song (feat. Drake)"
    assert tgd_common.normalise_featuring("Song featuring Drake") == "Song (feat. Drake)"


def test_normalise_featuring_parenthesised_forms():
    assert tgd_common.normalise_featuring("Song (ft. Drake)") == "Song (feat. Drake)"
    assert tgd_common.normalise_featuring("Song [feat Drake]") == "Song (feat. Drake)"


def test_normalise_featuring_idempotent_and_noop():
    once = tgd_common.normalise_featuring("Song ft Drake")
    assert tgd_common.normalise_featuring(once) == once
    assert tgd_common.normalise_featuring("Plain Title") == "Plain Title"
    assert tgd_common.normalise_featuring("") == ""


# ── Genre canonicalisation ────────────────────────────────────────────────────

def test_canonical_genre_known():
    assert tgd_common.canonical_genre("hip hop") == "Hip-Hop"
    assert tgd_common.canonical_genre("Hip-Hop/Rap") == "Hip-Hop"
    assert tgd_common.canonical_genre("RNB") == "R&B"
    assert tgd_common.canonical_genre("electronica") == "Electronic"


def test_canonical_genre_unknown_titlecased_but_preserved():
    assert tgd_common.canonical_genre("shoegaze") == "Shoegaze"
    # Already-capitalised unknowns are left as-is (no information lost).
    assert tgd_common.canonical_genre("K-Pop") == "K-Pop"
    assert tgd_common.canonical_genre("") == ""


# ── Duplicate quality score / keep-best ───────────────────────────────────────

def test_quality_score_prefers_lossless(tmp_path):
    flac = tmp_path / "a.flac"; flac.write_bytes(b"x" * 2000)
    mp3  = tmp_path / "a.mp3";  mp3.write_bytes(b"x" * 5000)
    # FLAC outranks MP3 regardless of the MP3 being a larger blob.
    assert gui._quality_score(flac) > gui._quality_score(mp3)


def test_quality_score_orders_group(tmp_path):
    files = {"z.mp3": b"x" * 100, "a.flac": b"y" * 100, "m.ogg": b"z" * 100}
    for n, b in files.items():
        (tmp_path / n).write_bytes(b)
    ordered = sorted((tmp_path / n for n in files),
                     key=gui._quality_score, reverse=True)
    assert ordered[0].suffix == ".flac"      # best kept first
    assert ordered[-1].suffix == ".mp3"      # worst trashed


def _same_bytes(*paths):
    for f in paths:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"identical audio")


def test_dupe_plan_never_trashes_playlist_copies(tmp_path):
    album  = tmp_path / "Artists" / "Band" / "Album" / "03 - Song.flac"
    deluxe = tmp_path / "Artists" / "Band" / "Album (Deluxe)" / "03 - Song.flac"
    pl     = tmp_path / "Playlists" / "Mix" / "03 - Song.flac"
    _same_bytes(album, deluxe, pl)
    plan = gui._dupe_keep_plan(tmp_path, [str(pl), str(deluxe), str(album)])
    by_path = {m["path"]: m for m in plan}
    assert by_path[str(pl)]["playlist"] and by_path[str(pl)]["keep"]
    assert by_path[str(album)]["keep"]             # shortest path wins the tie
    assert not by_path[str(deluxe)]["keep"]
    assert plan[0]["path"] == str(album)           # kept library copy listed first


def test_dupe_plan_keeps_a_group_made_only_of_playlist_copies(tmp_path):
    a = tmp_path / "Playlists" / "Mix" / "01 - Song.flac"
    b = tmp_path / "Playlists" / "Gym" / "04 - Song.flac"
    _same_bytes(a, b)
    assert all(m["keep"] for m in gui._dupe_keep_plan(tmp_path, [str(a), str(b)]))


def test_dupe_plan_ignores_scan_order(tmp_path):
    a = tmp_path / "Artists" / "Band" / "Album" / "x.flac"
    b = tmp_path / "Artists" / "Band" / "Album (1)" / "x.flac"
    _same_bytes(a, b)
    for order in ([str(a), str(b)], [str(b), str(a)]):
        kept = [m["path"] for m in gui._dupe_keep_plan(tmp_path, order) if m["keep"]]
        assert kept == [str(a)]


def test_dedupe_auto_trashes_only_extra_library_copies(tmp_path, monkeypatch, gui_post):
    home   = tmp_path / "lib"
    album  = home / "Artists" / "Band" / "Album" / "03 - Song.flac"
    deluxe = home / "Artists" / "Band" / "Album (Deluxe)" / "03 - Song.flac"
    pl     = home / "Playlists" / "Mix" / "03 - Song.flac"
    _same_bytes(album, deluxe, pl)
    monkeypatch.setattr(tgd, "load_config", lambda: {"home_music_folder": str(home)})
    monkeypatch.setattr(tgd, "_HASH_CACHE_FILE", tmp_path / "hash_cache.json")
    trashed = []
    monkeypatch.setattr(tgd_common, "send_to_trash",
                        lambda p: trashed.append(Path(p)) or "test")
    status, body = gui_post("/dedupe-auto", {})
    assert status == 200 and body["trashed"] == 1
    assert trashed == [deluxe.resolve()]


def test_fpcalc_exe_returns_none_or_path():
    # Just exercises the detector; fpcalc is not installed in CI.
    assert tgd_common.fpcalc_exe() is None or isinstance(tgd_common.fpcalc_exe(), str)
