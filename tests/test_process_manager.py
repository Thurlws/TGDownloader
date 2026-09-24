"""ProcessManager: a run started in reply to "done" must stay tracked."""

import sys
import threading
import time

import TGDownloader_GUI as gui


def test_start_from_done_is_not_orphaned(monkeypatch):
    # Fake backend: read the queue from stdin, then run for a moment.
    monkeypatch.setattr(gui, "_BACKEND_CMD", [
        sys.executable, "-c", "import sys, time; sys.stdin.read(); time.sleep(1.5)"])
    events = []
    second_started = threading.Event()
    all_done = threading.Event()

    class _PM(gui.ProcessManager):
        def broadcast(self, msg):
            events.append(msg)
            if msg.get("type") != "done":
                return
            if not second_started.is_set():
                second_started.set()
                self.start("second\n")     # the UI answering "done" with a new start
            else:
                all_done.set()

    pm = _PM()
    pm.start("first\n")
    assert second_started.wait(20)
    time.sleep(0.3)             # let the first run's stream thread finish
    # The old code cleared _proc after broadcasting "done", orphaning the
    # second backend: is_running() went False and Stop could not reach it.
    assert pm.is_running()
    assert all_done.wait(20)
    assert not pm.is_running()
    assert [e["code"] for e in events if e.get("type") == "done"] == [0, 0]
    assert not any("Server error" in e.get("text", "") for e in events)
