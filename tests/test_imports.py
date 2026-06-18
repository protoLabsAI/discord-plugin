"""Every module imports with no protoAgent host (the host-free contract)."""

from __future__ import annotations

import importlib


def test_all_modules_import_host_free():
    for mod in (
        "discord",
        "discord.gateway",
        "discord.conversation",
        "discord.turn_log",
        "discord.context",
        "discord.return_address",
        "discord.tools",
    ):
        importlib.import_module(mod)


def test_no_host_import_at_module_top():
    # graph.*/infra.* must never be importable at module top (they're lazy). If they
    # were top-level, importing the modules above would have raised ModuleNotFoundError.
    import sys

    assert "graph" not in sys.modules
    assert "infra" not in sys.modules
