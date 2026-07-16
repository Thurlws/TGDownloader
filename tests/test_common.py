"""Tests for tgd_common: config round-trip, credentials, version."""

import json
import re

import tgd_common


def test_version_is_semver_like():
    assert re.fullmatch(r"\d+\.\d+\.\d+", tgd_common.__version__)


def test_load_config_returns_defaults_when_file_missing(tmp_path):
    cfg = tgd_common.load_config(tmp_path / "nope.json")
    assert cfg["target_quality"] == "FLAC"
    assert cfg["use_keyring"] is False
    assert cfg["bot_username"] == ""


def test_load_config_merges_saved_values_over_defaults(tmp_path):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"target_quality": "MP3 320", "custom_key": 7}),
                 encoding="utf-8")
    cfg = tgd_common.load_config(p)
    assert cfg["target_quality"] == "MP3 320"
    assert cfg["custom_key"] == 7            # unknown keys survive
    assert cfg["max_queue"] == 10            # defaults still present


def test_load_config_survives_corrupt_file(tmp_path, caplog):
    p = tmp_path / "cfg.json"
    p.write_text("{not json", encoding="utf-8")
    cfg = tgd_common.load_config(p)
    assert cfg["target_quality"] == "FLAC"   # falls back to defaults
    assert any("Could not parse" in r.message for r in caplog.records)


def test_save_load_round_trip(tmp_path):
    p = tmp_path / "cfg.json"
    cfg = tgd_common.load_config(p)
    cfg["bot_username"] = "@SomeBot"
    cfg["api_id"] = 123
    tgd_common.save_config(cfg, p)
    again = tgd_common.load_config(p)
    assert again["bot_username"] == "@SomeBot"
    assert again["api_id"] == 123


def test_get_api_credentials_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(tgd_common, "CONFIG_FILE", tmp_path / "cfg.json")
    assert tgd_common.get_api_credentials() == (None, None)


def test_get_api_credentials_set(tmp_path, monkeypatch):
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps({"api_id": "42", "api_hash": "a" * 32}),
                 encoding="utf-8")
    monkeypatch.setattr(tgd_common, "CONFIG_FILE", p)
    assert tgd_common.get_api_credentials() == (42, "a" * 32)


def test_require_api_credentials_raises_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(tgd_common, "CONFIG_FILE", tmp_path / "cfg.json")
    try:
        tgd_common.require_api_credentials()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "setup wizard" in str(exc)


class _FakeKeyring:
    """In-memory stand-in for the keyring module."""
    def __init__(self):
        self.store = {}

    def set_password(self, service, key, value):
        self.store[(service, key)] = value

    def get_password(self, service, key):
        return self.store.get((service, key))


def test_keyring_overlay_round_trip(tmp_path, monkeypatch):
    fake = _FakeKeyring()
    monkeypatch.setattr(tgd_common, "_keyring", lambda: fake)
    p = tmp_path / "cfg.json"

    cfg = tgd_common.load_config(p)
    cfg["use_keyring"] = True
    cfg["api_hash"] = "b" * 32
    cfg["listenbrainz_token"] = "tok123"
    tgd_common.save_config(cfg, p)

    # Secrets must NOT be in the JSON on disk...
    on_disk = json.loads(p.read_text(encoding="utf-8"))
    assert on_disk["api_hash"] == ""
    assert on_disk["listenbrainz_token"] == ""
    # ...but the merged view restores them from the keyring.
    merged = tgd_common.load_config(p)
    assert merged["api_hash"] == "b" * 32
    assert merged["listenbrainz_token"] == "tok123"


def test_keyring_unavailable_keeps_secrets_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(tgd_common, "_keyring", lambda: None)
    p = tmp_path / "cfg.json"
    cfg = tgd_common.load_config(p)
    cfg["use_keyring"] = True
    cfg["api_hash"] = "c" * 32
    tgd_common.save_config(cfg, p)
    # Nothing is lost: without keyring the secret stays in the JSON.
    assert json.loads(p.read_text(encoding="utf-8"))["api_hash"] == "c" * 32
    assert tgd_common.load_config(p)["api_hash"] == "c" * 32
