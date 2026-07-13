"""Tests for the JSON-lines backend protocol.

The backend writes one JSON object per stdout line; the GUI parses it back.
These cover the GUI-side parser's accept/reject rules and the backend's raw
progress snapshot.
"""

import json

import TGDownloader as tgd
import TGDownloader_GUI as gui


# ── GUI-side parser: what counts as a protocol message ────────────────────────

def test_parse_log_line():
    line = json.dumps({"type": "log", "text": "hello\n"})
    assert gui._parse_backend_line(line) == {"type": "log", "text": "hello\n"}


def test_parse_result_line():
    msg = {"type": "result", "url": "u", "artist": "a", "status": "ok"}
    assert gui._parse_backend_line(json.dumps(msg)) == msg


def test_parse_progress_line():
    msg = {"type": "progress", "filesDone": 1, "filesTotal": 3, "speedMBs": 2.5}
    assert gui._parse_backend_line(json.dumps(msg)) == msg


def test_parse_paused_resumed():
    assert gui._parse_backend_line('{"type": "paused"}')  == {"type": "paused"}
    assert gui._parse_backend_line('{"type": "resumed"}') == {"type": "resumed"}


def test_plain_text_is_not_protocol():
    # Human log text and merged-in stderr must fall through as raw log lines.
    assert gui._parse_backend_line("Downloading album...\n") is None
    assert gui._parse_backend_line("Traceback (most recent call last):\n") is None


def test_malformed_json_is_not_protocol():
    assert gui._parse_backend_line('{"type": "log", "text":') is None


def test_unknown_type_is_not_protocol():
    assert gui._parse_backend_line('{"type": "wat"}') is None
    assert gui._parse_backend_line('{"text": "no type here"}') is None


def test_json_array_is_not_protocol():
    assert gui._parse_backend_line("[1, 2, 3]") is None


# ── Backend-side: raw progress snapshot ───────────────────────────────────────

def test_progress_snapshot_shape():
    tgd._progress = {"a": (500_000, 1_000_000), "b": (1_048_576, 1_048_576)}
    snap = tgd._progress_snapshot(start_time=0.0, total_files=2, done_files=1)
    assert snap["filesDone"] == 1 and snap["filesTotal"] == 2
    assert snap["mbDone"] == 1.5 and snap["mbTotal"] == 2.0
    assert set(snap) == {"filesDone", "filesTotal", "mbDone", "mbTotal",
                         "speedMBs", "pct", "eta"}


def test_progress_snapshot_empty_is_safe():
    tgd._progress = {}
    snap = tgd._progress_snapshot(start_time=0.0, total_files=0, done_files=0)
    assert snap["mbDone"] == 0.0 and snap["mbTotal"] == 0.0 and snap["pct"] == 0
