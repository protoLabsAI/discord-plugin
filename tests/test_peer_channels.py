"""Peer channels — cross-operator agent chat in a dedicated channel (#8).

Full flows with REST + agent faked: the peer gate (allowlisted bot, in a peer
channel, mentioning us, host fence present), the fenced + framed turn, the
bot↔bot turn budget and its ⏸ notice, the ``[no-reply]`` silence sentinel, 🛑
stop, the human re-arm, and the fail-closed refusal on a host without the
``tool_fence`` seam.
"""

from __future__ import annotations

import asyncio

import discord.gateway as gw
from discord.conversation import ConversationManager

CHAN = "peer-chan"
PEER = "peer-bot"  # the OTHER operator's bot user id
ME = "bot"


def _wire(monkeypatch, *, fence_supported=True, max_turns=2, tools=None, admins=("alice",)):
    monkeypatch.setenv("DISCORD_BURST_DEBOUNCE_S", "0")
    monkeypatch.setenv("DISCORD_PEER_COOLDOWN_S", "0")
    monkeypatch.delenv("DISCORD_ADMIN_IDS", raising=False)
    monkeypatch.setattr(gw, "_conversations", ConversationManager())
    monkeypatch.setattr(gw, "_message_buffers", {})
    monkeypatch.setattr(gw, "_get_turn_log", lambda: None)
    monkeypatch.setattr(gw, "_peer_stopped", set())
    monkeypatch.setattr(gw, "_peer_last_reply", {})
    monkeypatch.setattr(gw, "_invoke_supports_fence", fence_supported)
    gw.configure("tok", list(admins))
    cfg = {"peer_channel_ids": [CHAN], "peer_bot_ids": [PEER], "peer_max_turns": max_turns}
    if tools is not None:
        cfg["peer_tools"] = tools
    gw.configure_peers(cfg)

    api_calls: list = []
    asks: list = []
    replies = {"text": "the reply"}

    async def fake_api(method, path, body=None):
        api_calls.append((method, path, body))
        if path.endswith("/threads"):
            return {"id": "thread-1"}
        if method == "POST" and path.endswith("/messages"):
            return {"id": f"reply-{len(api_calls)}"}
        return None

    async def fake_ask(content, session_id, **kw):
        asks.append((content, session_id, kw.get("tool_fence")))
        return replies["text"]

    monkeypatch.setattr(gw, "_api", fake_api)
    monkeypatch.setattr(gw, "_ask_agent", fake_ask)
    return api_calls, asks, replies


def _msg(channel_id, user_id, content, *, message_id="m1", mentions=(), bot=False, webhook=False):
    d = {
        "id": message_id,
        "channel_id": channel_id,
        "guild_id": "g1",
        "content": content,
        "author": {"id": user_id, "username": user_id, "bot": bot},
        "mentions": [{"id": m} for m in mentions],
    }
    if webhook:
        d["webhook_id"] = "wh-1"
    return d


def _peer(content, *, message_id="p1", mention=True, channel_id=CHAN, webhook=False):
    return _msg(
        channel_id, PEER, content, message_id=message_id, mentions=(ME,) if mention else (), bot=True, webhook=webhook
    )


async def _flush(d):
    await gw._handle_message(d, ME)
    entry = gw._message_buffers.get(f"{d['channel_id']}:{d['author']['id']}")
    assert entry is not None, "message was dropped by the gate"
    await entry["timer"]
    await asyncio.sleep(0)


async def _dropped(d):
    await gw._handle_message(d, ME)
    return f"{d['channel_id']}:{d['author']['id']}" not in gw._message_buffers


def _posts(api_calls, channel_id=CHAN):
    return [c[2]["content"] for c in api_calls if c[0] == "POST" and c[1] == f"/channels/{channel_id}/messages"]


# ── config ───────────────────────────────────────────────────────────────────


def test_configure_peers_parses_and_defaults():
    gw.configure_peers({"peer_channel_ids": [" 1 ", ""], "peer_bot_ids": "2,3", "peer_max_turns": "4"})
    st = gw.peer_status()
    assert st["channel_ids"] == ["1"] and st["bot_ids"] == ["2", "3"] and st["max_turns"] == 4
    assert st["tools"] == gw._PEER_DEFAULT_TOOLS  # unset → the read-only default
    gw.configure_peers({"peer_channel_ids": ["1"], "peer_bot_ids": ["2"], "peer_tools": []})
    assert gw.peer_status()["tools"] == gw._FENCE_NOTHING  # empty = NO tools, not "no fence"
    gw.configure_peers({})
    assert gw.peer_status()["configured"] is False


