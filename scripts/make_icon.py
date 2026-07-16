#!/usr/bin/env python3
"""Regenerate the Windows app icon (icon.ico) from static/icon-512.png.

TGDownloader ships without Pillow (it's excluded from the PyInstaller bundle to
keep it slim), so this uses ffmpeg (already a hard dependency for audio work)
to produce cleanly Lanczos-downscaled PNGs at each standard icon size, then packs
them into one PNG-compressed .ico with a pure-Python struct writer.

Run this whenever the brand PNG (static/icon-512.png) changes:

    python scripts/make_icon.py

The resulting icon.ico is referenced by TGDownloader.spec (the exe icon).
"""
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT  = Path(__file__).resolve().parent.parent
SRC   = ROOT / "static" / "icon-512.png"
OUT   = ROOT / "icon.ico"
SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"Source icon not found: {SRC}")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pngs = []
        for s in SIZES:
            dst = tmp / f"{s}.png"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(SRC),
                 "-vf", f"scale={s}:{s}:flags=lanczos", "-pix_fmt", "rgba", str(dst)],
                check=True,
            )
            pngs.append((s, dst.read_bytes()))

        header = struct.pack("<HHH", 0, 1, len(pngs))   # reserved, type=1 (icon), count
        entries, blobs = b"", b""
        offset = 6 + 16 * len(pngs)                     # dir header + every entry
        for s, data in pngs:
            side = 0 if s >= 256 else s                 # 0 means 256 in the ICO spec
            entries += struct.pack("<BBBBHHII",
                                   side, side, 0, 0,    # w, h, colours, reserved
                                   1, 32,               # colour planes, bpp
                                   len(data), offset)   # byte size, offset
            blobs += data
            offset += len(data)

        OUT.write_bytes(header + entries + blobs)

    print(f"Wrote {OUT.name} ({OUT.stat().st_size:,} bytes), sizes {SIZES}")


if __name__ == "__main__":
    main()
