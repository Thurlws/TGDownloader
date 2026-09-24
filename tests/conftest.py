import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Make the repo root importable so tests can `import TGDownloader` etc.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def gui_post():
    """POST JSON to a real GUI server on an ephemeral port.

    Returns post(path, body) -> (status, parsed JSON). The Host header is the
    bare "127.0.0.1" the origin guard accepts, and proxies from the
    environment are bypassed so the request stays on loopback."""
    import TGDownloader_GUI as gui
    srv = gui.Server(("127.0.0.1", 0), gui.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(path: str, body: dict):
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "Host": "127.0.0.1"})
        try:
            with opener.open(req, timeout=30) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    yield post
    srv.shutdown()
    srv.server_close()
