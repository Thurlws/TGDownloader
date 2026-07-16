"""Guards for the v1.10.0 PWA assets: the manifest, service worker and icons
must exist and be well-formed, and the server's static route must be willing to
serve their MIME types. These are the files that make the app installable; if
one goes missing the install silently breaks, so pin them down."""

import json
import struct
from pathlib import Path

import tgd_common

STATIC = Path(tgd_common.__file__).resolve().parent / "static"


def test_manifest_valid_and_complete():
    man = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert man["name"] and man["start_url"] == "/" and man["display"] == "standalone"
    sizes = {i["sizes"] for i in man["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in man["icons"]:
        assert (STATIC / icon["src"].lstrip("/").split("/", 1)[1]).is_file()


def test_service_worker_present_and_has_fetch_handler():
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    # A fetch handler is what makes the app installable.
    assert "addEventListener('fetch'" in sw


def _png_dims(path: Path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def test_icons_are_valid_pngs_of_the_right_size():
    assert _png_dims(STATIC / "icon-192.png") == (192, 192)
    assert _png_dims(STATIC / "icon-512.png") == (512, 512)


def test_static_route_serves_pwa_mime_types():
    # The GET handler's static mime map must cover the new asset types, else the
    # icons/manifest 404 even though the files exist on disk.
    src = (Path(tgd_common.__file__).resolve().parent / "TGDownloader_GUI.py").read_text(encoding="utf-8")
    for ext in (".png", ".svg", ".webmanifest"):
        assert f'"{ext}"' in src, f"static route missing MIME for {ext}"
