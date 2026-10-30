import ipaddress
import socket
import pytest


def local_address(sock, address):
    if sock.family == getattr(socket, 'AF_UNIX', None):
        return True
    if not isinstance(address, tuple):
        return False
    try:
        return ipaddress.ip_address(address[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('FINANCIAL_SERVICE_NETWORK', '0')
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    def guarded(sock, address):
        if not local_address(sock, address):
            raise AssertionError('Unit tests must not access live data')
        return connect(sock, address)
    def guarded_ex(sock, address):
        if not local_address(sock, address):
            raise AssertionError('Unit tests must not access live data')
        return connect_ex(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', guarded)
    monkeypatch.setattr(socket.socket, 'connect_ex', guarded_ex)