def test_invoke_fence_detection():
    async def old(prompt, session_id): ...

    async def new(prompt, session_id, *, tool_fence=None): ...

    async def loose(*a, **k): ...

    assert gw._invoke_accepts_fence(old) is False
    assert gw._invoke_accepts_fence(new) is True
    assert gw._invoke_accepts_fence(loose) is True
    assert gw._invoke_accepts_fence(None) is False


# ── the peer gate ────────────────────────────────────────────────────────────


async def test_peer_mention_runs_a_fenced_framed_turn(monkeypatch):
    api_calls, asks, _ = _wire(monkeypatch)
    await _flush(_peer(f"<@{ME}> how does your API paginate?"))

    assert len(asks) == 1
    content, session_id, fence = asks[0]
    assert fence == gw._PEER_DEFAULT_TOOLS  # fenced to the read-only default
    assert session_id.startswith(f"discord-peer-{CHAN}:")
    assert "Peer agent message" in content and f"<@{PEER}>" in content
    assert "how does your API paginate?" in content
    assert "untrusted" in content and gw._NO_REPLY in content
    assert _posts(api_calls) == ["the reply"]
    # No auto-thread in a peer channel — the channel IS the exchange space.
    assert not [c for c in api_calls if c[1].endswith("/threads")]


async def test_peer_tools_config_is_the_fence(monkeypatch):
    _, asks, _ = _wire(monkeypatch, tools=["discord_read"])
    await _flush(_peer(f"<@{ME}> hi"))
    assert asks[0][2] == ["discord_read"]
    gw.configure_peers({"peer_channel_ids": [CHAN], "peer_bot_ids": [PEER], "peer_tools": []})
    await _flush(_peer(f"<@{ME}> hi again", message_id="p2"))
    assert asks[1][2] == gw._FENCE_NOTHING


async def test_peer_gate_negative_cases(monkeypatch):
    _, asks, _ = _wire(monkeypatch)
    # not mentioning us
    assert await _dropped(_peer("talking to someone else", mention=False))
    # right bot, wrong channel
    assert await _dropped(_peer(f"<@{ME}> hi", channel_id="other-chan"))
    # webhook, even with the peer's id
    assert await _dropped(_peer(f"<@{ME}> hi", webhook=True))
    # a bot that is NOT on the peer allowlist, in the peer channel, mentioning us
    assert await _dropped(_msg(CHAN, "stranger-bot", f"<@{ME}> hi", mentions=(ME,), bot=True))
    # ordinary (non-peer) channels still drop every bot
    assert await _dropped(_msg("chan-9", PEER, f"<@{ME}> hi", mentions=(ME,), bot=True))
    assert asks == []


async def test_no_fence_seam_means_peers_stay_deaf(monkeypatch):
    """Fail closed: a host whose invoke lacks tool_fence never runs a peer turn."""
    _, asks, _ = _wire(monkeypatch, fence_supported=False)
    assert gw.peer_status()["configured"] is True and gw.peer_status()["armed"] is False
    assert await _dropped(_peer(f"<@{ME}> hi"))
    assert asks == []


def test_start_in_background_reports_unsupported_host(monkeypatch):
    events: list = []
    gw.configure("tok", [])
    gw.configure_peers({"peer_channel_ids": [CHAN], "peer_bot_ids": [PEER]})
    monkeypatch.setattr(gw, "_run_gateway", lambda: asyncio.sleep(0))

    async def old_invoke(prompt, session_id):
        return ""

    async def run():
        task = gw.start_in_background(old_invoke, publish=lambda e, d: events.append((e, d)))
        if task is not None:
            await task

    asyncio.run(run())
    assert gw._invoke_supports_fence is False
    assert [e for e, _ in events] == ["discord.peer_unsupported"]


# ── loop bounds ──────────────────────────────────────────────────────────────


