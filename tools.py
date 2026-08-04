"""Outbound Discord tools — the stateless half of the Discord surface (ADR 0015).

Talks to Discord's REST API **v10** directly via ``httpx`` (already a core dep) —
no ``discord.py``.

- **Act:** ``discord_send`` (channel) · ``discord_dm`` (user) · ``discord_react``
- **Look:** ``discord_read`` (channel history)
- **Find:** ``discord_whoami`` · ``discord_list_guilds`` · ``discord_list_channels``

The *find* tools exist because everything else is keyed by a numeric channel ID
that nothing in the agent's context supplies — without them the toolset is only
usable when an operator hand-feeds an ID into the persona. ``discord_dm`` is the
only way to *start* a DM: a user ID is not a channel ID, so reaching a person
means opening their DM channel first (``POST /users/@me/channels``).

They're **off unless a bot token is configured**: when the
token is absent the tools are not registered (``register()`` gates on
``discord_configured()``), and any direct call degrades to a readable error.

The token comes from the in-app config (Settings → Discord, an ADR 0019
manifest secret) via :func:`configure`, with ``DISCORD_BOT_TOKEN`` as the env
fallback — the same two-source precedence ``gateway.configure`` uses. This
module deliberately keeps its own copy rather than importing the gateway: it's
the stateless half and must stay usable with no gateway connection. Keeping the
sources in sync is ``register()``'s job (it seeds both).

This is the request/response half. The persistent inbound **gateway** listener
(DMs + @-mentions, burst debounce, reactions, threads, return-address capture)
is a separate native surface — it can't live here or in an MCP server because it
owns a stateful connection. See ADR 0015.

Channel IDs are required per call — there is no default-channel env var; the
persona / operator names the channel to use.
"""

from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import tool

_DISCORD_API = "https://discord.com/api/v10"
_MAX_MESSAGE_LEN = 2000  # Discord hard limit
_USER_AGENT = "protoAgent (https://github.com/protoLabsAI/protoAgent, 0.1)"


# In-app config token (Settings → Discord). None = fall back to the env var.
_cfg_token: str | None = None


def configure(token: str | None) -> None:
    """Set the in-app bot token — call before gating on :func:`discord_configured`.

    Mirrors ``gateway.configure``'s contract: a blank/None token leaves
    ``DISCORD_BOT_TOKEN`` as the source (Docker back-compat). ``register()``
    seeds this from the resolved plugin config on load *and* on config reload,
    so a UI-set token surfaces the tools without needing an env var.
    """
    global _cfg_token
    _cfg_token = (token or "").strip() or None


def _token() -> str | None:
    return _cfg_token or os.environ.get("DISCORD_BOT_TOKEN")


def discord_configured() -> bool:
    """True when a bot token is present — the gate ``register()`` uses to decide
    whether to register these tools at all (ADR 0015: off by default)."""
    return bool((_token() or "").strip())


async def _request(method: str, path: str, json_body: dict[str, Any] | None = None) -> tuple[int, Any]:
    token = _token()
    if not token:
        return 0, "Error: no Discord bot token — set one in Settings → Discord (or DISCORD_BOT_TOKEN)."
    try:
        import httpx
    except ImportError:
        return 0, "Error: httpx not installed."

    url = f"{_DISCORD_API}{path}"
    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(method, url, headers=headers, json=json_body)
    except httpx.HTTPError as e:
        return 0, f"Error: Discord request failed: {e}"

    if resp.status_code in (200, 201, 204):
        try:
            return resp.status_code, resp.json() if resp.content else None
        except ValueError:
            return resp.status_code, resp.text
    # 429 carries a retry-after; surface it rather than silently failing.
    if resp.status_code == 429:
        retry = ""
        try:
            retry = f" (retry_after={resp.json().get('retry_after')}s)"
        except ValueError:
            pass
        return 429, f"rate limited{retry}: {resp.text[:300]}"
    return resp.status_code, resp.text[:500]


# ── send ──────────────────────────────────────────────────────────────────────


