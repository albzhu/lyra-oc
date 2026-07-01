#!/usr/bin/env python3
"""
oc_web.py — local web console for OpenClaw configuration.

A lightweight alternative to the osascript dialog tool: a single-page web app
served by Python's stdlib http.server, bound to 127.0.0.1 with a per-launch
token. No build step, no third-party deps. Shares all config logic with the
osascript tool via oc_config.py.

Phase 1: Models + Agents tabs fully functional. Reports + Terminal are stubs.
"""

import json
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import oc_config as C

# In-memory state for this single-user local session.
STATE = {"data": None, "dirty": False}
TOKEN = secrets.token_urlsafe(16)
LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# Action dispatch (mutates STATE["data"], sets dirty)
# --------------------------------------------------------------------------- #
def do_action(action, p):
    data = STATE["data"]
    if action == "set_primary":
        C.set_primary(data, p["key"], p["model_id"])
        if p.get("alias"):
            C.set_alias(data, p["key"], p["model_id"], p["alias"])
    elif action == "set_fallbacks":
        C.set_fallbacks(data, p["key"], p["fallbacks"])
    elif action == "set_alias":
        C.set_alias(data, p["key"], p["model_id"], p.get("alias", ""))
    elif action == "create_agent":
        C.create_agent(data, p["id"], p.get("name"), p.get("workspace") or None)
    elif action == "remove_agent":
        removed = C.remove_agent(data, p["key"])
        STATE["dirty"] = True
        return {"removed_bindings": len(removed)}
    elif action == "rename_agent":
        n = C.rename_agent(data, p["key"], p["new_id"])
        STATE["dirty"] = True
        return {"repointed_bindings": n}
    elif action == "edit_agent":
        node = C.agent_node(data, p["key"])
        if "name" in p:
            node["name"] = p["name"]
        if "workspace" in p:
            node["workspace"] = p["workspace"]
    else:
        raise ValueError(f"unknown action: {action}")
    STATE["dirty"] = True
    return {}


# --------------------------------------------------------------------------- #
# HTTP handler
# --------------------------------------------------------------------------- #
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _token_ok(self):
        from urllib.parse import urlparse, parse_qs
        q = parse_qs(urlparse(self.path).query)
        return self.headers.get("X-Token") == TOKEN or q.get("t", [None])[0] == TOKEN

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, HTML, "text/html; charset=utf-8")
        elif path == "/api/config":
            if not self._token_ok():
                return self._send(403, {"error": "bad token"})
            with LOCK:
                snap = C.snapshot(STATE["data"])
                snap["dirty"] = STATE["dirty"]
            self._send(200, snap)
        elif path == "/api/reports":
            if not self._token_ok():
                return self._send(403, {"error": "bad token"})
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(self.path).query)
            date = q.get("date", [None])[0]
            try:
                self._send(200, C.reports_overview(date))
            except Exception as e:
                self._send(500, {"error": str(e)})
        elif path == "/api/chat/meta":
            if not self._token_ok():
                return self._send(403, {"error": "bad token"})
            with LOCK:
                self._send(200, {
                    "models": C.candidate_models(STATE["data"], C.DEFAULTS_KEY),
                    "default": C.chat_default_model(STATE["data"]),
                })
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._token_ok():
            return self._send(403, {"error": "bad token"})
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body or "{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        path = self.path.split("?", 1)[0]

        if path == "/api/chat":
            # Streamed text response: write deltas straight to the socket.
            data = STATE["data"]
            try:
                gen = C.chat_stream(payload.get("model"), payload.get("messages", []), data)
                first = next(gen)  # triggers lazy setup; raises before headers on failure
            except StopIteration:
                first = ""
            except Exception as e:
                return self._send(400, {"ok": False, "error": str(e)})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                if first:
                    self.wfile.write(first.encode("utf-8")); self.wfile.flush()
                for chunk in gen:
                    self.wfile.write(chunk.encode("utf-8")); self.wfile.flush()
            except Exception:
                pass
            return

        try:
            if path == "/api/op":
                with LOCK:
                    extra = do_action(payload["action"], payload)
                    snap = C.snapshot(STATE["data"])
                    snap["dirty"] = STATE["dirty"]
                    snap.update(extra)
                self._send(200, {"ok": True, **snap})
            elif path == "/api/reports/probes":
                self._send(200, {"probes": C.health_probes()})
            elif path == "/api/reports/regen":
                date, p, proc = C.regenerate_token_report(payload.get("date"))
                ok = proc.returncode == 0 and p.exists()
                self._send(200, {"ok": ok, "date": date,
                                 "error": "" if ok else (proc.stderr or "tracker produced no output")[:400],
                                 **C.reports_overview(date)})
            elif path == "/api/reports/combined":
                p = C.generate_observability(payload.get("date"))
                self._send(200, {"ok": True, "path": str(p)})
            elif path == "/api/reports/schedule":
                proc = C.run_schedule(payload.get("time", "07:30"))
                self._send(200, {"ok": proc.returncode == 0,
                                 "output": (proc.stdout or "") + (proc.stderr or "")})
            elif path == "/api/save":
                with LOCK:
                    backup = C.save_config(STATE["data"])
                    proc = C.cold_restart()
                    STATE["dirty"] = False
                self._send(200, {
                    "ok": proc.returncode == 0,
                    "returncode": proc.returncode,
                    "output": (proc.stdout or "") + (proc.stderr or ""),
                    "backup": str(backup) if backup else None,
                })
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(400, {"ok": False, "error": f"{type(e).__name__}: {e}"})


