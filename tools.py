"""Outbound Discord tools — the stateless half of the Discord surface (ADR 0015).

Talks to Discord's REST API **v10** directly via ``httpx`` (already a core dep) —
no ``discord.py``. Three tools: ``discord_send`` / ``discord_read`` /
``discord_react``. They're **off unless a bot token is configured**: when the
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


@tool
async def discord_send(channel_id: str, content: str) -> str:
    """Post a message to a Discord channel.

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


# ── registry ────────────────────────────────────────────────────────────────


def get_discord_tools() -> list:
    """The outbound Discord tools. ``get_all_tools`` includes these only when
    ``discord_configured()`` (a bot token is set)."""
    return [discord_send, discord_read, discord_react]
