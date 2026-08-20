"""Gateway behavior — config seam, token validation, thread continuity, and 🔬
research threads (httpx / REST mocked — no network)."""

from __future__ import annotations

import asyncio

import discord.gateway as gw
from discord.conversation import ConversationManager


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


# ── thread continuity + 🔬 research threads (full flow, REST + agent faked) ────


def _wire(monkeypatch):
    """Fresh module state + recording fakes for a full message/reaction flow.

    Returns ``(api_calls, sessions)`` — every REST call as ``(method, path,
    body)`` and every agent invocation as ``(prompt, session_id)``."""
    monkeypatch.setenv("DISCORD_BURST_DEBOUNCE_S", "0")
    monkeypatch.delenv("DISCORD_ADMIN_IDS", raising=False)
    monkeypatch.setattr(gw, "_conversations", ConversationManager())
    monkeypatch.setattr(gw, "_message_buffers", {})
    monkeypatch.setattr(gw, "_get_turn_log", lambda: None)

    api_calls: list = []
    sessions: list = []

    async def fake_api(method, path, body=None):
        api_calls.append((method, path, body))
        if path.endswith("/threads"):
            return {"id": "thread-1"}
        if method == "GET" and "/messages/" in path:
            return {"id": "m1", "content": "What is quantum tunneling?\nsecond line"}
        if method == "POST" and path.endswith("/messages"):
            return {"id": "reply-1"}
        return None

    async def fake_ask(content, session_id):
        sessions.append((content, session_id))
        return "the reply"

    monkeypatch.setattr(gw, "_api", fake_api)
    monkeypatch.setattr(gw, "_ask_agent", fake_ask)
    return api_calls, sessions


def _msg(channel_id, user_id, content, message_id="m1", mentions=()):
    return {
        "id": message_id,
        "channel_id": channel_id,
        "guild_id": "g1",
        "content": content,
        "author": {"id": user_id, "username": user_id},
        "mentions": [{"id": m} for m in mentions],
    }


def _reaction(emoji="🔬", user_id="alice", channel_id="chan-1", message_id="m1"):
    return {
        "user_id": user_id,
        "channel_id": channel_id,
        "message_id": message_id,
        "guild_id": "g1",
        "emoji": {"name": emoji},
        "member": {"user": {"id": user_id, "bot": False}},
    }


async def _post_and_flush(d, bot_id="bot"):
    """Run a MESSAGE_CREATE through the gate and wait for its burst to flush."""
    await gw._handle_message(d, bot_id)
    entry = gw._message_buffers.get(f"{d['channel_id']}:{d['author']['id']}")
    assert entry is not None, "message was dropped by the gate"
    await entry["timer"]
    await asyncio.sleep(0)  # let cancelled typing/slow-reaction tasks settle


async def test_thread_followup_continues_conversation(monkeypatch):
    api_calls, sessions = _wire(monkeypatch)

    # Turn 1: @-mention in the channel → reply → auto-thread + alias.
    await _post_and_flush(_msg("chan-1", "alice", "<@bot> research topic", mentions=("bot",)))
    assert [c for c in api_calls if c[1] == "/channels/chan-1/messages/reply-1/threads"]
    assert gw._conversations.has_thread("thread-1")

    # Turn 2: plain follow-up INSIDE the thread (channel_id == thread_id).
    await _post_and_flush(_msg("thread-1", "alice", "follow-up question", message_id="m2"))

    assert len(sessions) == 2  # not dropped
    assert sessions[0][1] == sessions[1][1]  # same LangGraph session key
    assert sessions[0][1].startswith("discord-channel-chan-1:")


async def test_teammate_joins_thread_conversation(monkeypatch):
    _, sessions = _wire(monkeypatch)
    await _post_and_flush(_msg("chan-1", "alice", "<@bot> research topic", mentions=("bot",)))

    # A different user posts in the thread — same conversation, no mention needed.
    await _post_and_flush(_msg("thread-1", "bob", "adding context", message_id="m3"))
    assert len(sessions) == 2
    assert sessions[0][1] == sessions[1][1]


async def test_plain_guild_message_still_gated(monkeypatch):
    _, sessions = _wire(monkeypatch)
    await gw._handle_message(_msg("chan-2", "alice", "no mention, no conversation"), "bot")
    assert gw._message_buffers == {} and sessions == []


async def test_research_reaction_opens_thread_and_replies(monkeypatch):
    api_calls, sessions = _wire(monkeypatch)
    await gw._handle_reaction(_reaction(), "bot")

    thread_calls = [c for c in api_calls if c[1] == "/channels/chan-1/messages/m1/threads"]
    assert thread_calls and thread_calls[0][2]["name"] == "🔬 What is quantum tunneling?"

    # Agent got the full message content, keyed to the new thread.
    assert len(sessions) == 1
    assert sessions[0][0] == "What is quantum tunneling?\nsecond line"
    assert sessions[0][1].startswith("discord-channel-thread-1:")

    # Reply posted inside the thread; the thread is a live conversation.
    posts = [c for c in api_calls if c[0] == "POST" and c[1] == "/channels/thread-1/messages"]
    assert posts and posts[0][2]["content"] == "the reply"
    assert gw._conversations.has_thread("thread-1")


async def test_research_thread_supports_followups(monkeypatch):
    _, sessions = _wire(monkeypatch)
    await gw._handle_reaction(_reaction(), "bot")

    # Plain follow-up in the research thread continues the same conversation.
    await _post_and_flush(_msg("thread-1", "bob", "dig into the second line", message_id="m4"))
    assert len(sessions) == 2
    assert sessions[0][1] == sessions[1][1]


async def test_reaction_ignores_other_emoji_and_self(monkeypatch):
    api_calls, sessions = _wire(monkeypatch)
    await gw._handle_reaction(_reaction(emoji="👍"), "bot")
    await gw._handle_reaction(_reaction(user_id="bot"), "bot")
    assert api_calls == [] and sessions == []
