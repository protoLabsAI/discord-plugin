"""Outbound tools — token gate, registry, and arg/token guards (no network)."""

from __future__ import annotations

import discord.tools as dt


def test_discord_configured_gates_on_env(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    assert dt.discord_configured() is False
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    assert dt.discord_configured() is True


def test_discord_configured_gates_on_config_token(monkeypatch):
    """The in-app token (Settings → Discord) opens the gate with no env var."""
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    assert dt.discord_configured() is False
    dt.configure("ui-set-token")
    assert dt.discord_configured() is True


def test_config_token_beats_env(monkeypatch):
    """In-app config wins over the env fallback — same precedence as the gateway."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-token")
    dt.configure("ui-set-token")
    assert dt._token() == "ui-set-token"


def test_blank_config_token_falls_back_to_env(monkeypatch):
    """A cleared/blank UI field must not shadow DISCORD_BOT_TOKEN (Docker back-compat)."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "env-token")
    dt.configure("   ")
    assert dt._token() == "env-token"


def test_get_discord_tools_returns_the_full_surface():
    assert {t.name for t in dt.get_discord_tools()} == {
        "discord_send",
        "discord_dm",
        "discord_read",
        "discord_react",
        "discord_whoami",
        "discord_list_guilds",
        "discord_list_channels",
    }


async def test_send_requires_token(monkeypatch):
    monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
    out = await dt.discord_send.ainvoke({"channel_id": "123", "content": "hi"})
    assert "no Discord bot token" in out


async def test_send_validates_args(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    assert "channel_id is required" in await dt.discord_send.ainvoke({"channel_id": "", "content": "hi"})
    assert "content is empty" in await dt.discord_send.ainvoke({"channel_id": "123", "content": "  "})


async def test_read_requires_channel(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "x")
    assert "channel_id is required" in await dt.discord_read.ainvoke({"channel_id": ""})


# ── DM + discovery (no network: `_request` is stubbed) ────────────────────────


def _stub_requests(monkeypatch, responses):
    """Replace `_request` with a canned `(method, path) -> (status, body)` map.
    Returns the call log so a test can assert the REST calls actually made."""
    calls: list[tuple[str, str, dict | None]] = []

    async def _fake(method, path, json_body=None):
        calls.append((method, path, json_body))
        return responses.get((method, path), (404, "not stubbed"))

    monkeypatch.setattr(dt, "_request", _fake)
    return calls


async def test_dm_opens_a_channel_then_posts(monkeypatch):
    """The whole point: a user ID is not a channel ID, so a DM must open the
    1:1 channel via POST /users/@me/channels before it can post."""
    calls = _stub_requests(
        monkeypatch,
        {
            ("POST", "/users/@me/channels"): (200, {"id": "dm-chan-1"}),
            ("POST", "/channels/dm-chan-1/messages"): (200, {"id": "msg-1"}),
        },
    )

    out = await dt.discord_dm.ainvoke({"user_id": "user-9", "content": "hello"})

    assert "OK: posted 1 message(s)" in out
    assert calls[0] == ("POST", "/users/@me/channels", {"recipient_id": "user-9"})
    assert calls[1] == ("POST", "/channels/dm-chan-1/messages", {"content": "hello"})


async def test_dm_surfaces_a_refused_recipient(monkeypatch):
    """Discord refuses DMs to users who share no server with the bot — that has
    to read as a recipient problem, not a generic failure."""
    _stub_requests(monkeypatch, {("POST", "/users/@me/channels"): (403, "Cannot send messages to this user")})

    out = await dt.discord_dm.ainvoke({"user_id": "user-9", "content": "hello"})

    assert "could not open a DM channel with user user-9" in out
    assert "403" in out


async def test_dm_validates_args():
    assert "user_id is required" in await dt.discord_dm.ainvoke({"user_id": " ", "content": "hi"})
    assert "content is empty" in await dt.discord_dm.ainvoke({"user_id": "u1", "content": "  "})


async def test_long_dm_is_chunked_like_a_send(monkeypatch):
    """DMs go through the same splitter as channel posts — the 2000-char limit
    applies to both, so the two paths must not diverge."""
    calls = _stub_requests(
        monkeypatch,
        {
            ("POST", "/users/@me/channels"): (200, {"id": "dm-chan-1"}),
            ("POST", "/channels/dm-chan-1/messages"): (200, {"id": "m"}),
        },
    )

    await dt.discord_dm.ainvoke({"user_id": "u1", "content": ("x" * 1500 + "\n") * 3})

    posts = [c for c in calls if c[1] == "/channels/dm-chan-1/messages"]
    assert len(posts) > 1
    assert all(len(c[2]["content"]) <= 2000 for c in posts)


async def test_list_guilds(monkeypatch):
    _stub_requests(monkeypatch, {("GET", "/users/@me/guilds"): (200, [{"id": "g1", "name": "Lab"}])})
    out = await dt.discord_list_guilds.ainvoke({})
    assert "g1" in out and "Lab" in out


async def test_list_guilds_empty_points_at_dm(monkeypatch):
    _stub_requests(monkeypatch, {("GET", "/users/@me/guilds"): (200, [])})
    assert "discord_dm" in await dt.discord_list_guilds.ainvoke({})


async def test_list_channels_defaults_to_the_only_guild(monkeypatch):
    """The common single-server case shouldn't force a guild_id the agent
    has no way to know."""
    _stub_requests(
        monkeypatch,
        {
            ("GET", "/users/@me/guilds"): (200, [{"id": "g1", "name": "Lab"}]),
            ("GET", "/guilds/g1/channels"): (200, [{"id": "c1", "name": "general", "type": 0, "position": 0}]),
        },
    )

    out = await dt.discord_list_channels.ainvoke({})

    assert "c1" in out and "#general" in out and "[text]" in out


async def test_list_channels_lists_choices_when_ambiguous(monkeypatch):
    _stub_requests(
        monkeypatch,
        {("GET", "/users/@me/guilds"): (200, [{"id": "g1", "name": "Lab"}, {"id": "g2", "name": "Ops"}])},
    )

    out = await dt.discord_list_channels.ainvoke({})

    assert "guild_id is required" in out and "Lab (g1)" in out and "Ops (g2)" in out


async def test_whoami_reports_identity_and_return_address(monkeypatch):
    _stub_requests(monkeypatch, {("GET", "/users/@me"): (200, {"id": "bot-1", "username": "protoBot"})})

    out = await dt.discord_whoami.ainvoke({})

    assert "@protoBot" in out and "bot-1" in out
    # No host / no captured address in the suite — must degrade, not raise.
    assert "Operator DM channel:" in out


async def test_whoami_reports_the_configured_operator_id(monkeypatch):
    """The operator's ID is in config as the gateway's inbound allowlist. If the
    agent can't read it, it asks the operator for an ID its own config holds —
    which is exactly what happened in the field.
    """
    monkeypatch.delenv("DISCORD_ADMIN_IDS", raising=False)
    dt.configure("tok", ["249386616806834177"])
    _stub_requests(monkeypatch, {("GET", "/users/@me"): (200, {"id": "bot-1", "username": "protoBot"})})

    out = await dt.discord_whoami.ainvoke({})

    assert "249386616806834177" in out
    assert "discord_dm" in out  # tells the agent what to do with it


async def test_whoami_says_so_when_no_operator_configured(monkeypatch):
    monkeypatch.delenv("DISCORD_ADMIN_IDS", raising=False)
    dt.configure("tok", [])
    _stub_requests(monkeypatch, {("GET", "/users/@me"): (200, {"id": "bot-1", "username": "protoBot"})})

    assert "none configured" in await dt.discord_whoami.ainvoke({})


def test_admin_ids_fall_back_to_env_csv(monkeypatch):
    """Same precedence as the gateway: config wins, env CSV is the fallback."""
    monkeypatch.setenv("DISCORD_ADMIN_IDS", "111, 222")
    dt.configure("tok", None)
    assert dt._admin_ids() == {"111", "222"}

    dt.configure("tok", ["333"])
    assert dt._admin_ids() == {"333"}
