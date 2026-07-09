"""Tests for v1.6.0 library-intelligence helpers: tag normalisation (pure),
genre canonicalisation, and the duplicate quality score / keep-best ordering."""

import tgd_common
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


def test_fpcalc_exe_returns_none_or_path():
    # Just exercises the detector; fpcalc is not installed in CI.
    assert tgd_common.fpcalc_exe() is None or isinstance(tgd_common.fpcalc_exe(), str)
