import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail loudly if any test tries to open a real network connection."""
    def guard(*a, **k):
        raise RuntimeError("network access attempted in test")
    monkeypatch.setattr(socket.socket, "connect", guard)
