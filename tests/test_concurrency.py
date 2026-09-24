"""Threaded-server races: liked songs, ratings, WebSocket sends, transcodes."""

import threading
import time
import types

import TGDownloader_GUI as gui


def _run_threads(target, args_list):
    threads = [threading.Thread(target=target, args=a) for a in args_list]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)


def test_concurrent_likes_are_all_kept(monkeypatch):
    store = {"items": []}

    def slow_load():
        items = list(store["items"])
        time.sleep(0.01)          # widen the load-modify-save window
        return items

    monkeypatch.setattr(gui, "_load_liked", slow_load)
    monkeypatch.setattr(gui, "_save_liked", lambda items: store.update(items=list(items)))
    _run_threads(gui._toggle_liked,
                 [({"path_hash": "ab", "name": f"{i}.flac"},) for i in range(10)])
    assert sorted(it["name"] for it in store["items"]) == sorted(f"{i}.flac" for i in range(10))


def test_concurrent_ratings_are_all_kept(tmp_path, monkeypatch):
    path = tmp_path / "ratings.json"
    real_load = gui._load_ratings

    def slow_load(p=None):
        data = real_load(p)
        time.sleep(0.01)
        return data

    monkeypatch.setattr(gui, "_load_ratings", slow_load)
    _run_threads(lambda e: gui._set_rating(e, path=path),
                 [({"path_hash": "ab", "name": f"{i}.flac", "rating": 3},) for i in range(10)])
    assert len(real_load(path)) == 10


def test_direct_ws_send_waits_for_a_broadcast_in_progress():
    pm = gui.ProcessManager()
    sent = []
    conn = types.SimpleNamespace(sendall=sent.append)
    pm._cl_lock.acquire()          # as if broadcast() were mid-send on this socket
    t = threading.Thread(target=pm.send, args=(conn, {"type": "pong"}))
    t.start()
    t.join(0.3)
    assert sent == []              # did not write into the middle of that frame
    pm._cl_lock.release()
    t.join(5)
    assert len(sent) == 1


def test_one_transcode_per_file_at_a_time(tmp_path, monkeypatch):
    src = tmp_path / "song.flac"
    src.write_bytes(b"flac")
    monkeypatch.setattr(gui, "_TRANSCODE_DIR", tmp_path / "cache")
    monkeypatch.setattr(gui, "_ffmpeg_exe", lambda: "ffmpeg")
    calls = []

    def fake_ffmpeg(cmd, **kw):
        calls.append(cmd)
        time.sleep(0.2)
        with open(cmd[-1], "ab") as fh:          # two writers would interleave here
            fh.write(b"mp3-data")
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(gui.subprocess, "run", fake_ffmpeg)
    results = []
    _run_threads(lambda: results.append(gui._transcode_to_mp3(src)), [(), ()])
    assert len(calls) == 1                       # the second request reused the first
    assert len(results) == 2 and results[0] == results[1]
    assert results[0].read_bytes() == b"mp3-data"
