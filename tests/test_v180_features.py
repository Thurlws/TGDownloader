"""Tests for v1.8.0 pipeline & polish helpers: the post-download hook command
formatter and the Telegram flood-wait health summary."""

import sys
import time

import TGDownloader as dl
import TGDownloader_GUI as gui


# ── Post-download hook command formatting ────────────────────────────────────

# Placeholders become quoted references to the TGD_* environment variables, so
# third-party metadata (artist names, playlist titles, URLs) is never parsed
# as shell syntax.

def test_hook_command_placeholders_posix():
    out = dl._format_hook_command('notify "{artist}" {status} {folder}', windows=False)
    assert out == 'notify "${TGD_ARTIST}" "${TGD_STATUS}" "${TGD_FOLDER}"'


def test_hook_command_placeholders_windows():
    out = dl._format_hook_command('notify "{artist}" {status} {folder}', windows=True)
    assert out == 'notify "!TGD_ARTIST!" "!TGD_STATUS!" "!TGD_FOLDER!"'


def test_hook_command_appends_folder_when_no_placeholder():
    assert dl._format_hook_command("beet import", windows=False) == 'beet import "${TGD_FOLDER}"'
    assert dl._format_hook_command("beet import", windows=True) == 'beet import "!TGD_FOLDER!"'


def test_hook_command_url_placeholder():
    assert dl._format_hook_command("log {url}", windows=False) == 'log "${TGD_URL}"'


def test_hook_command_single_quoted_placeholder_posix():
    out = dl._format_hook_command("notify-send 'Got {artist}!'", windows=False)
    assert out == "notify-send 'Got '\"${TGD_ARTIST}\"'!'"


def test_hook_command_escaped_quotes_keep_context():
    # sh: \" inside "..." does not close it.  cmd: ^" outside quotes does not open one.
    assert (dl._format_hook_command(r'echo "a \" b {url}"', windows=False)
            == r'echo "a \" b ${TGD_URL}"')
    assert (dl._format_hook_command('echo ^"x {url}', windows=True)
            == 'echo ^"x "!TGD_URL!"')


def test_hook_noop_when_unconfigured(tmp_path):
    # Must not raise and must not spawn anything with an empty command.
    assert dl._run_post_download_hook({}, tmp_path, "u", "a", "ok") is None
    assert dl._run_post_download_hook(
        {"post_download_command": "  "}, tmp_path, "u", "a", "ok") is None


def test_hook_passes_hostile_values_as_data(tmp_path):
    # Runs the real shell (sh, or cmd on Windows). The artist value carries
    # shell syntax that used to execute when substituted into the command.
    canary = tmp_path / "pwned"
    artist = (f"AC&DC | 100% (live) ^ !x! & echo x > {canary} & "
              f"$(echo x > {canary}) `echo x > {canary}`; x' y")
    if sys.platform != "win32":
        artist += ' "q"'      # cmd has no way to pass a literal quote safely
    out_file = tmp_path / "args.txt"
    script = tmp_path / "write_args.py"
    script.write_text("import pathlib, sys\n"
                      "pathlib.Path(sys.argv[1]).write_text("
                      "'\\n'.join(sys.argv[2:]), encoding='utf-8')\n")
    cmd = f'"{sys.executable}" "{script}" "{out_file}" {{artist}} "{{status}}" {{url}}'
    url = "https://example.com/album/1?a=1&b=2"
    proc = dl._run_post_download_hook({"post_download_command": cmd},
                                      tmp_path, url, artist, "ok")
    assert proc is not None
    assert proc.wait(timeout=60) == 0
    assert out_file.read_text(encoding="utf-8").split("\n") == [artist, "ok", url]
    assert not canary.exists()


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
