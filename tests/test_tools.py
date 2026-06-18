"""Outbound tools — env gate, registry, and arg/token guards (no network)."""

from __future__ import annotations

import discord.tools as dt


def test_discord_configured_gates_on_env(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    assert dt.discord_configured() is False
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    assert dt.discord_configured() is True


def test_get_discord_tools_returns_three():
    assert {t.name for t in dt.get_discord_tools()} == {"discord_send", "discord_read", "discord_react"}


async def test_send_requires_token(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    out = await dt.discord_send.ainvoke({"channel_id": "123", "content": "hi"})
    assert "DISCORD_BOT_TOKEN" in out


async def test_send_validates_args(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    assert "channel_id is required" in await dt.discord_send.ainvoke({"channel_id": "", "content": "hi"})
    assert "content is empty" in await dt.discord_send.ainvoke({"channel_id": "123", "content": "  "})


async def test_read_requires_channel(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    assert "channel_id is required" in await dt.discord_read.ainvoke({"channel_id": ""})
