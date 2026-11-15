"""Conversation continuity for the Discord gateway (ADR 0015).

Per-``(channel, user)`` conversation state: when a user @-mentions the agent in
a channel, follow-up messages within the timeout window continue the same
conversation without another mention. The ``conversation_id`` is passed to the
agent as the session/thread key so the LangGraph thread stays consistent across
turns. DMs use a wider window (they're 1:1 sessions).

Ported from ``-deprecated-gina`` (which mirrors Workstacean's ConversationManager
— same key shape, same timeout semantics, same get_or_create / has / end API).

Threads are the exception to the ``(channel, user)`` key: a Discord thread is a
shared space, so thread conversations are keyed by ``thread_id`` alone — any
participant continues the same conversation. ``alias_thread`` links an
auto-created thread back onto the (channel, user) conversation it grew out of;
``get_or_create_thread`` also serves thread-native conversations (🔬 research
threads) with no channel ancestor.

Peer channels (cross-operator agent chat) are keyed by ``channel_id`` alone too
(``peer:<channel_id>``): both operators' bots and both humans share one
conversation there. The entry also carries the bot↔bot turn budget
(``peer_turns``) that bounds an exchange until a human speaks again.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("protoagent.discord.conversation")


@dataclass
class ConversationEntry:
    conversation_id: str
    channel_id: str
    user_id: str
    started_at: float
    last_activity: float
    timeout_s: float
    turn_number: int
    # Peer channels: consecutive turns this bot has taken in reply to a PEER
    # AGENT (reset by any admin-human message in the channel). ``peer_paused``
    # is set once the budget notice has been posted so it's posted once.
    peer_turns: int = 0
    peer_paused: bool = False


class ConversationManager:
    """Tracks active multi-turn conversations by ``(channel_id, user_id)``,
    plus thread conversations keyed by ``thread_id`` alone (see module doc).

    A periodic sweep (every ``sweep_interval_s``) drops expired conversations.
    """

    def __init__(self, sweep_interval_s: float = 30.0, *, turn_log: Any = None):
        self._conversations: dict[str, ConversationEntry] = {}
        self._sweep_interval_s = sweep_interval_s
        self._sweep_task: asyncio.Task | None = None
        # Optional durable turn log (duck-typed: needs ``thread_conversation``).
        # Lets a thread rejoin its old conversation after expiry/restart. None
        # keeps the original mint-on-miss behaviour unchanged.
        self._turn_log = turn_log

    def set_turn_log(self, turn_log: Any) -> None:
        """Wire the turn log used to restore a thread's conversation on a miss
        (the gateway builds its ``TurnLog`` lazily, so it attaches it here). With
        no turn log, ``get_or_create_thread`` mints a fresh id on a miss as before."""
        self._turn_log = turn_log

    def start(self) -> None:
        """Start the periodic sweep. Idempotent."""
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        if self._sweep_task is not None:
            self._sweep_task.cancel()
            try:
                await self._sweep_task
            except asyncio.CancelledError:
                pass
            self._sweep_task = None

    @staticmethod
    def _key(channel_id: str, user_id: str) -> str:
        return f"{channel_id}:{user_id}"

    @staticmethod
    def _thread_key(thread_id: str) -> str:
        # Distinct namespace — a bare thread_id must not collide with the
        # "{channel_id}:{user_id}" shape.
        return f"thread:{thread_id}"

    def get_or_create(self, channel_id: str, user_id: str, *, timeout_s: float = 300.0) -> tuple[str, bool, int]:
        """Return ``(conversation_id, is_new, turn_number)``. An active
        (unexpired) conversation bumps the turn number; otherwise a fresh one
        starts."""
        key = self._key(channel_id, user_id)
        now = time.monotonic()
        existing = self._conversations.get(key)
        if existing and (now - existing.last_activity) < existing.timeout_s:
            existing.last_activity = now
            existing.turn_number += 1
            return existing.conversation_id, False, existing.turn_number

        conversation_id = str(uuid.uuid4())
        self._conversations[key] = ConversationEntry(
            conversation_id=conversation_id,
            channel_id=channel_id,
            user_id=user_id,
            started_at=now,
            last_activity=now,
            timeout_s=timeout_s,
            turn_number=1,
        )
        return conversation_id, True, 1

    def alias_thread(self, thread_id: str, channel_id: str, user_id: str) -> bool:
        """Alias a thread onto the live ``(channel, user)`` conversation it grew
        out of. Follow-ups posted inside the thread (``channel_id == thread_id``
        on the wire) then continue the same conversation — keyed by thread alone,
        so teammates can join. The alias shares the entry object, so activity in
        the thread keeps the origin conversation warm (and vice versa). Returns
        False when there is no live conversation to alias."""
        entry = self._conversations.get(self._key(channel_id, user_id))
        if entry is None or (time.monotonic() - entry.last_activity) >= entry.timeout_s:
            return False
        self._conversations[self._thread_key(thread_id)] = entry
        return True

    def get_or_create_thread(self, thread_id: str, *, timeout_s: float = 300.0) -> tuple[str, bool, int]:
        """Thread-keyed twin of ``get_or_create`` — no user in the key, so any
        participant continues the conversation. Finds an ``alias_thread`` entry
        when one exists; otherwise, on a miss, rejoins the thread's prior
        conversation from the durable turn log (survives expiry/restart) before
        falling back to a thread-native conversation (🔬) with a fresh id."""
        key = self._thread_key(thread_id)
        now = time.monotonic()
        existing = self._conversations.get(key)
        if existing and (now - existing.last_activity) < existing.timeout_s:
            existing.last_activity = now
            existing.turn_number += 1
            return existing.conversation_id, False, existing.turn_number

        # Miss: before minting, try to reuse the thread's logged conversation_id
        # so the swept/restarted thread keeps its LangGraph session (and history).
        restored = self.restore_thread(thread_id)
        if restored is not None:
            conversation_id, origin_channel = restored
        else:
            conversation_id = str(uuid.uuid4())
            origin_channel = thread_id
        self._conversations[key] = ConversationEntry(
            conversation_id=conversation_id,
            channel_id=origin_channel,  # origin channel for an aliased thread; the thread itself otherwise
            user_id="",  # shared space — no single owner
            started_at=now,
            last_activity=now,
            timeout_s=timeout_s,
            turn_number=1,
        )
        return conversation_id, True, 1

    def restore_thread(self, thread_id: str) -> tuple[str, str] | None:
        """Look up a thread's prior conversation in the durable turn log:
        ``(conversation_id, origin_channel)`` when the thread has logged turns,
        else None. Called by ``get_or_create_thread`` on a miss so a thread that
        was swept (expiry) or lost to a restart rejoins its old conversation
        instead of minting a new id (ADR 0131 P2). A lookup failure degrades to
        None (mint a fresh id) rather than dropping the turn."""
        if self._turn_log is None:
            return None
        try:
            return self._turn_log.thread_conversation(thread_id)
        except Exception:
            log.exception("[conversation] thread restore lookup failed for %s", thread_id)
            return None

    def has_thread(self, thread_id: str) -> bool:
        entry = self._conversations.get(self._thread_key(thread_id))
        if entry is None:
            return False
        return (time.monotonic() - entry.last_activity) < entry.timeout_s

    def thread_origin(self, thread_id: str) -> str | None:
        """The channel the thread conversation is anchored to: the origin
        channel for an aliased auto-thread, the thread itself for a
        thread-native (🔬) conversation, None when untracked."""
        entry = self._conversations.get(self._thread_key(thread_id))
        return entry.channel_id if entry is not None else None

    # ── peer channels (cross-operator agent chat) ─────────────────────────

    @staticmethod
    def _peer_key(channel_id: str) -> str:
        return f"peer:{channel_id}"

    def get_or_create_peer(self, channel_id: str, *, timeout_s: float = 900.0) -> tuple[str, bool, int]:
        """Channel-keyed conversation for a peer channel — bots and humans all
        continue the same one. Same contract as ``get_or_create``."""
        key = self._peer_key(channel_id)
        now = time.monotonic()
        existing = self._conversations.get(key)
        if existing and (now - existing.last_activity) < existing.timeout_s:
            existing.last_activity = now
            existing.turn_number += 1
            return existing.conversation_id, False, existing.turn_number

        conversation_id = str(uuid.uuid4())
        self._conversations[key] = ConversationEntry(
            conversation_id=conversation_id,
            channel_id=channel_id,
            user_id="",  # shared space — no single owner
            started_at=now,
            last_activity=now,
            timeout_s=timeout_s,
            turn_number=1,
        )
        return conversation_id, True, 1

    def peer_entry(self, channel_id: str) -> ConversationEntry | None:
        """The live peer-channel entry (for the turn budget), or None."""
        entry = self._conversations.get(self._peer_key(channel_id))
        if entry is None or (time.monotonic() - entry.last_activity) >= entry.timeout_s:
            return None
        return entry

    def has_peer(self, channel_id: str) -> bool:
        return self.peer_entry(channel_id) is not None

    def end_peer(self, channel_id: str) -> None:
        self._conversations.pop(self._peer_key(channel_id), None)

    def has(self, channel_id: str, user_id: str) -> bool:
        entry = self._conversations.get(self._key(channel_id, user_id))
        if entry is None:
            return False
        return (time.monotonic() - entry.last_activity) < entry.timeout_s

    def end(self, channel_id: str, user_id: str) -> bool:
        return self._conversations.pop(self._key(channel_id, user_id), None) is not None

    async def _sweep_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._sweep_interval_s)
                self._sweep_once()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("[conversation] sweep loop crashed")

    def _sweep_once(self) -> None:
        now = time.monotonic()
        expired = [key for key, entry in self._conversations.items() if (now - entry.last_activity) >= entry.timeout_s]
        for key in expired:
            entry = self._conversations.pop(key, None)
            if entry is not None:
                log.info(
                    "[conversation] timed out %s (%d turn(s), user %s in %s)",
                    entry.conversation_id,
                    entry.turn_number,
                    entry.user_id,
                    entry.channel_id,
                )
