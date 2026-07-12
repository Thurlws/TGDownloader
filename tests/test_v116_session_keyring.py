"""Tests for opt-in Telegram-session keyring storage (v1.16.0).

The session helpers live in tgd_common so both processes share them. These use a
fake in-memory keyring and a synthetic Telethon SQLite session; no real Telegram.
The load in these tests: nothing happens unless use_keyring is on.
"""

import base64
import json
import socket
import sqlite3
import struct

import tgd_common


class _FakeKeyring:
    def __init__(self):
        self.store = {}
    def set_password(self, service, key, value):
        self.store[(service, key)] = value
    def get_password(self, service, key):
        return self.store.get((service, key))
    def delete_password(self, service, key):
        self.store.pop((service, key), None)


def _use_keyring_config(tmp_path, monkeypatch, *, on=True):
    """Point tgd_common at a temp config (use_keyring toggle) + temp DATA_DIR."""
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"use_keyring": on}), encoding="utf-8")
    monkeypatch.setattr(tgd_common, "CONFIG_FILE", cfg)
    monkeypatch.setattr(tgd_common, "DATA_DIR", tmp_path)


def _write_session_db(tmp_path, auth_key=b"\x11" * 256):
    db = tmp_path / "tg_audio_session.session"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sessions "
                 "(dc_id integer, server_address text, port integer, auth_key blob)")
    conn.execute("INSERT INTO sessions VALUES (?,?,?,?)",
                 (2, "149.154.167.51", 443, auth_key))
    conn.commit()
    conn.close()
    return db


# ── Reading the plaintext file → StringSession ────────────────────────────────

def test_read_session_file_string_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(tgd_common, "DATA_DIR", tmp_path)
    _write_session_db(tmp_path, auth_key=b"\x22" * 256)
    s = tgd_common.read_session_file_string()
    assert s and s.startswith("1")
    raw = base64.urlsafe_b64decode(s[1:])
    dc_id, ip, port, auth_key = struct.unpack(">B4sH256s", raw)
    assert dc_id == 2 and port == 443
    assert ip == socket.inet_aton("149.154.167.51")
    assert auth_key == b"\x22" * 256


def test_read_session_file_string_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(tgd_common, "DATA_DIR", tmp_path)
    assert tgd_common.read_session_file_string() is None


# ── Keyring storage is strictly opt-in ────────────────────────────────────────

def test_no_keyring_use_is_a_no_op(tmp_path, monkeypatch):
    _use_keyring_config(tmp_path, monkeypatch, on=False)
    fake = _FakeKeyring()
    monkeypatch.setattr(tgd_common, "_keyring", lambda: fake)
    assert tgd_common.save_session_string("1abc") is False   # refused
    assert tgd_common.load_session_string() is None
    assert fake.store == {}                                  # nothing written


def test_keyring_save_load_clear_round_trip(tmp_path, monkeypatch):
    _use_keyring_config(tmp_path, monkeypatch, on=True)
    monkeypatch.setattr(tgd_common, "_keyring", lambda: _FakeKeyring())
    # one shared fake across calls
    fake = tgd_common._keyring()
    monkeypatch.setattr(tgd_common, "_keyring", lambda: fake)

    assert tgd_common.save_session_string("1sessionblob") is True
    assert tgd_common.load_session_string() == "1sessionblob"
    tgd_common.clear_session_string()
    assert tgd_common.load_session_string() is None


# ── has_session across both stores ────────────────────────────────────────────

def test_has_session_file_or_keyring(tmp_path, monkeypatch):
    _use_keyring_config(tmp_path, monkeypatch, on=True)
    fake = _FakeKeyring()
    monkeypatch.setattr(tgd_common, "_keyring", lambda: fake)

    assert tgd_common.has_session() is False           # neither store
    _write_session_db(tmp_path)
    assert tgd_common.has_session() is True             # file only
    tgd_common.remove_session_file()
    assert tgd_common.has_session() is False
    fake.set_password(tgd_common._KEYRING_SERVICE, tgd_common._SESSION_KEYRING_KEY, "1x")
    assert tgd_common.has_session() is True             # keyring only


# ── Migration: plaintext file → keyring, then file removed ────────────────────

def test_migrate_moves_file_into_keyring(tmp_path, monkeypatch):
    _use_keyring_config(tmp_path, monkeypatch, on=True)
    fake = _FakeKeyring()
    monkeypatch.setattr(tgd_common, "_keyring", lambda: fake)
    _write_session_db(tmp_path, auth_key=b"\x33" * 256)

    assert tgd_common.migrate_session_to_keyring() is True
    assert not tgd_common.session_file().exists()        # plaintext removed
    stored = tgd_common.load_session_string()
    assert stored and stored.startswith("1")             # keyring now serves it
    assert tgd_common.has_session() is True


def test_migrate_noop_without_keyring_flag(tmp_path, monkeypatch):
    _use_keyring_config(tmp_path, monkeypatch, on=False)
    fake = _FakeKeyring()
    monkeypatch.setattr(tgd_common, "_keyring", lambda: fake)
    _write_session_db(tmp_path)
    assert tgd_common.migrate_session_to_keyring() is False
    assert tgd_common.session_file().exists()             # file left untouched
    assert fake.store == {}


def test_migrate_removes_redundant_file_when_already_in_keyring(tmp_path, monkeypatch):
    _use_keyring_config(tmp_path, monkeypatch, on=True)
    fake = _FakeKeyring()
    fake.set_password(tgd_common._KEYRING_SERVICE, tgd_common._SESSION_KEYRING_KEY, "1already")
    monkeypatch.setattr(tgd_common, "_keyring", lambda: fake)
    _write_session_db(tmp_path)
    # Keyring already holds a session → migration just drops the redundant file.
    assert tgd_common.migrate_session_to_keyring() is False
    assert not tgd_common.session_file().exists()
    assert tgd_common.load_session_string() == "1already"
