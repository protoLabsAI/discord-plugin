"""Manifest is valid data + stays version-coherent with pyproject."""

from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return yaml.safe_load((ROOT / "protoagent.plugin.yaml").read_text())


def test_manifest_basics():
    m = _manifest()
    assert m["id"] == "discord"
    assert m["config_section"] == "discord"
    assert m["secrets"] == ["bot_token"]
    assert m["enabled"] is False  # ships disabled — enabling is the operator's trust decision
    assert {s["key"] for s in m["settings"]} == {"enabled", "bot_token", "admin_ids"}


def test_version_coherence():
    m = _manifest()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert m["version"] == pyproject["project"]["version"]