# --------------------------------------------------------------------------- #
# HTML (single page; vanilla JS; reads token from its own URL)
# --------------------------------------------------------------------------- #
HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw Console</title>
<style>
  :root{--bg:#0f1115;--panel:#171a21;--line:#262b36;--fg:#e6e9ef;--mut:#8b93a7;
        --acc:#5b9dff;--ok:#3fb950;--warn:#f0883e;--danger:#f85149;}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
       background:var(--bg);color:var(--fg)}
  header{display:flex;align-items:center;gap:14px;padding:12px 18px;
         border-bottom:1px solid var(--line);background:var(--panel)}
  header h1{font-size:15px;margin:0;font-weight:600}
  .tabs{display:flex;gap:4px;margin-left:8px}
  .tab{padding:6px 12px;border-radius:7px;cursor:pointer;color:var(--mut)}
  .tab.active{background:var(--bg);color:var(--fg)}
  .tab.stub{opacity:.5}
  .spacer{flex:1}
  #saveBtn{padding:7px 14px;border:0;border-radius:7px;background:var(--acc);
           color:#fff;font-weight:600;cursor:pointer}
  #saveBtn:disabled{opacity:.4;cursor:default}
  #status{color:var(--mut);font-size:12px;min-width:120px}
  main{padding:18px;max-width:1100px;margin:0 auto}
  .pane{display:none} .pane.active{display:block}
  .row{display:flex;gap:18px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:10px;
        padding:14px 16px;margin-bottom:14px}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 10px}
  select,input[type=text]{background:var(--bg);border:1px solid var(--line);color:var(--fg);
        border-radius:7px;padding:7px 9px;font:inherit;width:100%}
  button.mini{background:var(--bg);border:1px solid var(--line);color:var(--fg);
        border-radius:6px;padding:4px 9px;cursor:pointer;font:inherit}
  button.mini:hover{border-color:var(--acc)}
  .fb{display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid var(--line);
      border-radius:7px;margin-bottom:6px;background:var(--bg)}
  .fb .idx{color:var(--mut);width:18px;text-align:right}
  .fb .name{flex:1;font-family:ui-monospace,monospace;font-size:13px}
  .fb .alias{color:var(--acc);font-size:12px}
  .agentlist .item{padding:8px 10px;border-radius:7px;cursor:pointer;color:var(--fg)}
  .agentlist .item:hover{background:var(--bg)}
  .agentlist .item.sel{background:var(--bg);outline:1px solid var(--acc)}
  .mut{color:var(--mut)} .mono{font-family:ui-monospace,monospace}
  table{width:100%;border-collapse:collapse} td,th{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line)}
  th{color:var(--mut);font-weight:500;font-size:12px}
  .pill{font-size:11px;padding:1px 7px;border-radius:10px;background:var(--bg);border:1px solid var(--line)}
  .danger{color:var(--danger);border-color:var(--danger)}
  dialog{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:12px;
         padding:18px;max-width:460px;width:90%}
  dialog::backdrop{background:rgba(0,0,0,.5)}
  .stubbox{text-align:center;color:var(--mut);padding:60px 20px}
  label{display:block;color:var(--mut);font-size:12px;margin:10px 0 4px}
