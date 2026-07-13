"""Tests for folder/file naming templates + multi-disc handling.

The defaults (empty template, multidisc "off") must reproduce the original sort
behaviour exactly; the opt-in modes rename files and lay out discs.
"""

import TGDownloader as tgd


# ── Pure helpers ──────────────────────────────────────────────────────────────

def test_parse_leading_int():
    assert tgd._parse_leading_int("3/12") == 3
    assert tgd._parse_leading_int("03") == 3
    assert tgd._parse_leading_int("") == 0
    assert tgd._parse_leading_int("junk") == 0


def test_render_template_basic():
    tags = {"title": "Song", "artist": "Band", "albumartist": "Band",
            "album": "Album", "year": "2021", "track": 3, "disc": 1}
    assert tgd._render_name_template("{track2} - {title}", tags, ".flac") == "03 - Song.flac"
    assert tgd._render_name_template("{year} {album}/{title}", tags, ".mp3") is not None


def test_render_template_sanitises_and_keeps_ext():
    tags = {"title": 'A: B? "C"', "track": 1, "disc": 0}
    out = tgd._render_name_template("{title}", tags, ".flac")
    assert out.endswith(".flac")
    assert ":" not in out and "?" not in out and '"' not in out


def test_render_template_empty_or_untitled_returns_none():
    assert tgd._render_name_template("", {"title": "x"}, ".flac") is None
    assert tgd._render_name_template("{title}", {"title": ""}, ".flac") is None


def test_render_template_unknown_placeholder_is_blank_not_crash():
    tags = {"title": "Song", "track": 1, "disc": 0}
    # {bogus} must not raise; it renders empty.
    assert tgd._render_name_template("{bogus}{title}", tags, ".mp3") == "Song.mp3"


# ── sort_by_album integration ─────────────────────────────────────────────────

def _make_files(src, names):
    src.mkdir(parents=True, exist_ok=True)
    for n in names:
        (src / n).write_bytes(b"\x00\x01\x02")   # dummy; tags are monkeypatched
    return [src / n for n in names]


def _patch(monkeypatch, cfg, tagmap):
    monkeypatch.setattr(tgd, "load_config", lambda: cfg)
    monkeypatch.setattr(tgd, "_get_album", lambda p: "Test Album")
    monkeypatch.setattr(tgd, "_read_sort_tags",
                        lambda p: tagmap[p.name])


def test_default_keeps_original_names(tmp_path, monkeypatch):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_files(src, ["a.flac", "b.flac"])
    # Default config: no template, multidisc off → tags never even read.
    monkeypatch.setattr(tgd, "load_config", lambda: {})
    monkeypatch.setattr(tgd, "_get_album", lambda p: "Test Album")
    tgd.sort_by_album(src, dest)
    assert (dest / "Test Album" / "a.flac").is_file()
    assert (dest / "Test Album" / "b.flac").is_file()


def test_template_renames(tmp_path, monkeypatch):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_files(src, ["x.flac", "y.flac"])
    tagmap = {
        "x.flac": {"title": "First", "track": 1, "disc": 1, "artist": "B",
                   "albumartist": "B", "album": "Test Album", "year": "2020"},
        "y.flac": {"title": "Second", "track": 2, "disc": 1, "artist": "B",
                   "albumartist": "B", "album": "Test Album", "year": "2020"},
    }
    _patch(monkeypatch, {"file_naming_template": "{track2} {title}",
                         "multidisc_mode": "off"}, tagmap)
    tgd.sort_by_album(src, dest)
    assert (dest / "Test Album" / "01 First.flac").is_file()
    assert (dest / "Test Album" / "02 Second.flac").is_file()


def test_multidisc_subfolders(tmp_path, monkeypatch):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_files(src, ["d1.flac", "d2.flac"])
    tagmap = {
        "d1.flac": {"title": "One", "track": 1, "disc": 1, "artist": "B",
                    "albumartist": "B", "album": "Test Album", "year": ""},
        "d2.flac": {"title": "Two", "track": 1, "disc": 2, "artist": "B",
                    "albumartist": "B", "album": "Test Album", "year": ""},
    }
    _patch(monkeypatch, {"file_naming_template": "",
                         "multidisc_mode": "subfolders"}, tagmap)
    tgd.sort_by_album(src, dest)
    assert (dest / "Test Album" / "Disc 1" / "d1.flac").is_file()
    assert (dest / "Test Album" / "Disc 2" / "d2.flac").is_file()


def test_multidisc_prefix(tmp_path, monkeypatch):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_files(src, ["d1.flac", "d2.flac"])
    tagmap = {
        "d1.flac": {"title": "One", "track": 1, "disc": 1, "artist": "B",
                    "albumartist": "B", "album": "Test Album", "year": ""},
        "d2.flac": {"title": "Two", "track": 1, "disc": 2, "artist": "B",
                    "albumartist": "B", "album": "Test Album", "year": ""},
    }
    _patch(monkeypatch, {"file_naming_template": "",
                         "multidisc_mode": "prefix"}, tagmap)
    tgd.sort_by_album(src, dest)
    assert (dest / "Test Album" / "1-d1.flac").is_file()
    assert (dest / "Test Album" / "2-d2.flac").is_file()


def test_single_disc_ignores_multidisc_mode(tmp_path, monkeypatch):
    # All tracks disc 1 → not multi-disc → no Disc subfolder / prefix.
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make_files(src, ["a.flac", "b.flac"])
    tagmap = {
        "a.flac": {"title": "A", "track": 1, "disc": 1, "artist": "B",
                   "albumartist": "B", "album": "Test Album", "year": ""},
        "b.flac": {"title": "B", "track": 2, "disc": 1, "artist": "B",
                   "albumartist": "B", "album": "Test Album", "year": ""},
    }
    _patch(monkeypatch, {"file_naming_template": "",
                         "multidisc_mode": "subfolders"}, tagmap)
    tgd.sort_by_album(src, dest)
    assert (dest / "Test Album" / "a.flac").is_file()
    assert not (dest / "Test Album" / "Disc 1").exists()
