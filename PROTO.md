# discord-plugin

Standalone protoAgent plugin — Discord inbound gateway + outbound tools. Talks to Discord REST + Gateway v10 directly via httpx + websockets (no discord.py). Installed at runtime via `plugin install`; not bundled with the host.

## Architecture

| Module | Responsibility |
|---|---|
| `__init__.py` | Plugin wiring: `register()` → surface + router + tools. Config seeding to both gateway and tools. |
| `gateway.py` | Inbound listener — raw Discord Gateway v10 over websockets. Burst debounce, conversation continuity, slow-response reactions, auto-threading, admin allowlist. ~590 LOC. |
| `conversation.py` | Per-(channel, user) conversation state with timeout windows. Session key generation for LangGraph thread continuity. |
| `context.py` | Context-envelope assembly — wraps user messages with `<recent_conversation>` from the turn log. |
| `turn_log.py` | Persistent SQLite log of Discord turns for long-window context across timeouts/restarts. Instance-scoped via `paths.scope_leaf`. |
| `return_address.py` | Stores the operator's DM channel ID so proactive/scheduled turns can deliver to Discord. |
| `tools.py` | Stateless outbound REST tools: send, dm, read, react, whoami, list_guilds, list_channels. Off unless a token is configured. |
| `view.py` | Console status dashboard — public iframe page + gated data routes. Uses the host design-system plugin-kit. |

## Host-free constraint

The test suite runs with NO protoAgent host. All `graph.*` and `infra.*` imports MUST be lazy (inside functions, never at module top level). `conftest.py` registers a synthetic `discord` package so relative imports resolve standalone. A top-level host import will break `pytest -q` and CI.

## Build / Test / Lint

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt ruff
ruff check .
ruff format --check tests
pytest -q
```

**Gate command (the one CI runs):**
```
pip install -r requirements-dev.txt ruff && ruff check . && ruff format --check tests && pytest -q
```

## Conventions

- **Ruff config** in `pyproject.toml`: line-length 120, target py311, select E/F/W, ignore E402/E501/E702/E731/E741. `__init__.py` ignores F401.
- **`ruff format`** is scoped to `tests/` only — the ported source follows protoAgent's check-only style. Do NOT reformat source files outside `tests/`.
- **Version** in `protoagent.plugin.yaml` and `pyproject.toml` must match (enforced by `test_manifest`).
- **No runtime pip deps** — the plugin relies on the host's core deps (httpx, websockets, fastapi, langchain-core, pydantic).
- **Token two-source precedence**: in-app config (`secrets.yaml`) takes priority over `DISCORD_BOT_TOKEN` env. Both gateway and tools get seeded from `__init__._seed()`.
- **Test fixtures**: `FakeRegistry` + `FakeHost` in `conftest.py`. The `_reset_configured_tokens` autouse fixture clears module-global token state between tests.

## Do / Don't

- **DO** keep all host imports (`graph.*`, `infra.*`) inside functions.
- **DO** test against `FakeRegistry`/`FakeHost` — never import the real host.
- **DO** keep `protoagent.plugin.yaml` version and `pyproject.toml` version in sync.
- **DON'T** add `discord.py` or any Discord library as a dependency.
- **DON'T** `ruff format` source files outside `tests/`.
- **DON'T** add runtime pip dependencies — use what the host provides.