async def _post_message(channel_id: str, content: str) -> str:
    """Post ``content`` to a channel, splitting at line boundaries to stay under
    Discord's 2000-char limit. Shared by ``discord_send`` and ``discord_dm`` — a
    DM is just a message to the DM channel, so the chunking must not diverge."""
    chunks: list[str] = []
    remaining = content
    while remaining:
        if len(remaining) <= _MAX_MESSAGE_LEN:
            chunks.append(remaining)
            break
        split_at = remaining[:_MAX_MESSAGE_LEN].rfind("\n")
        if split_at < 100:
            split_at = _MAX_MESSAGE_LEN
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()

    posted: list[str] = []
    for chunk in chunks:
        status, body = await _request("POST", f"/channels/{channel_id}/messages", json_body={"content": chunk})
        if status not in (200, 201):
            return f"Error: HTTP {status}: {body}"
        if isinstance(body, dict) and body.get("id"):
            posted.append(body["id"])

    return f"OK: posted {len(posted)} message(s) ({', '.join(posted)})"


@tool
async def discord_send(channel_id: str, content: str) -> str:
    """Post a message to a Discord channel.

    Use ``discord_list_channels`` to find a channel ID, or ``discord_dm`` to
    message a person directly (a user ID is NOT a channel ID).

    Args:
        channel_id: Numeric Discord channel ID (e.g. ``1469195643590541353``).
        content: Message body. Markdown supported. Long messages are split into
            multiple posts at line boundaries (Discord's 2000-char limit).

    Returns the posted message ID(s), or a readable error.
    """
    if not channel_id.strip():
        return "Error: channel_id is required."
    if not content.strip():
        return "Error: content is empty."

    return await _post_message(channel_id.strip(), content)


@tool
async def discord_dm(user_id: str, content: str) -> str:
    """Send a direct message to a Discord user.

    Opens (or reuses — Discord makes this idempotent) the 1:1 DM channel with
    ``user_id``, then posts there. This is the ONLY way to start a DM:
    ``discord_send`` takes a *channel* ID, and a user ID is not one.

    The bot can only DM users who share a server with it, or who have DMed it
    before; Discord rejects the rest.

    Args:
        user_id: Numeric Discord user ID (e.g. ``1234567890123456789``). Right-click
            a user with Developer Mode on → Copy User ID.
        content: Message body. Markdown supported; long messages are split.
    """
    if not user_id.strip():
        return "Error: user_id is required."
    if not content.strip():
        return "Error: content is empty."

    status, body = await _request("POST", "/users/@me/channels", json_body={"recipient_id": user_id.strip()})
    if status not in (200, 201):
        return f"Error: could not open a DM channel with user {user_id}: HTTP {status}: {body}"
    channel_id = body.get("id") if isinstance(body, dict) else None
    if not channel_id:
        return f"Error: Discord returned no DM channel id: {body}"

    return await _post_message(str(channel_id), content)


@tool
async def discord_read(channel_id: str, limit: int = 20) -> str:
    """Read recent messages from a Discord channel.

    Args:
        channel_id: Numeric Discord channel ID.
        limit: Max messages to return (1–100, default 20). Newest first.
    """
    if not channel_id.strip():
        return "Error: channel_id is required."
    limit = max(1, min(limit, 100))
    status, body = await _request("GET", f"/channels/{channel_id}/messages?limit={limit}")
    if status != 200:
        return f"Error: HTTP {status}: {body}"
    if not isinstance(body, list):
        return f"Error: unexpected response: {body}"

    lines = [f"{len(body)} message(s) in channel {channel_id}:"]
    for msg in body:
        author = msg.get("author", {}).get("username", "?")
        is_bot = " [bot]" if msg.get("author", {}).get("bot") else ""
        ts = msg.get("timestamp", "")[:19]
        text = (msg.get("content") or "").replace("\n", " ")[:300]
        lines.append(f"  {ts} @{author}{is_bot}: {text}")
    return "\n".join(lines)


