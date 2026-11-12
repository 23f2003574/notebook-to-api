"""Callback destinations: unsafe targets are refused, and the connection goes to
the address that was validated (no DNS rebinding window)."""
import socket
import threading
import urllib.request

import pytest

from backend.generator.api_generator import generate_fastapi_code


@pytest.fixture
def ns():
    code = generate_fastapi_code([{"name": "train_model", "args": [], "return_type": "dict"}])
    namespace = {"__name__": "webhook_guard_ns"}
    exec(compile(code, "<generated>", "exec"), namespace)
    yield namespace
    urllib.request.install_opener(None)


def _resolves_to(monkeypatch, *addresses):
    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 0)) for a in addresses]
    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.1.2.3", "192.168.0.5", "172.16.0.1", "169.254.169.254", "168.63.129.16",
    "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fd00:ec2::254", "fe80::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1",
])
def test_prohibited_addresses_are_rejected(ns, monkeypatch, address):
    _resolves_to(monkeypatch, address)
    assert ns["_is_unsafe_webhook_host"]("https://hooks.example.test/x") is True


def test_public_https_callback_is_accepted(ns, monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946")
    assert ns["_is_unsafe_webhook_host"]("https://hooks.example.test/x") is False
    assert ns["_is_unsafe_webhook_host"]("https://hooks.example.test:8443/x") is False


def test_one_bad_address_among_public_ones_rejects_the_host(ns, monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34", "10.0.0.9")
    assert ns["_is_unsafe_webhook_host"]("https://hooks.example.test/x") is True


@pytest.mark.parametrize("url", [
    "https://user:pw@hooks.example.test/x", "https://user@hooks.example.test/x", "https:///nohost",
    "https://hooks.example.test:99999/x", "https://hooks.example.test:6379/x", "https://hooks.example.test:22/x",
    "not a url", "",
])
def test_malformed_credentialed_and_sensitive_port_urls_are_rejected(ns, monkeypatch, url):
    _resolves_to(monkeypatch, "93.184.216.34")
    assert ns["_is_unsafe_webhook_host"](url) is True


def test_unresolvable_host_fails_closed(ns, monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("nope")
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    assert ns["_is_unsafe_webhook_host"]("https://hooks.example.test/x") is True


def test_dns_rebinding_between_check_and_connect_is_refused(ns, monkeypatch):
    """First lookup (the screen) is public; the connection's own lookup is internal."""
    answers = iter([["93.184.216.34"], ["127.0.0.1"]])

    def fake(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 0)) for a in next(answers)]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert ns["_is_unsafe_webhook_host"]("http://rebind.example.test/x") is False
    connected = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: connected.append(addr))
    with pytest.raises(OSError, match="non-public"):
        ns["_webhook_connect"]("rebind.example.test", 80, 2)
    assert connected == []  # nothing was ever dialed


def test_connection_dials_exactly_the_validated_address(ns, monkeypatch):
    _resolves_to(monkeypatch, "93.184.216.34")
    dialed = []
    monkeypatch.setattr(socket.socket, "connect", lambda self, addr: dialed.append(addr))
    sock = ns["_webhook_connect"]("hooks.example.test", 443, 2)
    sock.close()
    assert dialed == [("93.184.216.34", 443)]


def test_webhook_delivery_refuses_internal_target_end_to_end(ns, monkeypatch):
    _resolves_to(monkeypatch, "169.254.169.254")
    result = ns["_deliver_task_webhook"]("http://metadata.example.test/latest", {"task_id": "t"})
    assert result["delivered"] is False and "non-public" in result["error"]
    assert "169.254" not in result["error"]


def test_non_webhook_urllib_requests_are_untouched(ns):
    handlers = {type(h).__name__ for h in urllib.request._opener.handlers}
    assert {"_WebhookHTTPHandler", "_WebhookHTTPSHandler"} <= handlers
    req = urllib.request.Request("http://example.test/")
    assert getattr(req, "_notebook_webhook", False) is False  # only marked requests use the guarded connection
