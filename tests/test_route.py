"""The POST /api/config/test-discord route (the console's Test button)."""

from __future__ import annotations

import discord
import discord.gateway as gw
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _client(registry):
    app = FastAPI()
    app.include_router(discord._build_router(registry))
    return TestClient(app)


def test_route_reports_success(monkeypatch, registry):
    async def fake_validate(token):
        return (True, {"username": "mybot", "id": "1"}, "")

    monkeypatch.setattr(gw, "validate_token", fake_validate)
    r = _client(registry).post("/api/config/test-discord", json={"bot_token": "abc"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": "", "identity": "mybot"}


def test_route_reports_failure(monkeypatch, registry):
    async def fake_validate(token):
        return (False, None, "invalid bot token")

    monkeypatch.setattr(gw, "validate_token", fake_validate)
    r = _client(registry).post("/api/config/test-discord", json={"bot_token": "bad"})
    assert r.json() == {"ok": False, "error": "invalid bot token", "identity": None}