</style></head>
<body>
<header>
  <h1>🐾 OpenClaw Console</h1>
  <div class="tabs" id="tabs">
    <div class="tab active" data-pane="models">🧠 Models</div>
    <div class="tab" data-pane="agents">👥 Agents</div>
    <div class="tab" data-pane="reports">📊 Reports</div>
    <div class="tab" data-pane="terminal">💬 Terminal</div>
  </div>
  <div class="spacer"></div>
  <span id="status"></span>
  <button id="saveBtn" disabled>Save &amp; Restart</button>
</header>
<main>
  <section class="pane active" id="pane-models">
    <div class="row">
      <div class="card" style="width:240px;flex:none">
        <h2>Agents</h2><div class="agentlist" id="mAgents"></div>
      </div>
      <div class="card" style="flex:1" id="mDetail"><span class="mut">Select an agent…</span></div>
    </div>
  </section>
  <section class="pane" id="pane-agents">
    <div class="card">
      <h2>Agents</h2><table id="aTable"></table>
      <div style="margin-top:12px"><button class="mini" id="createBtn">➕ Create agent</button></div>
    </div>
  </section>
  <section class="pane" id="pane-reports">
    <div class="card">
      <h2>Daily token usage</h2>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
        <select id="repDate" style="width:auto"></select>
        <button class="mini" id="regenBtn">Regenerate</button>
        <button class="mini" id="combinedBtn">Generate combined report</button>
        <button class="mini" id="scheduleBtn">Schedule daily…</button>
        <span class="spacer" style="flex:1"></span>
        <span id="repMsg" class="mut"></span>
      </div>
      <div id="repSummary"></div>
    </div>
    <div class="row">
      <div class="card" style="flex:1"><h2>By model</h2><table id="repModels"></table></div>
      <div class="card" style="flex:1"><h2>By agent</h2><table id="repAgents"></table></div>
    </div>
    <div class="card">
      <h2>Live health probes</h2>
      <button class="mini" id="probeBtn">Run probes</button>
      <table id="probeTable" style="margin-top:10px"></table>
    </div>
    <div class="row">
      <div class="card" style="flex:1"><h2>Recent restarts</h2><div id="repRestarts" class="mono" style="font-size:12px"></div></div>
      <div class="card" style="flex:1"><h2>Recent errors</h2><div id="repErrors" class="mono" style="font-size:12px"></div></div>
    </div>
  </section>
  <section class="pane" id="pane-terminal">
    <div class="card" style="display:flex;flex-direction:column;height:70vh">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
        <h2 style="margin:0">Gateway config assistant</h2>
        <span class="spacer" style="flex:1"></span>
        <span class="mut" style="font-size:12px">model</span>
        <select id="chatModel" style="width:auto"></select>
        <button class="mini" id="chatClear">Clear</button>
      </div>
      <div id="chatLog" style="flex:1;overflow:auto;padding:6px;border:1px solid var(--line);border-radius:8px;background:var(--bg)"></div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <input id="chatInput" type="text" placeholder="Ask how to configure a gateway… (low-volume helper)">
        <button class="mini" id="chatSend">Send</button>
      </div>
      <div class="mut" style="font-size:11px;margin-top:6px">
        Direct LLM call via your OpenRouter key; sees a summary of your live config. Replies are advisory — apply changes in the Models/Agents tabs.
      </div>
    </div>
  </section>