@tool
async def discord_react(channel_id: str, message_id: str, emoji: str) -> str:
    """Add a reaction to a Discord message.

    Args:
        channel_id: Numeric channel ID.
        message_id: Numeric message ID.
        emoji: Unicode emoji (e.g. ``"✅"``) or a custom ``name:id``.
    """
    if not channel_id.strip() or not message_id.strip():
        return "Error: channel_id and message_id are required."
    from urllib.parse import quote

    encoded = quote(emoji)
    status, body = await _request("PUT", f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me")
    if status not in (200, 201, 204):
        return f"Error: HTTP {status}: {body}"
    return f"OK: reacted with {emoji}."


# ── discovery ─────────────────────────────────────────────────────────────────
#
# Without these the send/read tools are unusable on their own: every one of them
# needs a numeric channel ID, and nothing in the agent's context supplies one.

# Discord channel `type` ints worth naming; anything else prints as `type=N`.
_CHANNEL_TYPES = {0: "text", 2: "voice", 4: "category", 5: "announcement", 13: "stage", 15: "forum"}


@tool
async def discord_whoami() -> str:
    """Identify the Discord bot account this agent posts as.

    Returns the bot's username and user ID, plus the operator's captured DM
    channel (recorded when they last DMed the bot) if there is one — that
    channel is where proactive/scheduled output is delivered.
    """
    status, body = await _request("GET", "/users/@me")
    if status != 200 or not isinstance(body, dict):
        return f"Error: HTTP {status}: {body}"

    name = body.get("username", "?")
    lines = [f"Bot: @{name} (user ID {body.get('id')})"]

    # Lazy + best-effort: return_address reaches for the host's infra.paths, and
    # a missing/corrupt store just means "nothing captured yet" (it never raises).
    try:
        from .return_address import get as _return_address

        captured = _return_address()
    except Exception:  # noqa: BLE001 — discovery must not fail on an absent store
        captured = None
    lines.append(
        f"Operator DM channel: {captured} (proactive output lands here)"
        if captured
        else "Operator DM channel: none captured yet — it's recorded the first time they DM the bot."
    )
    return "\n".join(lines)


@tool
async def discord_list_guilds() -> str:
    """List the Discord servers (guilds) this bot has been added to.

    Start here when you don't know where to post — then ``discord_list_channels``
    for that server's channel IDs.
    """
    status, body = await _request("GET", "/users/@me/guilds")
    if status != 200:
        return f"Error: HTTP {status}: {body}"
    if not isinstance(body, list):
        return f"Error: unexpected response: {body}"
    if not body:
        return "No servers — the bot hasn't been invited to any. Use discord_dm to message a user directly."

    lines = [f"{len(body)} server(s):"]
    for guild in body:
        lines.append(f"  {guild.get('id')}  {guild.get('name', '?')}")
    return "\n".join(lines)


@tool
async def discord_list_channels(guild_id: str = "") -> str:
    """List channels in a Discord server, with the IDs ``discord_send`` needs.

    Args:
        guild_id: Numeric server ID. Optional — if the bot is in exactly one
            server, that one is used; otherwise the choices are listed.
    """
    guild_id = guild_id.strip()
    if not guild_id:
        status, body = await _request("GET", "/users/@me/guilds")
        if status != 200 or not isinstance(body, list):
            return f"Error: could not resolve a default server: HTTP {status}: {body}"
        if len(body) != 1:
            listing = ", ".join(f"{g.get('name')} ({g.get('id')})" for g in body) or "none"
            return f"Error: guild_id is required — the bot is in {len(body)} server(s): {listing}"
        guild_id = str(body[0].get("id"))

    status, body = await _request("GET", f"/guilds/{guild_id}/channels")
    if status != 200:
        return f"Error: HTTP {status}: {body}"
    if not isinstance(body, list):
        return f"Error: unexpected response: {body}"

    lines = [f"{len(body)} channel(s) in server {guild_id}:"]
    for ch in sorted(body, key=lambda c: (c.get("position") or 0)):
        kind = _CHANNEL_TYPES.get(ch.get("type"), f"type={ch.get('type')}")
        lines.append(f"  {ch.get('id')}  #{ch.get('name', '?')} [{kind}]")
    return "\n".join(lines)


# ── registry ────────────────────────────────────────────────────────────────


def get_discord_tools() -> list:
    """The outbound Discord tools. ``register()`` includes these only when
    ``discord_configured()`` (a bot token is set)."""
    return [
        discord_send,
        discord_dm,
        discord_read,
        discord_react,
        discord_whoami,
        discord_list_guilds,
        discord_list_channels,
    ]
