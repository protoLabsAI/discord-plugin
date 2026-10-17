"""Discord return-address store (ADR 0015).

When the operator DMs the agent, the gateway records that **DM channel id** here.
Scheduler-fired and proactive turns have no originating caller — they need a
stored "where do I deliver?" address. With one captured, reactive Activity-thread
output (ADR 0003) is forwarded to the operator's Discord DM, so "remind me in 30
minutes" actually lands somewhere.

Single small JSON file in this plugin's instance-scoped store
(``sdk.plugin_store``, host ≥ 0.148). Override the location with
``DISCORD_RETURN_ADDRESS_PATH`` (used by tests).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("protoagent.discord.return_address")

_PLUGIN_ID = "discord"  # protoagent.plugin.yaml `id`
_LEAF = "return-address.json"
_KEY = "discord_dm_channel_id"


def _path() -> Path:
    """Where the return address lives: ``DISCORD_RETURN_ADDRESS_PATH``, else this
    instance's plugin store.

    The host import stays lazy so the module imports in the standalone test suite;
    the manifest's v0.148 floor guarantees the SDK seam when a host loads it.
    """
    override = os.environ.get("DISCORD_RETURN_ADDRESS_PATH")
    if override:
        p = Path(override)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    from graph import sdk

    p = sdk.plugin_store(plugin_id=_PLUGIN_ID) / _LEAF
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def get() -> str | None:
    """The captured Discord DM channel id, or ``None`` if none recorded yet."""
    try:
        p = _path()
        if not p.exists():
            return None
        return json.loads(p.read_text()).get(_KEY) or None
    except Exception:  # noqa: BLE001 — a missing/corrupt file just means "no address"
        return None


def record(channel_id: str) -> None:
    """Idempotently store ``channel_id`` as the operator's Discord return address.
    Best-effort — never raises (a failure just means proactive delivery is off)."""
    if not channel_id:
        return
    try:
        if get() == channel_id:
            return
        path = _path()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({_KEY: channel_id}))
        os.replace(tmp, path)  # atomic: a crash mid-write never leaves a truncated file
        log.info("[discord] recorded return address (DM channel %s)", channel_id)
    except Exception:
        log.exception("[discord] failed to record return address (non-fatal)")
