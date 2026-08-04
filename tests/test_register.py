"""register() wires the surface, the Test route, and the token-gated tools."""

from __future__ import annotations

import discord
from conftest import FakeRegistry


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


def test_tools_on_with_config_token(monkeypatch):
    """The DOCUMENTED path: the operator pastes the token into Settings → Discord,
    which lands in `registry.config` (manifest secret, ADR 0019) — no env var.

    Regression: `register()` seeded only the gateway's token, while the tool gate
    read the env var, so this path registered ZERO tools — the gateway answered
    DMs but the agent had nothing to reply with.
    """
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    registry = FakeRegistry(config={"enabled": True, "bot_token": "ui-set-token", "admin_ids": []})

    discord.register(registry)

    assert {t.name for t in registry.tools} >= {"discord_send", "discord_read", "discord_react"}


def test_config_token_reaches_the_tool_body(monkeypatch):
    """Registration isn't enough — the request helper must find the same token,
    or every call fails with "no bot token" despite the tools being present."""
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    registry = FakeRegistry(config={"enabled": True, "bot_token": "ui-set-token"})

    discord.register(registry)

    from discord import tools as dt

    assert dt._token() == "ui-set-token"


def test_config_admin_ids_reach_the_tools(monkeypatch):
    """`admin_ids` went only to the gateway (inbound allowlist), so the agent
    couldn't learn its own operator's user ID from its own config."""
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_ADMIN_IDS", raising=False)
    registry = FakeRegistry(config={"enabled": True, "bot_token": "t", "admin_ids": ["249386616806834177"]})

    discord.register(registry)

    from discord import tools as dt

    assert dt._admin_ids() == {"249386616806834177"}


async def test_reload_reseeds_the_tool_token(registry, monkeypatch):
    """A Settings save swaps the token; already-bound tool objects must follow it,
    not keep posting with the revoked one.

    `enabled` stays False on purpose: the seed happens before `_launch`'s start
    check, so this exercises the reseed without opening a real gateway websocket.
    """
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    registry.config = {"enabled": False, "bot_token": "old-token"}
    discord.register(registry)

    from discord import tools as dt

    assert dt._token() == "old-token"

    reload = next(s for s in registry.surfaces if s["name"] == "discord-gateway")["reload"]

    class _NewConfig:
        plugin_config = {"discord": {"enabled": False, "bot_token": "new-token"}}

    await reload(_NewConfig())

    assert dt._token() == "new-token"
