"""The single-instance lock: a second launch must see the port as taken."""

import socket

import pytest

import TGDownloader_GUI as gui


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_second_instance_cannot_take_the_lock():
    port = _free_port()
    first = gui._acquire_instance_lock(port)
    assert first is not None
    try:
        assert gui._acquire_instance_lock(port) is None
    finally:
        first.close()


def test_lock_rejects_a_reuseaddr_socket():
    # Older builds set SO_REUSEADDR on their lock socket, which let two of
    # them bind the same port. The lock must keep such a socket out too.
    port = _free_port()
    first = gui._acquire_instance_lock(port)
    assert first is not None
    other = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        with pytest.raises(OSError):
            other.bind(("127.0.0.1", port))
    finally:
        other.close()
        first.close()


def test_lock_is_free_again_after_release():
    port = _free_port()
    gui._acquire_instance_lock(port).close()
    again = gui._acquire_instance_lock(port)
    assert again is not None
    again.close()
