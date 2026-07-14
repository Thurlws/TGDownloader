"""Tests for the self-update apply helpers.

Covers the pure/testable pieces — staging a release zip (with zip-slip and
shape guards), the download validator, the swap-script generation, and the
frozen-only gate. The actual file swap + relaunch runs only in a packaged
Windows build and is not exercised here.
"""

import pathlib
import zipfile

import pytest

import TGDownloader_GUI as gui


def _make_bundle_zip(path, *, with_exe=True, with_internal=True, evil=False):
    with zipfile.ZipFile(path, "w") as zf:
        if with_exe:
            zf.writestr("TGDownloader.exe", b"MZ fake exe")
        if with_internal:
            zf.writestr("_internal/base_library.zip", b"stuff")
            zf.writestr("_internal/gui.html", b"<html>")
        if evil:
            zf.writestr("../escape.txt", b"pwned")
    return path


# ── Staging ───────────────────────────────────────────────────────────────────

def test_stage_update_returns_bundle_root(tmp_path):
    zip_path = _make_bundle_zip(tmp_path / "rel.zip")
    staging  = tmp_path / "staging"
    root = gui._stage_update(zip_path, staging)
    assert (root / "TGDownloader.exe").is_file()
    assert (root / "_internal").is_dir()


def test_stage_update_rejects_missing_exe(tmp_path):
    zip_path = _make_bundle_zip(tmp_path / "rel.zip", with_exe=False)
    with pytest.raises(ValueError, match="no TGDownloader.exe"):
        gui._stage_update(zip_path, tmp_path / "staging")


def test_stage_update_rejects_missing_internal(tmp_path):
    zip_path = _make_bundle_zip(tmp_path / "rel.zip", with_internal=False)
    with pytest.raises(ValueError, match="_internal"):
        gui._stage_update(zip_path, tmp_path / "staging")


def test_stage_update_blocks_zip_slip(tmp_path):
    zip_path = _make_bundle_zip(tmp_path / "rel.zip", evil=True)
    with pytest.raises(ValueError, match="unsafe path"):
        gui._stage_update(zip_path, tmp_path / "staging")
    # And nothing escaped the staging dir.
    assert not (tmp_path / "escape.txt").exists()


# ── Download validation ───────────────────────────────────────────────────────

def test_download_zip_validates_and_checks_size(tmp_path):
    src = _make_bundle_zip(tmp_path / "rel.zip")
    dest = tmp_path / "got.zip"
    url = src.resolve().as_uri()
    gui._download_zip(url, dest, expected_size=src.stat().st_size)
    assert zipfile.is_zipfile(dest)


def test_download_zip_rejects_size_mismatch(tmp_path):
    src = _make_bundle_zip(tmp_path / "rel.zip")
    url = src.resolve().as_uri()
    with pytest.raises(ValueError, match="size mismatch"):
        gui._download_zip(url, tmp_path / "got.zip", expected_size=999_999)


def test_download_zip_rejects_non_zip(tmp_path):
    plain = tmp_path / "notzip.bin"
    plain.write_bytes(b"just some bytes, definitely not a zip archive")
    url = plain.resolve().as_uri()
    with pytest.raises(ValueError, match="not a valid zip"):
        gui._download_zip(url, tmp_path / "got.zip")


# ── Swap-script generation ────────────────────────────────────────────────────

def test_build_update_script_shape():
    bat = gui._build_update_script(4242, pathlib.Path(r"C:\App"),
                                   pathlib.Path(r"C:\App\_update\bundle"))
    assert 'set "PID=4242"' in bat
    assert r'set "APP=C:\App"' in bat
    assert r'set "NEW=C:\App\_update\bundle"' in bat
    assert "_internal.old" in bat and "TGDownloader.old.exe" in bat   # backups
    assert 'start "" "%APP%\\TGDownloader.exe"' in bat                # relaunch
    assert ":rollback" in bat                                         # rollback path
    assert 'del "%~f0"' in bat                                        # self-delete


# ── Frozen-only gate ──────────────────────────────────────────────────────────

def test_apply_update_refuses_when_not_frozen():
    # The test process isn't a frozen build, so apply must refuse cleanly with no
    # side effects rather than trying to swap anything.
    res = gui._apply_update()
    assert res["ok"] is False
    assert "packaged" in res["error"].lower()


def test_cleanup_leftovers_noop_when_not_frozen():
    # Must not raise (and must not touch anything) outside a frozen build.
    gui._cleanup_update_leftovers()


# ── Update checksum verification (defensive; skips when no .sha256 shipped) ────

def test_parse_sha256_valid_and_invalid():
    good = "a" * 64
    assert gui._parse_sha256(f"{good}  TGDownloader.zip") == good
    assert gui._parse_sha256(f"{good.upper()}\n") == good        # lower-cased
    assert gui._parse_sha256("") is None
    assert gui._parse_sha256("deadbeef") is None                 # too short
    assert gui._parse_sha256("z" * 64) is None                   # non-hex


def test_verify_update_checksum_accepts_matching(tmp_path):
    zip_path = _make_bundle_zip(tmp_path / "rel.zip")
    digest   = gui._sha256_file(zip_path)
    sha = tmp_path / "rel.zip.sha256"
    sha.write_text(f"{digest}  rel.zip\n", encoding="ascii")
    gui._verify_update_checksum(zip_path, sha.resolve().as_uri())   # must not raise


def test_verify_update_checksum_rejects_tampered(tmp_path):
    zip_path = _make_bundle_zip(tmp_path / "rel.zip")
    sha = tmp_path / "rel.zip.sha256"
    sha.write_text(("b" * 64) + "  rel.zip\n", encoding="ascii")     # wrong hash
    with pytest.raises(ValueError, match="mismatch"):
        gui._verify_update_checksum(zip_path, sha.resolve().as_uri())


def test_verify_update_checksum_absent_is_noop(tmp_path):
    zip_path = _make_bundle_zip(tmp_path / "rel.zip")
    gui._verify_update_checksum(zip_path, "")     # older release, no hash → skip


def test_verify_update_checksum_rejects_malformed(tmp_path):
    zip_path = _make_bundle_zip(tmp_path / "rel.zip")
    sha = tmp_path / "rel.zip.sha256"
    sha.write_text("not-a-valid-hash\n", encoding="ascii")
    with pytest.raises(ValueError, match="malformed"):
        gui._verify_update_checksum(zip_path, sha.resolve().as_uri())
