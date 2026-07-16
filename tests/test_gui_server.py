"""Tests for TGDownloader_GUI helpers: version compare, range parsing,
request-origin guard, and traversal-guarded file resolution."""

import TGDownloader_GUI as gui


# ── Version parsing (update check) ────────────────────────────────────────────

def test_parse_version_basic():
    assert gui._parse_version("v1.2.3") == (1, 2, 3)
    assert gui._parse_version("1.2") == (1, 2)
    assert gui._parse_version("release-2.0.1-beta") == (2, 0, 1)
    assert gui._parse_version("") == ()
    assert gui._parse_version(None) == ()


def test_parse_version_ordering():
    assert gui._parse_version("1.2.1") > gui._parse_version("1.2")
    assert gui._parse_version("v2.0.0") > gui._parse_version("v1.9.9")


# ── HTTP Range parsing ────────────────────────────────────────────────────────

def test_range_none():
    assert gui._parse_range_header("", 100) == (0, 99)


def test_range_normal():
    assert gui._parse_range_header("bytes=10-19", 100) == (10, 19)


def test_range_open_ended():
    assert gui._parse_range_header("bytes=10-", 100) == (10, 99)


def test_range_end_clamped_to_file_size():
    assert gui._parse_range_header("bytes=0-9999", 100) == (0, 99)


def test_range_suffix():
    # bytes=-500 means "the last 500 bytes" (the old parser served byte 0+).
    assert gui._parse_range_header("bytes=-30", 100) == (70, 99)


def test_range_suffix_larger_than_file():
    assert gui._parse_range_header("bytes=-500", 100) == (0, 99)


def test_range_unsatisfiable_returns_none():
    assert gui._parse_range_header("bytes=100-", 100) is None
    assert gui._parse_range_header("bytes=50-40", 100) is None
    assert gui._parse_range_header("bytes=-0", 100) is None


def test_range_malformed_serves_full_file():
    assert gui._parse_range_header("bytes=abc-def", 100) == (0, 99)
    assert gui._parse_range_header("bytes=-", 100) == (0, 99)


# ── CSRF / DNS-rebinding request guard ────────────────────────────────────────

def test_local_request_allowed():
    host = f"127.0.0.1:{gui.HTTP_PORT}"
    assert gui._is_local_request(host, None)                        # plain GET
    assert gui._is_local_request(host, f"http://{host}")            # POST / WS
    assert gui._is_local_request(f"localhost:{gui.HTTP_PORT}",
                                 f"http://localhost:{gui.HTTP_PORT}")
    assert gui._is_local_request(None, None)                        # HTTP/1.0


def test_dns_rebinding_host_rejected():
    assert not gui._is_local_request(f"evil.example:{gui.HTTP_PORT}", None)
    assert not gui._is_local_request("evil.example", None)


def test_foreign_origin_rejected():
    host = f"127.0.0.1:{gui.HTTP_PORT}"
    assert not gui._is_local_request(host, "https://evil.example")
    assert not gui._is_local_request(host, "null")


# ── Traversal-guarded file resolution ─────────────────────────────────────────

def test_resolve_in_dir_normal(tmp_path):
    (tmp_path / "track.mp3").write_bytes(b"x")
    assert gui._resolve_in_dir(tmp_path, "track.mp3").name == "track.mp3"


def test_resolve_in_dir_blocks_traversal(tmp_path):
    inner = tmp_path / "album"
    inner.mkdir()
    (tmp_path / "secret.txt").write_bytes(b"x")
    assert gui._resolve_in_dir(inner, "../secret.txt") is None
    assert gui._resolve_in_dir(inner, "..\\secret.txt") is None


def test_resolve_in_dir_case_insensitive_fallback(tmp_path):
    (tmp_path / "Track.Mp3").write_bytes(b"x")
    found = gui._resolve_in_dir(tmp_path, "track.mp3")
    assert found is not None and found.name == "Track.Mp3"


def test_resolve_in_dir_missing(tmp_path):
    assert gui._resolve_in_dir(tmp_path, "nope.mp3") is None
    assert gui._resolve_in_dir(tmp_path, "") is None
