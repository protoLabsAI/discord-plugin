"""The return-address store resolves a real path and round-trips — not just imports.

``_path()`` called ``infra.paths.scope_leaf``, which the host removed in the two-tier
instance-paths cutover (ADR 0065). ``record()`` swallows every exception by design, so
each operator DM logged an ImportError and nothing was ever stored: ``discord_whoami``
always said "none captured yet" and proactive output never reached the operator's DM.

So these tests call the thing, through a faked ``graph.sdk.plugin_store``.
"""

from __future__ import annotations

import importlib
import logging
import sys
from types import ModuleType

import pytest


@pytest.fixture
def ra():
    return importlib.import_module("discord.return_address")


@pytest.fixture
def fake_store(monkeypatch, tmp_path):
    """Install a fake ``graph.sdk`` whose plugin_store is a not-yet-existing dir."""
    monkeypatch.delenv("DISCORD_RETURN_ADDRESS_PATH", raising=False)
    calls = []
    store = tmp_path / "missing" / "discord"

    class FakeSdk:
        @staticmethod
        def plugin_store(*, plugin_id):
            calls.append(plugin_id)
            return store

    graph = ModuleType("graph")
    graph.sdk = FakeSdk
    monkeypatch.setitem(sys.modules, "graph", graph)
    monkeypatch.setitem(sys.modules, "graph.sdk", FakeSdk)
    return store, calls


def test_path_resolves_through_plugin_store(ra, fake_store):
    store, calls = fake_store

    path = ra._path()

    assert path == store / "return-address.json"
    assert path.parent.is_dir()
    assert calls == ["discord"]  # the manifest id, not a guess


def test_record_and_get_round_trip_through_plugin_store(ra, fake_store, caplog):
    store, _ = fake_store

    assert ra.get() is None
    with caplog.at_level(logging.ERROR, logger="protoagent.discord.return_address"):
        ra.record("dm-123")

    assert not caplog.records, "record() must not log a failure"
    assert (store / "return-address.json").is_file()
    assert ra.get() == "dm-123"

    ra.record("dm-456")  # a new DM channel replaces the old one
    assert ra.get() == "dm-456"


def test_env_override_wins_over_plugin_store(ra, fake_store, monkeypatch, tmp_path):
    store, calls = fake_store
    target = tmp_path / "nested" / "addr.json"
    monkeypatch.setenv("DISCORD_RETURN_ADDRESS_PATH", str(target))

    ra.record("dm-789")

    assert ra._path() == target
    assert ra.get() == "dm-789"
    assert target.is_file()
    assert not (store / "return-address.json").exists()
    assert calls == []


def test_manifest_id_matches_plugin_id(ra):
    from pathlib import Path

    import yaml

    manifest = yaml.safe_load((Path(__file__).resolve().parent.parent / "protoagent.plugin.yaml").read_text())
    assert manifest["id"] == ra._PLUGIN_ID


async def test_whoami_reports_a_recorded_dm_channel(ra, fake_store, monkeypatch):
    """The field symptom: whoami said "none captured yet" forever. Once record() works,
    whoami must surface the captured channel."""
    import discord.tools as dt

    ra.record("dm-321")

    async def fake_request(method, path, json_body=None):
        return (200, {"id": "bot-1", "username": "protoBot"})

    monkeypatch.setattr(dt, "_request", fake_request)
    out = await dt.discord_whoami.ainvoke({})

    assert "Operator DM channel: dm-321" in out
