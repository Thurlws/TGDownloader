"""Tests for the parallel download workers: connect spacing and temp-file
handling in download_all_async."""

import asyncio
import types

from telethon.tl.types import DocumentAttributeFilename

import TGDownloader as dl


# ── Connect gate ──────────────────────────────────────────────────────────────

def test_connect_gate_spaces_the_first_wave():
    now = [100.0]
    gate = dl._ConnectGate(4.0, clock=lambda: now[0])
    assert [gate.reserve() for _ in range(3)] == [0.0, 4.0, 8.0]


def test_connect_gate_wait_does_not_grow_with_position():
    # A worker that reaches its turn late must not sleep again for its index:
    # the old code slept index * 4s inside a worker slot, so track 50 of an
    # album idled for 200s after it finally got a worker.
    now = [0.0]
    gate = dl._ConnectGate(4.0, clock=lambda: now[0])
    for _ in range(50):
        gate.reserve()
    now[0] = 1000.0                 # long after the last reserved slot
    assert gate.reserve() == 0.0
    assert gate.reserve() == 4.0


def test_connect_gate_waits_for_a_slot_still_ahead():
    now = [0.0]
    gate = dl._ConnectGate(4.0, clock=lambda: now[0])
    gate.reserve()                  # slot at t=0
    now[0] = 1.5
    assert gate.reserve() == 2.5    # next slot is t=4


# ── Temp files: partial downloads and same-named tracks ──────────────────────
# download_all_async driven with a fake Telethon client, so the real worker,
# retry and rename code runs without a network.

class _Msg:
    def __init__(self, mid, name, payload):
        self.id = mid
        self.payload = payload
        self.document = types.SimpleNamespace(
            id=mid, size=len(payload),
            attributes=[DocumentAttributeFilename(file_name=name)])


class _FakeClient:
    fail_ids: set = set()

    def __init__(self, *a, **k):
        pass

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def iter_download(self, message, request_size=None):
        half = len(message.payload) // 2
        yield message.payload[:half]
        if message.id in self.fail_ids:
            raise ConnectionError("connection lost mid-file")
        yield message.payload[half:]


class _FakeSession:
    def __init__(self, *a):
        pass

    @staticmethod
    def save(_session):
        return ""


def _download(monkeypatch, tmp_dir, msgs, fail_ids=()):
    monkeypatch.setattr(dl, "TelegramClient", _FakeClient)
    monkeypatch.setattr(dl, "StringSession", _FakeSession)
    monkeypatch.setattr(dl, "_get_api_creds", lambda: (1, "hash"))
    monkeypatch.setattr(dl, "_CONNECT_GAP_S", 0.0)
    monkeypatch.setattr(_FakeClient, "fail_ids", set(fail_ids))
    events = [types.SimpleNamespace(message=m) for m in msgs]
    client = types.SimpleNamespace(session=None)
    return asyncio.run(dl.download_all_async(
        client, events, tmp_dir, {"max_parallel_downloads": 3}))


def test_failed_download_leaves_no_partial_file(monkeypatch, tmp_path):
    # The sort step files every audio file in the temp dir; a truncated
    # "02 Broken.flac" left there used to end up in the library.
    msgs = [_Msg(1, "01 Good.flac", b"A" * 100),
            _Msg(2, "02 Broken.flac", b"B" * 100)]
    got = _download(monkeypatch, tmp_path, msgs, fail_ids={2})
    assert [p.name for p in got] == ["01 Good.flac"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["01 Good.flac"]
    assert (tmp_path / "01 Good.flac").read_bytes() == b"A" * 100


def test_same_filename_from_two_messages_keeps_both(monkeypatch, tmp_path):
    msgs = [_Msg(1, "Intro.flac", b"1" * 50), _Msg(2, "Intro.flac", b"2" * 50)]
    got = _download(monkeypatch, tmp_path, msgs)
    assert sorted(p.name for p in got) == ["Intro (1).flac", "Intro.flac"]
    assert {p.read_bytes() for p in got} == {b"1" * 50, b"2" * 50}
    assert not list(tmp_path.glob("*.part"))


def test_fresh_url_tmp_drops_leftovers_from_a_stopped_run(monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "_DATA_DIR", tmp_path)
    stale = tmp_path / "tg_tmp_downloads" / "url_1"
    stale.mkdir(parents=True)
    (stale / "Other Artist - Track.flac").write_bytes(b"x")
    d = dl._fresh_url_tmp(1)
    assert d == stale and d.is_dir() and not any(d.iterdir())
