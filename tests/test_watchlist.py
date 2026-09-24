"""Artist watchlist: no lost updates while a check runs, and no empty
baseline when Deezer cannot be reached."""

import threading

import TGDownloader_GUI as gui


class _MemStore:
    """In-memory stand-in for the SQLite-backed watchlist."""

    def __init__(self):
        self.data = {}

    def load(self):
        return {k: dict(v) for k, v in self.data.items()}

    def save(self, wl):
        self.data = {k: dict(v) for k, v in wl.items()}


def _use_mem_store(monkeypatch):
    store = _MemStore()
    monkeypatch.setattr(gui, "_load_watchlist", store.load)
    monkeypatch.setattr(gui, "_save_watchlist", store.save)
    return store


def _album(aid):
    return {"album_id": str(aid), "title": f"A{aid}", "cover": "", "link": "",
            "release_date": "2024-01-01", "record_type": "album"}


def test_add_refuses_when_the_lookup_fails(monkeypatch):
    # Saving [] as the baseline flagged the whole back catalogue as "new".
    store = _use_mem_store(monkeypatch)
    monkeypatch.setattr(gui, "_fetch_artist_albums", lambda aid: None)
    res = gui._watchlist_add("123", "Band", "")
    assert "error" in res
    assert store.data == {}


def test_add_accepts_an_artist_with_no_albums(monkeypatch):
    store = _use_mem_store(monkeypatch)
    monkeypatch.setattr(gui, "_fetch_artist_albums", lambda aid: [])
    assert gui._watchlist_add("123", "Band", "")["ok"]
    assert store.data["123"]["known_album_ids"] == []


def test_add_rejects_a_non_numeric_id(monkeypatch):
    _use_mem_store(monkeypatch)
    monkeypatch.setattr(gui, "_fetch_artist_albums", lambda aid: [])
    assert "error" in gui._watchlist_add("1/../x", "Band", "")


def test_fetch_treats_a_deezer_error_payload_as_failure(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}'

    monkeypatch.setattr(gui.urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    assert gui._fetch_artist_albums("123") is None


def test_check_does_not_undo_changes_made_while_it_runs(monkeypatch):
    store = _use_mem_store(monkeypatch)
    store.data = {"1": {"name": "One", "known_album_ids": ["10"]},
                  "2": {"name": "Two", "known_album_ids": []}}
    in_fetch = threading.Event()
    release = threading.Event()

    def slow_fetch(aid):
        if aid == "1":
            in_fetch.set()
            release.wait(5)
        return [_album(10), _album(11)] if aid == "1" else [_album(20)]

    monkeypatch.setattr(gui, "_fetch_artist_albums", slow_fetch)
    result = {}
    t = threading.Thread(target=lambda: result.update(gui._watchlist_check()))
    t.start()
    assert in_fetch.wait(5)
    # While the check is waiting on Deezer, the user watches a new artist,
    # stops watching "2", and marks album 11 as seen.
    monkeypatch.setattr(gui, "_fetch_artist_albums",
                        lambda aid: slow_fetch(aid) if aid != "3" else [_album(30)])
    gui._watchlist_add("3", "Three", "")
    gui._watchlist_remove("2")
    gui._watchlist_mark_seen("1", ["11"])
    release.set()
    t.join(5)

    assert set(store.data) == {"1", "3"}                  # add + remove kept
    assert "11" in store.data["1"]["known_album_ids"]     # mark_seen kept
    assert store.data["1"]["last_checked"] > 0
    # "2" was removed and 11 marked seen mid-check: neither is reported.
    assert [r["album_id"] for r in result["new_releases"]] == []


def test_check_skips_artists_whose_lookup_failed(monkeypatch):
    store = _use_mem_store(monkeypatch)
    store.data = {"1": {"name": "One", "known_album_ids": [], "last_checked": 0}}
    monkeypatch.setattr(gui, "_fetch_artist_albums", lambda aid: None)
    res = gui._watchlist_check()
    assert res["new_releases"] == [] and res["checked"] == 0 and res["failed"] == 1
    assert store.data["1"]["last_checked"] == 0
