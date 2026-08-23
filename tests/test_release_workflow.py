"""Release workflow is a valid, thin caller of the org's reusable plugin-release.

Mirrors ``test_manifest``: parse the YAML as data and assert its shape, so a bad
edit (wrong ref, dropped guard, missing dispatch) fails in CI rather than on main.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
RELEASE_YML = ROOT / ".github" / "workflows" / "release.yml"


def _workflow() -> dict:
    return yaml.safe_load(RELEASE_YML.read_text())


def _triggers(wf: dict) -> dict:
    # PyYAML follows YAML 1.1, where the bareword ``on`` is the boolean True — so the
    # `on:` key deserializes to the Python key ``True``. Accept either spelling.
    return wf.get("on", wf.get(True))


def test_release_workflow_exists():
    assert RELEASE_YML.is_file()


def test_triggers_on_main_push_and_manual_dispatch():
    triggers = _triggers(_workflow())
    assert triggers["push"]["branches"] == ["main"]
    # workflow_dispatch present (value is None for a bare `workflow_dispatch:`).
    assert "workflow_dispatch" in triggers


def test_calls_reusable_plugin_release_with_inherited_secrets():
    job = _workflow()["jobs"]["release"]
    assert job["uses"] == "protoLabsAI/release-tools/.github/workflows/plugin-release.yml@v2"
    assert job["secrets"] == "inherit"


def test_guard_scopes_to_repo_and_release_or_dispatch():
    guard = _workflow()["jobs"]["release"]["if"]
    # Only ever runs in the canonical repo — a fork push can't cut a release.
    assert "github.repository == 'protoLabsAI/discord-plugin'" in guard
    # Fires on a manual dispatch OR a `chore: release vX.Y.Z` push, and nothing else.
    assert "github.event_name == 'workflow_dispatch'" in guard
    assert "startsWith(github.event.head_commit.message, 'chore: release v')" in guard
