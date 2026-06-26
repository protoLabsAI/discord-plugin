"""Console view for the Discord plugin — connection + bot status dashboard.

Public page at /plugins/discord/view (declared in manifest public_paths); links the
host design-system plugin-kit so it's themed from the operator's live `--pl-*`
tokens, and uses the kit's `apiFetch` to read the gated /api/plugins/discord/* routes.
"""

import os

PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Discord</title>
<script>
  window.__base = location.pathname.split("/plugins/")[0];
  document.write('<link rel="stylesheet" href="' + window.__base + '/_ds/plugin-kit.css">');
</script>
<style>
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--pl-color-bg-raised);color:var(--pl-color-fg);
    font-family:var(--pl-font-sans,ui-sans-serif,system-ui,sans-serif);font-size:13px}
  .wrap{max-width:760px;margin:0 auto;padding:20px}
  h1{font-size:17px;margin:0 0 2px} .sub{color:var(--pl-color-fg-muted);margin:0 0 18px;font-size:12px}
  .row{display:flex;align-items:center;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--pl-color-border)}
  .row:last-child{border-bottom:none} .k{color:var(--pl-color-fg-muted)}
  .empty{color:var(--pl-color-fg-muted);padding:8px 0} .err{color:var(--pl-color-status-error);font-size:12px}
</style></head><body>
<div class="wrap">
  <h1>Discord</h1>
  <p class="sub">Inbound DM/@-mention gateway + outbound tools.</p>
  <div class="pl-card" id="status"><div class="empty">Loading…</div></div>
</div>
<script type="module">
  "use strict";
  let kit;
  try { kit = await import(window.__base + "/_ds/plugin-kit.js"); }
  catch (e) { kit = { initPluginView(cb){ cb && cb(); }, apiFetch:(p,i)=>fetch(window.__base+p,i) }; }
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s==null?"":s).replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
  const badge = (variant, text) => '<span class="pl-badge pl-badge--'+variant+'">'+esc(text)+'</span>';
  const row = (k, v) => '<div class="row"><span class="k">'+esc(k)+'</span>'+v+'</div>';

  async function load(){
    try{
      const r = await kit.apiFetch("/api/plugins/discord/status");
      if(!r.ok){ $("status").innerHTML='<div class="err">Status '+r.status+'</div>'; return; }
      const s = await r.json();
      const gw = s.gateway || {};
      const gwState = gw.ready ? badge("success","ready · "+(gw.guilds||0)+" guild(s)")
                    : gw.connected ? badge("warning","connecting…")
                    : badge("error","offline");
      const botState = !s.token_configured ? '<span class="k">no token</span>'
                    : (s.bot && s.bot.ok) ? badge("success", s.bot.username||"valid")
                    : badge("error", (s.bot && s.bot.error) || "invalid token");
      $("status").innerHTML =
        row("Enabled", badge(s.enabled?"success":"warning", s.enabled?"on":"off")) +
        row("Bot token", badge(s.token_configured?"success":"error", s.token_configured?"configured":"not set")) +
        row("Bot identity", botState) +
        row("Gateway", gwState) +
        row("Admin allowlist", '<span class="k">'+(s.admin_ids?(s.admin_ids+" id(s)"):"anyone")+'</span>');
    }catch(e){ $("status").innerHTML='<div class="err">'+esc(e)+'</div>'; }
  }
  kit.initPluginView(load);
  load();
</script></body></html>"""


def build_view_routers(registry):
    """Page (/plugins/discord/view) + gated data (/api/plugins/discord/*)."""
    from fastapi import APIRouter
    from fastapi.responses import HTMLResponse

    page = APIRouter()

    @page.get("/view")
    async def view():
        return HTMLResponse(PAGE)

    data = APIRouter()

    @data.get("/status")
    async def status() -> dict:
        from .gateway import gateway_status, validate_token

        cfg = registry.config or {}
        token = cfg.get("bot_token") or os.environ.get("DISCORD_BOT_TOKEN", "")
        bot = None
        if token:
            try:
                ok, info, err = await validate_token(token)
                bot = {"ok": ok, "username": (info or {}).get("username"), "error": err}
            except Exception as exc:  # noqa: BLE001
                bot = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {
            "enabled": bool(cfg.get("enabled")),
            "token_configured": bool(token),
            "admin_ids": len(cfg.get("admin_ids") or []),
            "gateway": gateway_status(),
            "bot": bot,
        }

    return page, data
