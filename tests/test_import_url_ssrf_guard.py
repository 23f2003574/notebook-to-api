"""Dashboard notebook URL import refuses internal destinations at connect time."""
import asyncio
import ipaddress
import socket

import httpx
import pytest
from fastapi import HTTPException

from backend.routes import upload as upload_module
from backend.runtime.destination_guard import ip_is_unsafe

PUBLIC = "93.184.216.34"


def _resolver(*answers):
    """socket.getaddrinfo stand-in returning one address list per call (last repeats)."""
    calls = []

    def fake(host, port, *args, **kwargs):
        calls.append(host)
        addresses = answers[min(len(calls), len(answers)) - 1]
        return [(socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port or 0))
                for a in addresses]

    fake.calls = calls
    return fake


@pytest.fixture
def mock_http(monkeypatch):
    """Route the import's httpx client to a handler; records the requests it receives."""
    seen = []
    state = {"handler": lambda request: httpx.Response(200, content=b"{}")}
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        def handler(request):
            seen.append(request)
            return state["handler"](request)
        return real(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(upload_module.httpx, "AsyncClient", factory)
    return seen, state


def _fetch(url):
    return asyncio.run(upload_module._download_url_for_import(url))


@pytest.mark.parametrize("address", [
    "100.100.100.200", "168.63.129.16", "100.64.0.1",          # audit-identified
    "10.0.0.5", "172.16.0.1", "192.168.1.1", "127.0.0.1", "169.254.169.254",
    "0.0.0.0", "224.0.0.1", "240.0.0.1", "192.0.0.8", "198.18.0.1",
    "::1", "fe80::1", "fc00::1", "fd00:ec2::254", "::",
    "::ffff:127.0.0.1", "::ffff:169.254.169.254", "::ffff:100.100.100.200", "::ffff:10.0.0.1",
])
def test_prohibited_destinations_are_rejected(address, monkeypatch):
    assert ip_is_unsafe(address)
    monkeypatch.setattr(upload_module.socket, "getaddrinfo", _resolver([address]))
    with pytest.raises(HTTPException) as exc:
        upload_module._reject_unsafe_import_url_host("http://notebooks.example.com/a.ipynb")
    assert exc.value.status_code == 400 and "non-public" in exc.value.detail
    assert address not in exc.value.detail  # no internal network details leak back


@pytest.mark.parametrize("address", [PUBLIC, "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(address):
    assert not ip_is_unsafe(address)


def test_hostname_that_rebinds_after_the_precheck_is_blocked_before_connecting(monkeypatch, mock_http):
    seen, _ = mock_http
    resolver = _resolver([PUBLIC], ["127.0.0.1"])  # public for the pre-check, internal afterwards
    monkeypatch.setattr(upload_module.socket, "getaddrinfo", resolver)
    with pytest.raises(HTTPException) as exc:
        _fetch("http://rebind.example.com/a.ipynb")
    assert exc.value.status_code == 400 and "non-public" in exc.value.detail
    assert resolver.calls == ["rebind.example.com"] * 2
    assert seen == []  # nothing was ever sent


def test_connection_goes_to_the_validated_address_not_the_hostname(monkeypatch, mock_http):
    seen, _ = mock_http
    monkeypatch.setattr(upload_module.socket, "getaddrinfo", _resolver([PUBLIC]))
    final_url, body = _fetch("https://notebooks.example.com/a.ipynb")
    assert (final_url, body) == ("https://notebooks.example.com/a.ipynb", b"{}")
    (request,) = seen
    assert request.url.host == PUBLIC  # httpx never resolves the name itself
    assert request.headers["host"] == "notebooks.example.com"
    assert request.extensions["sni_hostname"] == "notebooks.example.com"


def test_redirect_to_a_prohibited_destination_is_blocked(monkeypatch, mock_http):
    seen, state = mock_http
    monkeypatch.setattr(
        upload_module.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (
            "100.100.100.200" if host == "metadata.example.com" else PUBLIC, port or 0))],
    )
    state["handler"] = lambda request: httpx.Response(302, headers={"location": "http://metadata.example.com/x"})
    with pytest.raises(HTTPException) as exc:
        _fetch("http://notebooks.example.com/a.ipynb")
    assert exc.value.status_code == 400
    assert [r.headers["host"] for r in seen] == ["notebooks.example.com"]  # hop 2 never connected


def test_redirect_between_public_hosts_is_followed(monkeypatch, mock_http):
    seen, state = mock_http
    monkeypatch.setattr(upload_module.socket, "getaddrinfo", _resolver([PUBLIC]))
    state["handler"] = lambda request: (
        httpx.Response(302, headers={"location": "https://cdn.example.com/b.ipynb"})
        if request.headers["host"] == "notebooks.example.com" else httpx.Response(200, content=b"{}")
    )
    final_url, _ = _fetch("http://notebooks.example.com/a.ipynb")
    assert final_url == "https://cdn.example.com/b.ipynb"


def test_unreachable_first_address_falls_back_to_the_next_validated_one(monkeypatch, mock_http):
    seen, state = mock_http
    monkeypatch.setattr(upload_module.socket, "getaddrinfo", _resolver([PUBLIC, "8.8.8.8"]))

    def handler(request):
        if request.url.host == PUBLIC:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, content=b"{}")

    state["handler"] = handler
    assert _fetch("http://notebooks.example.com/a.ipynb")[1] == b"{}"
    assert [r.url.host for r in seen] == [PUBLIC, "8.8.8.8"]


def test_ip_literal_url_for_a_metadata_address_is_rejected(mock_http):
    seen, _ = mock_http
    with pytest.raises(HTTPException) as exc:
        _fetch("http://100.100.100.200/latest/meta-data")
    assert exc.value.status_code == 400 and seen == []


def test_webhook_guard_and_import_guard_share_one_policy():
    """The generated app embeds its own copy of the policy; both must agree."""
    from backend.generator.api_generator import _WEBHOOK_GUARD_SOURCE
    import urllib.request
    from urllib.parse import urlparse
    namespace = {"ipaddress": ipaddress, "socket": socket, "urllib": urllib, "urlparse": urlparse}
    exec(_WEBHOOK_GUARD_SOURCE, namespace)  # noqa: S102 - our own generator template
    for address in ["100.100.100.200", "168.63.129.16", "100.64.0.1", "10.1.1.1", "127.0.0.1", "::1",
                    "169.254.169.254", "::ffff:127.0.0.1", "fd00:ec2::254", "198.18.0.1", "0.0.0.0",
                    "224.0.0.1", PUBLIC, "8.8.8.8", "2606:4700:4700::1111"]:
        assert namespace["_webhook_ip_unsafe"](address) == ip_is_unsafe(address), address
    assert namespace["_WEBHOOK_BLOCKED_IPS"] == __import__(
        "backend.runtime.destination_guard", fromlist=["BLOCKED_IPS"]).BLOCKED_IPS
