# SPDX-License-Identifier: MIT
"""Headless dev server: serves the GUI on the real port without opening a
browser window or taking the single-instance lock. Used by tooling/previews:
    python dev_server.py
"""
import os
import sys

os.environ["TGD_NO_BROWSER"] = "1"

# Optional port override (argv or env) so a second, test instance can run
# alongside the real app without fighting over port 7842.
if len(sys.argv) > 1:
    os.environ["TGD_HTTP_PORT"] = sys.argv[1]

import TGDownloader_GUI as gui  # noqa: E402

if __name__ == "__main__":
    srv = gui.Server(("127.0.0.1", gui.HTTP_PORT), gui.Handler)
    print(f"TGDownloader dev server on http://127.0.0.1:{gui.HTTP_PORT}/ (no browser launch)",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
