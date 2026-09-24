"""Tests for the parallel download workers: connect spacing and temp-file
handling in download_all_async."""

import TGDownloader as dl


# ── Connect gate ──────────────────────────────────────────────────────────────

def test_connect_gate_spaces_the_first_wave():
    now = [100.0]
    gate = dl._ConnectGate(4.0, clock=lambda: now[0])
    assert [gate.reserve() for _ in range(3)] == [0.0, 4.0, 8.0]


def test_connect_gate_wait_does_not_grow_with_position():
    # A worker that reaches its turn late must not sleep again for its index:
    # the old code slept index * 4s inside a worker slot, so track 50 of an
    # album idled for 200s after it finally got a worker.
    now = [0.0]
    gate = dl._ConnectGate(4.0, clock=lambda: now[0])
    for _ in range(50):
        gate.reserve()
    now[0] = 1000.0                 # long after the last reserved slot
    assert gate.reserve() == 0.0
    assert gate.reserve() == 4.0


def test_connect_gate_waits_for_a_slot_still_ahead():
    now = [0.0]
    gate = dl._ConnectGate(4.0, clock=lambda: now[0])
    gate.reserve()                  # slot at t=0
    now[0] = 1.5
    assert gate.reserve() == 2.5    # next slot is t=4
