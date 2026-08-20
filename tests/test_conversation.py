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