</main>
<dialog id="dlg"></dialog>
<script>
const T = new URLSearchParams(location.search).get("t") || "";
const H = {"X-Token":T,"Content-Type":"application/json"};
let S = null, sel = "__defaults__";

const $ = s => document.querySelector(s);
async function getConfig(){ S = await (await fetch("/api/config",{headers:H})).json(); render(); }
async function op(payload){
  const r = await fetch("/api/op",{method:"POST",headers:H,body:JSON.stringify(payload)});
  const j = await r.json();
  if(!j.ok){ alert("Error: "+(j.error||"unknown")); return null; }
  S = j; render(); return j;
}
function setStatus(t){ $("#status").textContent = t||""; }
function markDirty(){ $("#saveBtn").disabled = !S.dirty; setStatus(S.dirty?"unsaved changes":""); }

function agentByKey(k){ return S.agents.find(a=>a.key===k); }

function render(){
  // Models: agent list
  $("#mAgents").innerHTML = S.agents.map(a=>
    `<div class="item ${a.key===sel?'sel':''}" data-k="${a.key}">${a.is_defaults?'⚙ defaults':'⚙ '+a.id}</div>`).join("");
  $("#mAgents").querySelectorAll(".item").forEach(el=>el.onclick=()=>{sel=el.dataset.k;render();});
  renderDetail();
  // Agents table
  $("#aTable").innerHTML =
    "<tr><th>id</th><th>name</th><th>primary</th><th>fallbacks</th><th>bindings</th><th></th></tr>"+
    S.agents.filter(a=>!a.is_defaults).map(a=>
      `<tr><td class="mono">${a.id}</td><td>${esc(a.name)}</td>
       <td class="mono mut">${esc(a.primary||'—')}</td><td>${a.fallbacks.length}</td>
       <td>${a.bindings?('<span class="pill">'+a.bindings+'</span>'):'—'}</td>
       <td style="text-align:right">
         <button class="mini" onclick="editAgent('${a.key}')">Edit</button>
         <button class="mini" onclick="renameAgent('${a.key}')">Rename</button>
         <button class="mini danger" onclick="removeAgent('${a.key}')">Remove</button></td></tr>`).join("");
  markDirty();
}

function optList(cur){
  const opts = S.candidates.map(m=>`<option value="${m}" ${m===cur?'selected':''}>${m}</option>`).join("");
  return `<option value="">— pick —</option>${opts}<option value="__custom__">✎ custom…</option>`;
}

function renderDetail(){
  const a = agentByKey(sel); if(!a){ $("#mDetail").innerHTML="<span class=mut>Select an agent…</span>"; return; }
  const fbs = a.fallbacks.map((m,i)=>`
    <div class="fb"><span class="idx">${i+1}</span><span class="name">${m}</span>
      <span class="alias">${a.aliases[m]?('· '+esc(a.aliases[m])):''}</span>
      <button class="mini" onclick="moveFb('${a.key}',${i},-1)" ${i===0?'disabled':''}>↑</button>
      <button class="mini" onclick="moveFb('${a.key}',${i},1)" ${i===a.fallbacks.length-1?'disabled':''}>↓</button>
      <button class="mini danger" onclick="rmFb('${a.key}',${i})">✕</button></div>`).join("") || "<span class=mut>(none)</span>";
  $("#mDetail").innerHTML = `
    <h2>${a.is_defaults?'Defaults (global)':esc(a.name)+' · '+a.id}</h2>
    <label>Primary model</label>
    <div style="display:flex;gap:8px">
      <select id="primSel">${optList(a.primary)}</select>
      <button class="mini" onclick="changePrimary('${a.key}')">Set</button></div>
    <label style="margin-top:16px">Fallbacks (tried in order)</label>
    ${fbs}
    <div style="display:flex;gap:8px;margin-top:10px">
      <select id="addSel">${optList("")}</select>
      <button class="mini" onclick="addFb('${a.key}')">Add fallback</button></div>`;
}

