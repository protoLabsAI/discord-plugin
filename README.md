# discord-plugin

Run your protoAgent as a **Discord** bot — inbound DMs + channel @-mentions, with
outbound `discord_send` / `discord_read` / `discord_react` tools. A standalone
[protoAgent](https://github.com/protoLabsAI/protoAgent) plugin (ADR 0015/0016 ·
0018/0019), extracted from core per **ADR 0058** so it installs at runtime and
isn't bundled with the host.

Talks to Discord's REST + Gateway **v10** directly over `httpx` + `websockets`
(both core host deps) — no `discord.py`.

## Install

```bash
# from the protoAgent host (CLI or Settings → Plugins → install from URL)
python -m server plugin install https://github.com/protoLabsAI/discord-plugin
```

Install ≠ enable ≠ trust — it ships **disabled**. Enable it, then set a bot token:

1. **Enable:** `plugins: { enabled: [discord] }` (or the Settings → Plugins toggle).
2. **Configure:** System → Settings → **Discord** → paste a bot token (Developer
   Portal → your app → Bot → Reset Token), optionally restrict `admin_ids`, and
   hit **Test connection**. Saving reconnects the gateway live.

The bot token is stored in `secrets.yaml` (never tracked YAML). `DISCORD_BOT_TOKEN`
/ `DISCORD_ADMIN_IDS` remain env fallbacks for Docker/headless deploys.

## What it contributes

- **Surface** — the inbound gateway (DMs + @-mentions): burst debounce,
  per-conversation continuity, slow-response reactions (👀→✅), auto-threading,
  admin allowlist, long-window context, and return-address delivery.
- **Route** — `POST /api/config/test-discord` (the console's Test button).
- **Tools** — registered only when a token is set, from Settings → Discord *or*
  the `DISCORD_BOT_TOKEN` env. Saving a token rebuilds the graph, so they appear
  without a restart.

  | Tool | Does |
  | --- | --- |
  | `discord_send` | Post to a channel (long messages auto-split at 2000 chars) |
  | `discord_dm` | DM a **user** — opens the 1:1 channel first; a user ID is not a channel ID |
  | `discord_read` | Recent channel history |
  | `discord_react` | Add a reaction |
  | `discord_whoami` | Which bot account it posts as + the captured operator DM channel |
  | `discord_list_guilds` | Servers the bot is in |
  | `discord_list_channels` | Channel IDs in a server (defaults to the only server) |

  The last three exist because everything else is keyed by a numeric ID that
  nothing in the agent's context supplies — without them the toolset only works
  when an operator hand-feeds an ID into the persona.

Config/secrets/Settings come from `protoagent.plugin.yaml` (ADR 0019). Behavior is
identical to the former first-party `plugins/discord`.

## Develop

Host-free — the suite runs with no protoAgent host (the `graph.*` / `infra.*`
imports the gateway uses are lazy):

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check .
```

At runtime the host provides `langchain-core`, `fastapi`, `httpx`, `websockets`,
and the `graph.*` / `infra.*` packages — the plugin declares no runtime pip deps.

## Links

protoAgent guides: [plugins](https://github.com/protoLabsAI/protoAgent/blob/main/docs/guides/plugins.md)
· [communication-plugins](https://github.com/protoLabsAI/protoAgent/blob/main/docs/guides/communication-plugins.md)
· ADRs [0015](https://github.com/protoLabsAI/protoAgent/blob/main/docs/adr/0015-discord-ingress-surface.md)
/ [0058](https://github.com/protoLabsAI/protoAgent/blob/main/docs/adr/0058-runtime-plugin-install-frozen-app.md).
