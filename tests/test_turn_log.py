"""The turn log actually resolves a path and round-trips — not just imports.

This suite exists because the previous one didn't catch a total feature outage.
``tests/test_imports.py`` proved every module *imports* host-free, and it passed
throughout: ``_default_db_path()`` called ``infra.paths.scope_leaf``, which the host
removed in the two-tier instance-paths cutover (ADR 0065), and the ImportError only
fired when the function was *called* — where ``gateway._get_turn_log()`` swallowed it
and disabled long-window context. Green tests, no turns.db written anywhere, no signal.

So these tests call the thing.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

import pytest


@pytest.fixture
def turn_log_mod():
    return importlib.import_module("discord.turn_log")


def test_default_db_path_resolves_through_the_sdk(turn_log_mod, monkeypatch, tmp_path):
    """The default path uses the released host seam, not a core path implementation."""
    monkeypatch.delenv("DISCORD_LOG_PATH", raising=False)
    calls = []

    class FakeSdk:
        @staticmethod
        def plugin_store(*, plugin_id):
            calls.append(plugin_id)
            return tmp_path

    graph = ModuleType("graph")
    graph.sdk = FakeSdk
    monkeypatch.setitem(sys.modules, "graph", graph)

    path = turn_log_mod._default_db_path()

    assert path == tmp_path / "turns.db"
    assert calls == ["discord"]


def test_env_override_wins(turn_log_mod, monkeypatch, tmp_path):
    target = tmp_path / "nested" / "custom.db"
    monkeypatch.setenv("DISCORD_LOG_PATH", str(target))

    path = turn_log_mod._default_db_path()

    assert path == target
    assert path.parent.is_dir()


def test_turn_log_constructs_and_round_trips(turn_log_mod, tmp_path):
    """A default-constructed TurnLog is what the gateway builds; it must work."""
    log = turn_log_mod.TurnLog(tmp_path / "turns.db")
    log.record_user_turn("chan-1", "user-1", "what did we decide?")
    log.record_assistant_turn("chan-1", "user-1", "to ship it")

    turns = log.get_recent_turns("chan-1", "user-1")

    assert [t.role for t in turns] == ["user", "assistant"]
    assert [t.content for t in turns] == ["what did we decide?", "to ship it"]


def test_gateway_gets_a_working_turn_log(monkeypatch, tmp_path):
    """The end-to-end assertion the outage needed: the gateway's lazy accessor returns a
    log, not None. It catches every exception by design, so only calling it proves the
    feature is on."""
    monkeypatch.setenv("DISCORD_LOG_PATH", str(tmp_path / "turns.db"))
    gateway = importlib.import_module("discord.gateway")
    monkeypatch.setattr(gateway, "_turn_log", None, raising=False)

    assert gateway._get_turn_log() is not None, (
        "the gateway disabled its turn log — long-window context is off; "
        "check the logged '[discord] turn-log init failed' reason"
    )