function pickModel(selId){
  let v = $("#"+selId).value;
  if(v==="__custom__"){ v = prompt("Full model id:")||""; v=v.trim(); }
  return v;
}
async function maybeAlias(key, model){
  const a = agentByKey(key);
  if(model && !a.aliases[model]){
    const al = (prompt("Optional nickname for "+model+" (blank = none):")||"").trim();
    if(al) await op({action:"set_alias",key,model_id:model,alias:al});
  }
}
async function changePrimary(key){
  const m = pickModel("primSel"); if(!m) return;
  await op({action:"set_primary",key,model_id:m});
  await maybeAlias(key,m);
}
async function addFb(key){
  const m = pickModel("addSel"); if(!m) return;
  const a = agentByKey(key); if(a.fallbacks.includes(m)){ alert("Already a fallback."); return; }
  await op({action:"set_fallbacks",key,fallbacks:[...a.fallbacks,m]});
  await maybeAlias(key,m);
}
async function rmFb(key,i){
  const a=agentByKey(key); const f=[...a.fallbacks]; f.splice(i,1);
  await op({action:"set_fallbacks",key,fallbacks:f});
}
async function moveFb(key,i,d){
  const a=agentByKey(key); const f=[...a.fallbacks]; const j=i+d;
  [f[i],f[j]]=[f[j],f[i]];
  await op({action:"set_fallbacks",key,fallbacks:f});
}

function esc(s){ return (s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c])); }

// Agents tab dialogs
function dlg(html){ const d=$("#dlg"); d.innerHTML=html; d.showModal(); return d; }
function createBtn(){
  dlg(`<h2>Create agent</h2>
   <label>Agent id</label><input id="ni" type="text" placeholder="e.g. researcher">
   <label>Display name</label><input id="nn" type="text">
   <label>Workspace (blank = ~/.openclaw/workspace-&lt;id&gt;)</label><input id="nw" type="text">
   <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
     <button class="mini" onclick="dlg('').close()">Cancel</button>
     <button class="mini" onclick="doCreate()">Create</button></div>`);
}
async function doCreate(){
  const id=$("#ni").value.trim(); if(!id) return;
  const j=await op({action:"create_agent",id,name:$("#nn").value.trim(),workspace:$("#nw").value.trim()});
  if(j){ $("#dlg").close(); }
}
function editAgent(key){
  const a=agentByKey(key);
  dlg(`<h2>Edit ${esc(a.id)}</h2>
   <label>Display name</label><input id="en" type="text" value="${esc(a.name)}">
   <label>Workspace</label><input id="ew" type="text" value="${esc(a.workspace)}">
   <div style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">
     <button class="mini" onclick="$('#dlg').close()">Cancel</button>
     <button class="mini" onclick="doEdit('${key}')">Save</button></div>`);
}
async function doEdit(key){
  await op({action:"edit_agent",key,name:$("#en").value.trim(),workspace:$("#ew").value.trim()});
  $("#dlg").close();
}
async function renameAgent(key){
  const a=agentByKey(key);
  const nid=(prompt("New id for '"+a.id+"':",a.id)||"").trim();
  if(!nid||nid===a.id) return;
  const j=await op({action:"rename_agent",key,new_id:nid});
  if(j&&j.repointed_bindings) setStatus(j.repointed_bindings+" binding(s) repointed");
}
async function removeAgent(key){
  const a=agentByKey(key);
  let msg="Remove agent '"+a.id+"'?";
  if(a.bindings) msg+="\n\n⚠️ "+a.bindings+" binding(s) will also be removed.";
  if(!confirm(msg)) return;
  const j=await op({action:"remove_agent",key});
  if(j&&j.removed_bindings) setStatus("removed "+j.removed_bindings+" binding(s)");
  if(sel===key) sel="__defaults__";
}

