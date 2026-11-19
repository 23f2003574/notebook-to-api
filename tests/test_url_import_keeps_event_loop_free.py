"""A slow DNS lookup while validating an import URL must not freeze the dashboard."""
import asyncio
import socket
import time

import httpx
import pytest

import backend.routes.upload as upload_routes
from backend.dashboard import app


def test_event_loop_keeps_running_during_a_slow_dns_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr(upload_routes, "UPLOAD_DIR", str(tmp_path / "uploads"))
    real = socket.getaddrinfo

    def slow(host, *args, **kwargs):
        if host == "slow.example":
            time.sleep(1.0)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 80))]  # private: rejected
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", slow)

    async def scenario():
        ticks = 0
        stop = False

        async def ticker():
            nonlocal ticks
            while not stop:
                await asyncio.sleep(0.05)
                ticks += 1

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            task = asyncio.create_task(ticker())
            response = await client.post("/api/notebooks/import-url", json={"url": "http://slow.example/a.ipynb"})
            stop = True
            await task
        return response, ticks

    response, ticks = asyncio.run(scenario())
    assert response.status_code == 400 and "non-public" in response.text
    assert ticks >= 10, f"event loop was blocked (only {ticks} ticks during a 1s lookup)"
