"""Secrets stay out of backups and debug bundles, and restores never take
credentials from a zip."""

import json

import TGDownloader_GUI as gui
import tgd_common


def test_acoustid_key_is_a_secret():
    # Keyring storage and every redaction list derive from SECRET_KEYS.
    assert "acoustid_api_key" in tgd_common.SECRET_KEYS
    assert set(tgd_common.SECRET_KEYS) <= set(tgd_common.REDACTED_KEYS)
    assert {"api_id", "spotify_client_id"} <= set(tgd_common.REDACTED_KEYS)


def test_redacted_config_drops_every_secret(tmp_path, monkeypatch):
    cfg = {k: "SECRET-VALUE" for k in tgd_common.REDACTED_KEYS}
    cfg.update({"home_music_folder": "/music", "theme": "dark"})
    path = tmp_path / "tg_audio_config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(tgd_common, "CONFIG_FILE", path)

    out = gui._redacted_config_json()

    assert "SECRET-VALUE" not in out
    assert json.loads(out) == {"home_music_folder": "/music", "theme": "dark"}


def test_redacted_config_without_a_config_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tgd_common, "CONFIG_FILE", tmp_path / "missing.json")
    assert gui._redacted_config_json() is None
