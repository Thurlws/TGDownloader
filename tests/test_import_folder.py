"""Folder import: every source file reaches the library and the counts are
honest. Tags are faked from the dummy file body ("artist|album|payload")."""

import TGDownloader as tgd
import TGDownloader_GUI as gui


def _fake_tags(monkeypatch, tmp_path, cfg=None):
    monkeypatch.setattr(tgd, "load_config", lambda: dict(cfg or {}))
    monkeypatch.setattr(tgd, "_HASH_CACHE_FILE", tmp_path / "hash_cache.json")
    monkeypatch.setattr(tgd, "_get_artist",
                        lambda p: p.read_bytes().split(b"|")[0].decode())
    monkeypatch.setattr(tgd, "_get_album",
                        lambda p: p.read_bytes().split(b"|")[1].decode())


def _track(path, artist, album, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"{artist}|{album}|{payload}".encode())


def test_same_named_tracks_from_two_albums_both_import(monkeypatch, tmp_path):
    # The old code staged every file flat under its bare name, so one album's
    # "01 - Intro.flac" overwrote the other's and both were counted as imported.
    # (Album names are distinct enough not to fuzzy-match each other.)
    _fake_tags(monkeypatch, tmp_path)
    src, home = tmp_path / "src", tmp_path / "lib"
    home.mkdir()
    _track(src / "Blue Sky" / "01 - Intro.flac", "Band", "Blue Sky", "a")
    _track(src / "Night Drive" / "01 - Intro.flac", "Band", "Night Drive", "b")

    res = gui._import_folder(tgd, home, src)

    assert (res["imported"], res["dupes"], res["failed"]) == (2, 0, 0)
    band = home / "Artists" / "Band"
    assert (band / "Blue Sky" / "01 - Intro.flac").read_bytes().endswith(b"|a")
    assert (band / "Night Drive" / "01 - Intro.flac").read_bytes().endswith(b"|b")
    assert not (home / ".tgimport_tmp").exists()
    assert (src / "Blue Sky" / "01 - Intro.flac").exists()     # source untouched


def test_reimport_counts_duplicates_not_imports(monkeypatch, tmp_path):
    _fake_tags(monkeypatch, tmp_path)
    src, home = tmp_path / "src", tmp_path / "lib"
    home.mkdir()
    _track(src / "01 - Song.flac", "Band", "Album", "x")
    assert gui._import_folder(tgd, home, src)["imported"] == 1
    res = gui._import_folder(tgd, home, src)
    assert (res["imported"], res["dupes"], res["failed"]) == (0, 1, 0)


def test_file_that_fails_to_move_counts_as_failed(monkeypatch, tmp_path):
    _fake_tags(monkeypatch, tmp_path)
    src, home = tmp_path / "src", tmp_path / "lib"
    home.mkdir()
    _track(src / "a.flac", "Band", "Album", "a")
    _track(src / "b.flac", "Band", "Album", "b")
    real_move = tgd.shutil.move

    def flaky_move(s, d):
        if str(s).endswith("b.flac"):
            raise OSError("disk full")
        return real_move(s, d)

    monkeypatch.setattr(tgd.shutil, "move", flaky_move)
    res = gui._import_folder(tgd, home, src)
    assert (res["imported"], res["dupes"], res["failed"]) == (1, 0, 1)


def test_import_refuses_a_folder_that_contains_the_library(monkeypatch, tmp_path, gui_post):
    home = tmp_path / "Music" / "Library"
    home.mkdir(parents=True)
    monkeypatch.setattr(tgd, "load_config", lambda: {"home_music_folder": str(home)})
    status, body = gui_post("/import-folder", {"path": str(tmp_path / "Music")})
    assert status == 400
    assert "contain" in body["error"]
