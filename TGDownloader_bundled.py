# SPDX-License-Identifier: MIT
# Copyright (c) 2025 TGDownloader contributors
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
TGDownloader — Bundled Entry Point  (Option 3 + 4)
====================================================
This is the PyInstaller entry point AND the development entry point.

Two runtime modes
-----------------
Normal  :  python TGDownloader_bundled.py   → starts the GUI web-server
           TGDownloader.exe                  → same (frozen)
Backend :  launched internally by ProcessManager with --backend flag;
           receives the formatted URL list on stdin, streams results back
           on stdout exactly as the old subprocess model did.

Directory layout
----------------
In development:   everything lives beside this file.
In frozen bundle: read-only resources (gui.html) live in sys._MEIPASS;
                  writable data (config, sessions, logs) live beside the .exe.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


# ── 1. Resolve directories ────────────────────────────────────────────────────

if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(sys._MEIPASS)           # read-only: bundled resources
    DATA_DIR   = Path(sys.executable).parent  # writable:  next to .exe
else:
    BUNDLE_DIR = Path(__file__).parent
    DATA_DIR   = Path(__file__).parent

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Broadcast to every module and child process via environment variables
os.environ["TGD_DATA_DIR"]   = str(DATA_DIR)
os.environ["TGD_BUNDLE_DIR"] = str(BUNDLE_DIR)

LOG_FILE = DATA_DIR / "tgdownloader_debug.log"
os.environ["TGD_LOG_FILE"]   = str(LOG_FILE)


# ── 2. Logging helpers ────────────────────────────────────────────────────────

def _setup_logging(to_console: bool = True) -> None:
    handlers: list[logging.Handler] = [
        logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a"),
    ]
    if to_console:
        handlers.append(logging.StreamHandler(sys.__stdout__ or sys.stdout))

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
        handlers=handlers,
        force=True,
    )


class _LogWriter:
    """Redirect sys.stdout / sys.stderr into the log file."""

    def __init__(self, logger: logging.Logger, level: int) -> None:
        self._logger = logger
        self._level  = level

    def write(self, msg: str) -> None:
        msg = msg.rstrip("\n\r")
        if msg:
            self._logger.log(self._level, msg)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


# ── 3. Backend subprocess mode ────────────────────────────────────────────────

if "--backend" in sys.argv:
    # Spawned by ProcessManager.start().
    # stdout IS the pipe back to the parent — must not be redirected because
    # it carries the ##RESULT## / ##PROG## structured lines the GUI reads.
    _setup_logging(to_console=False)
    _blog = logging.getLogger("backend")

    if getattr(sys, "frozen", False):
        # Only redirect stderr; stdout stays as the pipe.
        sys.stderr = _LogWriter(_blog, logging.ERROR)  # type: ignore[assignment]

    _blog.info("Backend subprocess started (pid=%s)", os.getpid())

    try:
        import asyncio
        from TGDownloader import main as _backend_main  # type: ignore
        asyncio.run(_backend_main())
    except KeyboardInterrupt:
        _blog.info("Backend interrupted by user")
    except SystemExit as _exc:
        _blog.info("Backend sys.exit(%s)", _exc.code)
    except Exception:
        _blog.exception("Backend crashed")

    sys.exit(0)


# ── 4. Normal GUI-server startup ──────────────────────────────────────────────

_setup_logging(to_console=not getattr(sys, "frozen", False))

if getattr(sys, "frozen", False):
    # No console window — redirect stray prints so nothing is silently lost.
    _alog = logging.getLogger("app")
    sys.stdout = _LogWriter(_alog, logging.INFO)   # type: ignore[assignment]
    sys.stderr = _LogWriter(_alog, logging.ERROR)  # type: ignore[assignment]

logging.getLogger("app").info(
    "TGDownloader GUI starting (frozen=%s, pid=%s)",
    getattr(sys, "frozen", False),
    os.getpid(),
)

try:
    from TGDownloader_GUI import main as _gui_main  # type: ignore
except ImportError as _exc:
    logging.critical("Cannot import TGDownloader_GUI: %s", _exc)
    sys.exit(1)

if __name__ == "__main__":
    _gui_main()