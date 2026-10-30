import socket
import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("FINANCIAL_SERVICE_NETWORK", "0")
    def denied(*args, **kwargs):
        raise AssertionError("Unit tests must not access live data")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
