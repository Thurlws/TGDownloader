"""Config / manifest / session writes: atomic, locked, and never silently
overwriting data another writer changed or that failed to parse."""

import json
import threading
import time

import pytest

import TGDownloader as tgd
import TGDownloader_GUI as gui
import tgd_common


# ── atomic_write_text ─────────────────────────────────────────────────────────

def test_atomic_write_replaces_content_and_leaves_no_temp(tmp_path):
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    tgd_common.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "new"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_atomic_write_failure_keeps_the_old_file(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(tgd_common.os, "replace", boom)
    with pytest.raises(OSError):
        tgd_common.atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_reader_never_sees_a_half_written_config(tmp_path):
    # With a plain write_text a reader could catch the file truncated
    # mid-write, fall back to defaults, and save those over the settings.
    path = tmp_path / "cfg.json"
    big = {"home_music_folder": "/music", "pad": "x" * 200_000}
    tgd_common.save_config(big, path)
    stop = threading.Event()
    bad: list = []

    def reader():
        while not stop.is_set():
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except ValueError as exc:          # partial JSON: a torn write
                bad.append(exc)
            except OSError:
                pass     # Windows: the file is briefly locked during a replace

    t = threading.Thread(target=reader)
    t.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        tgd_common.save_config(big, path)
    stop.set()
    t.join()
    assert bad == []


# ── Config lock ───────────────────────────────────────────────────────────────

def test_concurrent_config_updates_keep_every_change(tmp_path):
    path = tmp_path / "cfg.json"
    tgd_common.save_config({}, path)
    threads = [threading.Thread(target=tgd_common.update_config,
                                args=({f"k{i}": i}, path)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    cfg = tgd_common.load_config(path)
    assert all(cfg.get(f"k{i}") == i for i in range(20))


def test_unparseable_config_is_kept_before_it_can_be_overwritten(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text('{"api_hash": "abc", broken', encoding="utf-8")
    cfg = tgd_common.load_config(path)
    assert cfg["home_music_folder"] is None               # defaults
    backup = tmp_path / "cfg.json.corrupt"
    assert backup.read_text(encoding="utf-8") == '{"api_hash": "abc", broken'
    tgd_common.save_config(cfg, path)                      # the old file is gone...
    assert backup.exists()                                 # ...but not lost


# ── Manifest ──────────────────────────────────────────────────────────────────

def test_backend_save_does_not_undo_a_history_removal(tmp_path):
    # The backend loads the manifest when a run starts and keeps it in memory.
    tgd.save_manifest(tmp_path, {"http://a": {"status": "complete"},
                                 "http://b": {"status": "complete"}})
    run_copy = tgd.load_manifest(tmp_path)
    # Mid-run, the user removes "b" from the history in the GUI.
    tgd.update_manifest(tmp_path, lambda mf: mf.pop("http://b", None))
    # The backend then finishes "c". It used to save its stale copy whole,
    # which put "b" back.
    tgd.mark_url_complete(tmp_path, run_copy, "http://c", "Artist", [])
    on_disk = tgd.load_manifest(tmp_path)
    assert set(on_disk) == {"http://a", "http://c"}
    assert "http://c" in run_copy          # the in-memory copy is updated too


def test_unparseable_manifest_is_kept(tmp_path):
    mdir = tmp_path / ".tgdownloader"
    mdir.mkdir()
    (mdir / "manifest.json").write_text("{broken", encoding="utf-8")
    assert tgd.load_manifest(tmp_path) == {}
    assert (mdir / "manifest.json.corrupt").read_text(encoding="utf-8") == "{broken"


# ── Saved queue sessions (through the HTTP handler) ───────────────────────────

def test_concurrent_session_saves_keep_every_session(tmp_path, monkeypatch, gui_post):
    monkeypatch.setattr(gui, "SESSIONS_FILE", tmp_path / "tg_sessions.json")
    results = []

    def save(i):
        results.append(gui_post("/sessions", {"name": f"s{i}", "entries": [i]}))

    threads = [threading.Thread(target=save, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(status == 200 for status, _ in results)
    assert gui._load_sessions() == {f"s{i}": [i] for i in range(12)}
