"""ConversationManager — (channel, user) continuity + thread-keyed conversations."""

from __future__ import annotations

from discord.conversation import ConversationManager


def test_get_or_create_reuses_within_timeout():
    mgr = ConversationManager()
    conv_id, is_new, turn = mgr.get_or_create("chan", "user", timeout_s=300)
    assert is_new and turn == 1
    again, is_new2, turn2 = mgr.get_or_create("chan", "user", timeout_s=300)
    assert again == conv_id and not is_new2 and turn2 == 2


def test_get_or_create_expired_starts_fresh():
    mgr = ConversationManager()
    conv_id, _, _ = mgr.get_or_create("chan", "user", timeout_s=0.0)  # instantly expired
    assert not mgr.has("chan", "user")
    fresh, is_new, turn = mgr.get_or_create("chan", "user", timeout_s=300)
    assert fresh != conv_id and is_new and turn == 1


def test_alias_thread_joins_existing_conversation():
    mgr = ConversationManager()
    conv_id, _, _ = mgr.get_or_create("chan", "alice", timeout_s=300)
    assert mgr.alias_thread("thread-1", "chan", "alice") is True
    assert mgr.has_thread("thread-1")
    assert mgr.thread_origin("thread-1") == "chan"
    same, is_new, turn = mgr.get_or_create_thread("thread-1", timeout_s=300)
    assert same == conv_id and not is_new and turn == 2


def test_alias_thread_without_live_conversation():
    mgr = ConversationManager()
    assert mgr.alias_thread("thread-1", "chan", "alice") is False
    assert not mgr.has_thread("thread-1")
    mgr.get_or_create("chan", "alice", timeout_s=0.0)  # expired — still no alias
    assert mgr.alias_thread("thread-1", "chan", "alice") is False


def test_thread_key_has_no_user_component():
    # A teammate's turn in the thread continues the conversation alice started.
    mgr = ConversationManager()
    conv_id, _, _ = mgr.get_or_create("chan", "alice", timeout_s=300)
    mgr.alias_thread("thread-1", "chan", "alice")
    same, is_new, _ = mgr.get_or_create_thread("thread-1", timeout_s=300)
    assert same == conv_id and not is_new


def test_get_or_create_thread_native():
    # 🔬 research threads have no channel ancestor — the thread is the origin.
    mgr = ConversationManager()
    conv_id, is_new, turn = mgr.get_or_create_thread("thread-9", timeout_s=300)
    assert is_new and turn == 1
    assert mgr.thread_origin("thread-9") == "thread-9"
    again, is_new2, turn2 = mgr.get_or_create_thread("thread-9", timeout_s=300)
    assert again == conv_id and not is_new2 and turn2 == 2


def test_thread_key_does_not_collide_with_channel_key():
    mgr = ConversationManager()
    chan_conv, _, _ = mgr.get_or_create("thread-1", "user", timeout_s=300)
    thread_conv, _, _ = mgr.get_or_create_thread("thread-1", timeout_s=300)
    assert chan_conv != thread_conv


def test_sweep_drops_expired_thread_aliases():
    mgr = ConversationManager()
    mgr.get_or_create("chan", "alice", timeout_s=300)
    mgr.alias_thread("thread-1", "chan", "alice")
    for entry in mgr._conversations.values():
        entry.last_activity -= 1000
    mgr._sweep_once()
    assert mgr._conversations == {}
    assert not mgr.has_thread("thread-1")


# ── thread restore from the durable turn log (ADR 0131 P2) ────────────────────


class _FakeTurnLog:
    """Minimal stand-in for TurnLog: only the bit restore_thread calls."""

    def __init__(self, mapping=None):
        # thread_id -> (conversation_id, origin_channel)
        self._mapping = dict(mapping or {})

    def thread_conversation(self, thread_id):
        return self._mapping.get(thread_id)


def test_get_or_create_thread_restores_after_expiry():
    # After the in-memory entry expires and is swept, a thread with logged turns
    # rejoins the SAME conversation_id instead of minting a new one.
    tlog = _FakeTurnLog()
    mgr = ConversationManager(turn_log=tlog)
    conv_id, is_new, _ = mgr.get_or_create_thread("thread-1", timeout_s=300)
    assert is_new
    # The gateway would have logged this thread's turns; model that in the log.
    tlog._mapping["thread-1"] = (conv_id, "chan-origin")

    for entry in mgr._conversations.values():
        entry.last_activity -= 1000
    mgr._sweep_once()
    assert not mgr.has_thread("thread-1")

    restored, _, turn = mgr.get_or_create_thread("thread-1", timeout_s=300)
    assert restored == conv_id
    assert turn == 1
    assert mgr.thread_origin("thread-1") == "chan-origin"


def test_get_or_create_thread_restores_after_restart():
    # A brand-new manager (process restart: empty in-memory map) still rejoins
    # the thread's logged conversation and keeps it warm for follow-ups.
    tlog = _FakeTurnLog({"thread-1": ("conv-persisted", "chan-origin")})
    mgr = ConversationManager(turn_log=tlog)

    restored, _, _ = mgr.get_or_create_thread("thread-1", timeout_s=300)
    assert restored == "conv-persisted"
    assert mgr.thread_origin("thread-1") == "chan-origin"

    again, is_new, turn = mgr.get_or_create_thread("thread-1", timeout_s=300)
    assert again == "conv-persisted" and not is_new and turn == 2


def test_set_turn_log_wires_restore_after_construction():
    # The gateway builds its TurnLog lazily and attaches it with set_turn_log.
    mgr = ConversationManager()
    mgr.set_turn_log(_FakeTurnLog({"thread-1": ("conv-wired", "chan-origin")}))
    restored, _, _ = mgr.get_or_create_thread("thread-1", timeout_s=300)
    assert restored == "conv-wired"


def test_get_or_create_thread_brand_new_mints_without_turn_log():
    # No turn log wired → unchanged behaviour: a miss mints a fresh id.
    mgr = ConversationManager()
    conv_id, is_new, turn = mgr.get_or_create_thread("thread-new", timeout_s=300)
    assert is_new and turn == 1 and conv_id
    assert mgr.thread_origin("thread-new") == "thread-new"


def test_get_or_create_thread_brand_new_mints_with_empty_turn_log():
    # Turn log wired but nothing logged for the thread → still mints a fresh,
    # distinct id (origin is the thread itself).
    mgr = ConversationManager(turn_log=_FakeTurnLog())
    conv_a, is_new, _ = mgr.get_or_create_thread("thread-a", timeout_s=300)
    assert is_new and conv_a
    conv_b, _, _ = mgr.get_or_create_thread("thread-b", timeout_s=300)
    assert conv_b != conv_a
    assert mgr.thread_origin("thread-a") == "thread-a"


def test_restore_thread_tolerates_lookup_failure():
    # A turn-log read error must not drop the turn — it degrades to a fresh id.
    class _BoomLog:
        def thread_conversation(self, thread_id):
            raise RuntimeError("db gone")

    mgr = ConversationManager(turn_log=_BoomLog())
    conv_id, is_new, _ = mgr.get_or_create_thread("thread-x", timeout_s=300)
    assert is_new and conv_id
