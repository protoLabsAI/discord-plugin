"""register() wires the surface, the Test route, and the token-gated tools."""

from __future__ import annotations

import discord


def test_register_wires_surface_and_route(registry, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    discord.register(registry)

    surf = next(s for s in registry.surfaces if s["name"] == "discord-gateway")
    assert callable(surf["start"]) and surf["stop"] and surf["reload"]

    # The Test-connection route mounts at the existing /api path (prefix="").
    assert "" in [p for p, _ in registry.routers]
    router = next(r for p, r in registry.routers if p == "")
    assert "/api/config/test-discord" in {route.path for route in router.routes}


def test_tools_off_without_token(registry, monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    discord.register(registry)
    assert registry.tools == []


def test_tools_on_with_token(registry, monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake-token")
    discord.register(registry)
    assert {t.name for t in registry.tools} >= {"discord_send", "discord_read", "discord_react"}