// Reports
let repData=null;
async function loadReports(date){
  const qs = date?("?date="+encodeURIComponent(date)):"";
  repData = await (await fetch("/api/reports"+qs,{headers:H})).json();
  renderReports();
}
function fmtN(n){ return (n==null)?"—":Number(n).toLocaleString(); }
function renderReports(){
  const r=repData; if(!r) return;
  $("#repDate").innerHTML = (r.dates||[]).map(d=>`<option ${d===r.date?'selected':''}>${d}</option>`).join("")
    || "<option>(no reports yet)</option>";
  const g=(r.token&&r.token.summary)||{};
  $("#repSummary").innerHTML = Object.keys(g).length ? `
    <div style="display:flex;gap:24px;flex-wrap:wrap">
      <div><div class="mut">Est. cost</div><div style="font-size:22px">$${(g.cost??0).toFixed(4)}</div></div>
      <div><div class="mut">Turns</div><div style="font-size:22px">${fmtN(g.turns)}</div></div>
      <div><div class="mut">Total tokens</div><div style="font-size:22px">${fmtN(g.total)}</div></div>
      <div><div class="mut">In / Out</div><div style="font-size:22px">${fmtN(g.input)} / ${fmtN(g.output)}</div></div>
      <div><div class="mut">Cache / Reasoning</div><div style="font-size:22px">${fmtN(g.cache)} / ${fmtN(g.reasoning)}</div></div>
    </div>` : '<span class="mut">No token report for this date. Click Regenerate.</span>';
  const ms=(r.token&&r.token.models)||[];
  $("#repModels").innerHTML="<tr><th>model</th><th>turns</th><th>tokens</th><th>cost</th></tr>"+
    ms.map(m=>`<tr><td class="mono">${esc(m.id)}</td><td>${m.turns}</td><td>${fmtN(m.tokens)}</td><td>$${m.cost.toFixed(4)}</td></tr>`).join("");
  const ag=(r.token&&r.token.agents)||[];
  $("#repAgents").innerHTML="<tr><th>agent</th><th>turns</th><th>tokens</th><th>cost</th></tr>"+
    ag.map(a=>`<tr><td>${esc(a.name)}</td><td>${a.turns}</td><td>${fmtN(a.tokens)}</td><td>$${a.cost.toFixed(4)}</td></tr>`).join("");
  $("#repRestarts").innerHTML=(r.logs.restarts||[]).map(esc).join("<br>")||'<span class="mut">none</span>';
  $("#repErrors").innerHTML=(r.logs.errors||[]).map(esc).join("<br>")||'<span class="mut">none</span>';
}
function repMsg(t){ $("#repMsg").textContent=t||""; }
document.addEventListener("change",e=>{ if(e.target.id==="repDate") loadReports(e.target.value); });
$("#regenBtn").onclick=async()=>{
  repMsg("regenerating…");
  const r=await(await fetch("/api/reports/regen",{method:"POST",headers:H,body:JSON.stringify({date:$("#repDate").value})})).json();
  repData=r; renderReports(); repMsg(r.ok?"regenerated":("error: "+(r.error||"")));
};
$("#combinedBtn").onclick=async()=>{
  repMsg("building combined report…");
  const r=await(await fetch("/api/reports/combined",{method:"POST",headers:H,body:JSON.stringify({date:$("#repDate").value})})).json();
  repMsg(r.ok?("wrote "+r.path.split("/").pop()):"error");
};
$("#scheduleBtn").onclick=async()=>{
  const t=(prompt("Daily run time (HH:MM, 24h):","07:30")||"").trim(); if(!t) return;
  repMsg("scheduling…");
  const r=await(await fetch("/api/reports/schedule",{method:"POST",headers:H,body:JSON.stringify({time:t})})).json();
  repMsg(r.ok?"scheduled daily at "+t:"schedule failed");
  if(!r.ok) alert((r.output||"").slice(-400));
};
$("#probeBtn").onclick=async()=>{
  $("#probeTable").innerHTML='<tr><td class="mut">probing…</td></tr>';
  const r=await(await fetch("/api/reports/probes",{method:"POST",headers:H,body:"{}"})).json();
  $("#probeTable").innerHTML="<tr><th>service</th><th>status</th><th>code</th><th>hint</th></tr>"+
    r.probes.map(p=>`<tr><td>${esc(p.name)}</td>
      <td style="color:${p.ok?'var(--ok)':'var(--danger)'}">${p.ok?'PASS':'FAIL'}</td>
      <td class="mono">${p.code}</td><td class="mut">${esc(p.hint)}</td></tr>`).join("");
};

