"""Test bootstrap — import the plugin with NO protoAgent host.

The host loads a plugin under a synthetic package; the suite does the same so the
modules' relative imports (``from .gateway import ...``) resolve standalone.
Executing ``__init__.py`` is safe: the host-only imports (``graph.*`` / ``infra.*``)
live inside functions, not at module top, so no host is needed to import.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = "discord"

# Register a synthetic `discord` package whose __path__ is the repo root, so
# `import discord` + `from discord.gateway import ...` resolve to the plugin files.
if PKG not in sys.modules:
    _spec = importlib.util.spec_from_file_location(PKG, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[PKG] = _mod
    _spec.loader.exec_module(_mod)


class FakeHost:
    """The plugin host seam (registry.host) — agent invoke + event bus."""

    def __init__(self):
        self.invoke = lambda *a, **k: None
        self.publish = lambda *a, **k: None
        self.subscribe = lambda *a, **k: None
        self.config = None
        self.apply_settings = None


class FakeRegistry:
    """Captures what register() contributes — no host required."""

    def __init__(self, config=None, host=None):
        self.config = config if config is not None else {}
        self.host = host or FakeHost()
        self.tools: list = []
        self.routers: list = []
        self.surfaces: list = []

    def register_tool(self, t):
        self.tools.append(t)

    def register_tools(self, ts):
        self.tools.extend(ts)

    def register_router(self, router, prefix=""):
        self.routers.append((prefix, router))

    def register_surface(self, start, stop=None, reload=None, name=None):
        self.surfaces.append({"name": name, "start": start, "stop": stop, "reload": reload})


@pytest.fixture(autouse=True)
def _reset_configured_tokens():
    """Clear the in-app token both halves cache, so a config-token test can't
    leak a token into the next test's "no token" assertions (module globals
    outlive a test; the env var is handled per-test by monkeypatch)."""
    import discord.gateway as gw
    import discord.tools as dt

    yield
    gw.configure(None, None)
    dt.configure(None, None)


@pytest.fixture
def registry():
    return FakeRegistry()
