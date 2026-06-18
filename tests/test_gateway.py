"""Gateway config seam + token validation (httpx mocked — no network)."""

from __future__ import annotations

import discord.gateway as gw


class _FakeResp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def _fake_client(resp):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **k):
            return resp

    return _Client


def test_configure_overrides_env(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    gw.configure("  tok  ", ["1", " 2 ", ""])
    assert gw._token() == "tok"
    assert gw._admin_ids() == {"1", "2"}
    gw.configure(None, None)  # blank token → env is the source again
    assert gw._token() is None


def test_admin_ids_env_fallback(monkeypatch):
    gw.configure(None, None)
    monkeypatch.setenv("DISCORD_ADMIN_IDS", "10, 20 ,30")
    assert gw._admin_ids() == {"10", "20", "30"}


async def test_validate_token_empty():
    ok, user, err = await gw.validate_token("")
    assert ok is False and user is None and "empty" in err


async def test_validate_token_success(monkeypatch):
    monkeypatch.setattr(gw.httpx, "AsyncClient", _fake_client(_FakeResp(200, {"username": "bot", "id": "1"})))
    ok, user, err = await gw.validate_token("tok")
    assert ok is True and user["username"] == "bot" and err == ""


async def test_validate_token_401(monkeypatch):
    monkeypatch.setattr(gw.httpx, "AsyncClient", _fake_client(_FakeResp(401)))
    ok, user, err = await gw.validate_token("bad")
    assert ok is False and user is None and "401" in err