// Terminal (gateway config assistant)
let chatHist=[], chatMeta=null, chatBusy=false;
async function loadChatMeta(){
  chatMeta = await (await fetch("/api/chat/meta",{headers:H})).json();
  $("#chatModel").innerHTML = chatMeta.models.map(m=>
    `<option ${m===chatMeta.default?'selected':''}>${m}</option>`).join("");
}
function addBubble(role,text){
  const who = role==="user"?"you":"assistant";
  const color = role==="user"?"var(--acc)":"var(--ok)";
  const el=document.createElement("div");
  el.style.cssText="margin:8px 0";
  el.innerHTML=`<div style="color:${color};font-size:11px;text-transform:uppercase;letter-spacing:.05em">${who}</div>`+
    `<div class="bubbletext" style="white-space:pre-wrap"></div>`;
  el.querySelector(".bubbletext").textContent=text;
  $("#chatLog").appendChild(el); $("#chatLog").scrollTop=$("#chatLog").scrollHeight;
  return el.querySelector(".bubbletext");
}
async function chatSend(){
  if(chatBusy) return;
  const inp=$("#chatInput"); const text=inp.value.trim(); if(!text) return;
  inp.value=""; chatBusy=true; $("#chatSend").disabled=true;
  addBubble("user",text); chatHist.push({role:"user",content:text});
  const out=addBubble("assistant","…");
  try{
    const r=await fetch("/api/chat",{method:"POST",headers:H,
      body:JSON.stringify({model:$("#chatModel").value,messages:chatHist})});
    if(!r.ok){ const j=await r.json().catch(()=>({error:"error"})); out.textContent="⚠️ "+(j.error||r.status); chatBusy=false; $("#chatSend").disabled=false; return; }
    const reader=r.body.getReader(), dec=new TextDecoder(); let acc="";
    out.textContent="";
    while(true){ const {done,value}=await reader.read(); if(done) break;
      acc+=dec.decode(value,{stream:true}); out.textContent=acc; $("#chatLog").scrollTop=$("#chatLog").scrollHeight; }
    chatHist.push({role:"assistant",content:acc});
  }catch(e){ out.textContent="⚠️ "+e; }
  chatBusy=false; $("#chatSend").disabled=false; inp.focus();
}
$("#chatSend").onclick=chatSend;
$("#chatInput").addEventListener("keydown",e=>{ if(e.key==="Enter") chatSend(); });
$("#chatClear").onclick=()=>{ chatHist=[]; $("#chatLog").innerHTML=""; };

// Tabs
$("#tabs").querySelectorAll(".tab").forEach(t=>t.onclick=()=>{
  $("#tabs .tab.active").classList.remove("active");
  document.querySelector(".pane.active").classList.remove("active");
  t.classList.add("active"); $("#pane-"+t.dataset.pane).classList.add("active");
  if(t.dataset.pane==="reports" && !repData) loadReports();
  if(t.dataset.pane==="terminal" && !chatMeta) loadChatMeta();
});
$("#createBtn").onclick = createBtn;
$("#saveBtn").onclick = async ()=>{
  if(!confirm("Save to openclaw-template.json and run `make cold-restart`?")) return;
  setStatus("saving + restarting…"); $("#saveBtn").disabled=true;
  const r=await fetch("/api/save",{method:"POST",headers:H,body:"{}"});
  const j=await r.json();
  await getConfig();
  setStatus(j.ok?"saved · gateway restarted":"saved, but restart reported an error");
  if(!j.ok) alert((j.output||"").split("\n").slice(-12).join("\n"));
};
getConfig();
</script>
</body></html>"""


def main():
    STATE["data"] = C.load_config()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = httpd.server_address[1]
    url = f"http://127.0.0.1:{port}/?t={TOKEN}"
    print(f"OpenClaw Console running at {url}")
    print("Press Ctrl-C to stop.")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