async def test_turn_budget_pauses_then_ignores_until_a_human_posts(monkeypatch):
    api_calls, asks, _ = _wire(monkeypatch, max_turns=2, admins=("alice",))
    await _flush(_peer(f"<@{ME}> q1", message_id="p1"))
    await _flush(_peer(f"<@{ME}> q2", message_id="p2"))
    posts = _posts(api_calls)
    assert posts[:2] == ["the reply", "the reply"]
    assert len(posts) == 3 and posts[2].startswith("⏸") and "<@alice>" in posts[2]

    # Budget spent → a third peer mention is ignored, and no second ⏸.
    assert await _dropped(_peer(f"<@{ME}> q3", message_id="p3"))
    assert len(asks) == 2 and len(_posts(api_calls)) == 3

    # An admin human speaking re-arms the exchange (same conversation continues).
    await _flush(_msg(CHAN, "alice", f"<@{ME}> carry on", message_id="h1", mentions=(ME,)))
    assert gw._conversations.peer_entry(CHAN).peer_turns == 0
    assert asks[2][2] is None  # the human's turn is NOT fenced
    assert "shared peer channel" in asks[2][0] and f"<@{PEER}>" in asks[2][0]  # told who the peers are
    assert asks[2][1] == asks[0][1]  # one channel-wide conversation
    await _flush(_peer(f"<@{ME}> q4", message_id="p4"))
    assert len(asks) == 4


async def test_non_admin_human_in_peer_channel_is_ignored(monkeypatch):
    _, asks, _ = _wire(monkeypatch, admins=("alice",))
    assert await _dropped(_msg(CHAN, "other-operator", f"<@{ME}> hi", mentions=(ME,)))
    assert asks == []


async def test_no_reply_sentinel_posts_nothing(monkeypatch):
    api_calls, asks, replies = _wire(monkeypatch)
    replies["text"] = " [No-Reply] "
    await _flush(_peer(f"<@{ME}> fyi only"))
    assert len(asks) == 1 and _posts(api_calls) == []
    # An EMPTY peer reply is silence too — never the human-facing fallback chatter.
    replies["text"] = ""
    await _flush(_peer(f"<@{ME}> again", message_id="p2"))
    assert _posts(api_calls) == []


async def test_stop_reaction_ends_the_exchange_until_a_human_posts(monkeypatch):
    api_calls, asks, _ = _wire(monkeypatch, admins=("alice",))
    await _flush(_peer(f"<@{ME}> q1"))
    assert gw._conversations.has_peer(CHAN)

    stop = {
        "user_id": "alice",
        "channel_id": CHAN,
        "message_id": "reply-1",
        "guild_id": "g1",
        "emoji": {"name": "🛑"},
        "member": {"user": {"id": "alice", "bot": False}},
    }
    await gw._handle_reaction(stop, ME)
    assert not gw._conversations.has_peer(CHAN)
    assert [c for c in api_calls if c[0] == "PUT" and "/reactions/" in c[1]]  # acknowledged with a reaction

    assert await _dropped(_peer(f"<@{ME}> q2", message_id="p2"))
    assert len(asks) == 1

    # Only an admin can stop; a non-admin's 🛑 is ignored.
    _wire(monkeypatch, admins=("alice",))
    await _flush(_peer(f"<@{ME}> q1"))
    await gw._handle_reaction({**stop, "user_id": "mallory"}, ME)
    assert gw._conversations.has_peer(CHAN)

    # A human post lifts the stop.
    await gw._handle_reaction(stop, ME)
    await _flush(_msg(CHAN, "alice", f"<@{ME}> resume", message_id="h2", mentions=(ME,)))
    await _flush(_peer(f"<@{ME}> q3", message_id="p3"))


async def test_cooldown_spaces_peer_replies(monkeypatch):
    api_calls, _, _ = _wire(monkeypatch, max_turns=5)
    monkeypatch.setenv("DISCORD_PEER_COOLDOWN_S", "0.05")
    sleeps: list = []
    real_sleep = asyncio.sleep

    async def rec_sleep(s):
        sleeps.append(s)
        await real_sleep(0)

    monkeypatch.setattr(gw.asyncio, "sleep", rec_sleep)
    await _flush(_peer(f"<@{ME}> q1", message_id="p1"))
    await _flush(_peer(f"<@{ME}> q2", message_id="p2"))
    assert any(0 < s <= 0.05 for s in sleeps)  # the second reply waited out the gap
    assert len(_posts(api_calls)) == 2
