"""Tests for v1.8.0 pipeline & polish helpers: the post-download hook command
formatter and the Telegram flood-wait health summary."""

import time

import TGDownloader as dl
import TGDownloader_GUI as gui


# ── Post-download hook command formatting ────────────────────────────────────

def test_hook_command_placeholders():
    out = dl._format_hook_command(
        'notify "{artist}" {status} {folder}',
        dest=r"C:\Music\Artists\A\Album", artist="A", status="ok", url="http://x")
    assert out == 'notify "A" ok C:\\Music\\Artists\\A\\Album'


def test_hook_command_appends_folder_when_no_placeholder():
    out = dl._format_hook_command(
        "beet import", dest="/music/a", artist="A", status="ok", url="u")
    assert out == 'beet import "/music/a"'


def test_hook_command_url_placeholder():
    out = dl._format_hook_command(
        "log {url}", dest="/d", artist="", status="partial", url="https://z/1")
    assert out == "log https://z/1"


def test_hook_noop_when_unconfigured(tmp_path):
    # Must not raise and must not spawn anything with an empty command.
    dl._run_post_download_hook({}, tmp_path, "u", "a", "ok")
    dl._run_post_download_hook({"post_download_command": "  "}, tmp_path, "u", "a", "ok")


# ── Flood-wait health summary ────────────────────────────────────────────────

def test_flood_health_windows_and_recent():
    now = time.time()
    events = [
        {"t": now - 100,    "wait": 30, "file": "a.flac"},   # within the hour
        {"t": now - 7000,   "wait": 60, "file": "b.flac"},   # within 24 h
        {"t": now - 90000,  "wait": 90, "file": "c.flac"},   # older than 24 h
    ]
    d = gui._flood_health(events, now=now)
    assert d["total_recorded"] == 3
    assert d["last_hour"] == 1
    assert d["last_24h"] == 2
    assert d["wait_secs_24h"] == 90            # 30 + 60
    assert d["recent"][0]["file"] == "c.flac"  # newest-last input → reversed
    assert len(d["recent"]) == 3


def test_flood_health_empty():
    d = gui._flood_health([], now=1000.0)
    assert d == {"total_recorded": 0, "last_hour": 0, "last_24h": 0,
                 "wait_secs_24h": 0, "recent": []}


def test_flood_regex_matches_backend_log_line():
    line = "  ⏳ Flood-wait 42s for 03 - Song Title.flac (attempt 2/8) — waiting…\n"
    m = gui._FLOOD_RE.search(line)
    assert m and m.group(1) == "42" and m.group(2) == "03 - Song Title.flac"
