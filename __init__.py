"""Discord ingress + outbound tools — a standalone protoAgent plugin.

ADR 0015/0016 (the surface) · 0018/0019 (the plugin contracts) · extracted from
core into its own repo per ADR 0058 (opt-in, installed via ``plugin install`` —
not bundled with the host). The gateway (``gateway.py`` + ``conversation`` /
``context`` / ``turn_log`` / ``return_address``) is the implementation; this
module's ``register()`` is the wiring.

Contributes: a **surface** (the inbound gateway, lifecycle-managed +
reconnect-on-save), a **route** (``POST /api/config/test-discord`` — the console's
Test button), and the outbound **tools** (``discord_send`` / ``discord_read`` /
``discord_react``, registered when a token is set). Config/secrets/Settings come
from the manifest (ADR 0019). Behavior is identical to the former first-party
``plugins/discord``.
"""

from __future__ import annotations

import logging
import os

from pydantic import BaseModel

log = logging.getLogger("protoagent.plugins.discord")


class DiscordProbe(BaseModel):
    """Body for the Test-connection route. **Must be module-level**: this file
    uses `from __future__ import annotations` (PEP 563), so a function-local
    model can't be resolved by FastAPI's `get_type_hints()` (which looks in
    module globals) — the body would be silently ignored and every token read
    as empty."""

    bot_token: str = ""


# Holds the running gateway task across start/stop/reload.
_state: dict = {"task": None}


def _should_start(cfg: dict) -> bool:
    """Start rule (mirrors the old `_start_discord_surface`): the UI path needs
    `enabled` + a token; a bare `DISCORD_BOT_TOKEN` env (no UI token) starts for
    Docker back-compat."""
    cfg_token = (cfg.get("bot_token") or "").strip()
    env_token = (os.environ.get("DISCORD_BOT_TOKEN") or "").strip()
    return (bool(cfg_token) and bool(cfg.get("enabled"))) or (not cfg_token and bool(env_token))


def _launch(cfg: dict, host) -> None:
    """Configure + (re)start the gateway from the given config + host services."""
    from .gateway import configure, start_in_background

    configure(cfg.get("bot_token"), cfg.get("admin_ids"))
    if not _should_start(cfg):
        log.info(
            "[discord] gateway not started (enabled=%s, token set=%s)",
            cfg.get("enabled"),
            bool((cfg.get("bot_token") or "").strip() or os.environ.get("DISCORD_BOT_TOKEN")),
        )
        return
    if not (host and host.invoke):
        log.warning("[discord] no agent invoke available — gateway not started")
        return
    _state["task"] = start_in_background(host.invoke, publish=host.publish, subscribe=host.subscribe)


async def _teardown() -> None:
    from .gateway import stop as _discord_stop

    task = _state.get("task")
    if task is not None:
        task.cancel()
        _state["task"] = None
    try:
        await _discord_stop()
    except Exception:  # noqa: BLE001
        log.exception("[discord] stop failed")


def _build_router(registry):
    """`POST /api/config/test-discord` — verify a bot token (the console's Test
    button). Mounted at the existing path (prefix="") so the UI is unchanged."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.post("/api/config/test-discord")
    async def _test_discord(req: DiscordProbe | None = None):
        from .gateway import validate_token

        body = req or DiscordProbe()
        token = body.bot_token or (registry.config.get("bot_token") or "")
        ok, bot_user, error = await validate_token(token)
        # `identity` is the generic test-route field the console renders (ADR 0029) —
        # the bot's username on success.
        return {"ok": ok, "error": error, "identity": (bot_user or {}).get("username") if ok else None}

    return router


def register(registry) -> None:
    host = registry.host

    def _start():
        _launch(registry.config, host)
        return _state.get("task")

    async def _reload(new_config):
        # Reconnect on a config change (Settings save / wizard finish) — the
        # surface reload hook (ADR 0018) keeps Discord's live-reconnect.
        new = (getattr(new_config, "plugin_config", {}) or {}).get("discord", {})
        await _teardown()
        registry.config = dict(new)
        _launch(registry.config, host)

    registry.register_surface(_start, stop=_teardown, reload=_reload, name="discord-gateway")
    registry.register_router(_build_router(registry), prefix="")  # existing /api path

    # Console view (ADR 0026): public page + gated data routes.
    try:
        from .view import build_view_routers

        _page, _data = build_view_routers(registry)
        registry.register_router(_page)  # default prefix /plugins/discord (public via public_paths)
        registry.register_router(_data, prefix="/api/plugins/discord")
    except Exception:  # noqa: BLE001 — view is best-effort
        log.exception("[discord] mounting view router failed")

    # Outbound tools — only when a token is set (off by default, as before). Seed
    # the client from the resolved config first so a UI-set token (not just the
    # DISCORD_BOT_TOKEN env) surfaces the tools at graph build.
    from .gateway import configure
    from .tools import discord_configured, get_discord_tools

    configure(registry.config.get("bot_token"), registry.config.get("admin_ids"))
    if discord_configured():
        registry.register_tools(get_discord_tools())
