"""Console view for the Discord plugin — connection + bot status dashboard.

Public page at /plugins/discord/view (declared in manifest public_paths); data from
the gated /api/plugins/discord/* routes with the operator bearer.
"""

import os

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Discord</title>
<style>
  :root{--bg:#0a0f14;--fg:#e6e6e6;--muted:#9aa0aa;--card:#11161c;--line:#1f2630;--accent:#9b87f2}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--fg);
    font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;font-size:14px}
  .wrap{max-width:720px;margin:0 auto;padding:24px}
  h1{font-size:18px;margin:0 0 2px} .sub{color:var(--muted);margin:0 0 20px;font-size:13px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
  .row{display:flex;align-items:center;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line)}
  .row:last-child{border-bottom:none} .k{color:var(--muted)}
  .badge{font-weight:600} .ok{color:#46c46a} .no{color:#e5687a} .warn{color:#e0b34a}
  .err{color:#e5687a;font-size:13px} .empty{color:var(--muted);padding:8px 0}
</style></head><body><div class="wrap">
  <h1>Discord</h1>
  <p class="sub">Inbound DM/@-mention gateway + outbound tools.</p>
  <div class="card" id="status"><div class="empty">Loading…</div></div>
</div>
<script>
  var BASE = location.pathname.replace(/\\/plugins\\/discord\\/view.*$/, "");
  var TOKEN = "";
  function authed(){ return TOKEN ? {Authorization:"Bearer "+TOKEN} : {}; }
  function esc(s){ return (s||"").replace(/[&<>]/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;"}[c];}); }
  function badge(ok, yes, no, cls){ return '<span class="badge '+(ok?(cls||'ok'):'no')+'">'+(ok?yes:no)+'</span>'; }
  function row(k,v){ return '<div class="row"><span class="k">'+k+'</span>'+v+'</div>'; }

  async function load(){
    try{
      var r = await fetch(BASE+"/api/plugins/discord/status", {headers:authed()});
      if(!r.ok){ document.getElementById("status").innerHTML='<div class="err">Status '+r.status+'</div>'; return; }
      var s = await r.json();
      var gw = s.gateway||{};
      var gwState = gw.ready ? badge(true,"ready · "+(gw.guilds||0)+" guild(s)","") :
                    gw.connected ? '<span class="badge warn">connecting…</span>' :
                    '<span class="badge no">offline</span>';
      var botState = !s.token_configured ? '<span class="k">no token</span>' :
                     (s.bot && s.bot.ok) ? badge(true, esc(s.bot.username||"valid"), "") :
                     '<span class="badge no">'+esc((s.bot&&s.bot.error)||"invalid token")+'</span>';
      document.getElementById("status").innerHTML =
        row("Enabled", badge(s.enabled,"on","off","warn")) +
        row("Bot token", badge(s.token_configured,"configured","not set")) +
        row("Bot identity", botState) +
        row("Gateway", gwState) +
        row("Admin allowlist", '<span class="k">'+(s.admin_ids?(s.admin_ids+" id(s)"):"anyone")+'</span>');
    }catch(e){ document.getElementById("status").innerHTML='<div class="err">'+e+'</div>'; }
  }
  function applyTheme(t){ if(!t)return; if(t.bg)document.body.style.background=t.bg; if(t.fg)document.body.style.color=t.fg; }
  window.addEventListener("message", function(e){
    var m=e.data||{};
    if(m.type==="protoagent:init"){ TOKEN=m.token||""; applyTheme(m.theme); load(); }
    else if(m.type==="protoagent:theme"){ applyTheme(m.theme); }
  });
  setTimeout(function(){ if(!TOKEN) load(); }, 800);
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
