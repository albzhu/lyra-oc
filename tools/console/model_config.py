#!/usr/bin/env python3
"""
OpenClaw Model Configurator
===========================

A lightweight, no-dependency macOS GUI for changing which models each OpenClaw
agent uses (the "primary" model and its ordered "fallbacks").

Design notes
------------
* UI is built from native macOS dialogs via `osascript` (AppleScript). No app to
  build, no server to run -- just double-click the .command launcher.
* LISTING the current primary/fallbacks is done with `jq` (per request): the
  in-memory config is piped to jq over stdin, so what you see always reflects
  your pending edits.
* WRITING is done with a stage -> validate -> backup -> atomic-replace flow
  (mirrors the discord-add-channel skill's `modify_json.py`, hardened a bit).
* It edits `openclaw-template.json`, NOT `openclaw.json`. `make sync` (run by
  `make restart`) regenerates openclaw.json FROM the template and would clobber
  any direct edit to openclaw.json.
* On save it runs `make cold-restart` from ~/.openclaw inline and reports the
  result in a dialog. `cold-restart` is `sync down up` (no `logs --follow`), so
  it returns cleanly instead of hanging the way `make restart` would.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
OPENCLAW_DIR = Path(os.path.expanduser("~/.openclaw"))
TEMPLATE = OPENCLAW_DIR / "openclaw-template.json"
STAGED = OPENCLAW_DIR / "openclaw-template_staged.json"
BACKUP_DIR = OPENCLAW_DIR / "openclaw-backups"

APP_TITLE = "OpenClaw Model Config"


# --------------------------------------------------------------------------- #
# Tool discovery (jq) -- macOS doesn't ship jq, so look in the usual places
# --------------------------------------------------------------------------- #
def find_jq():
    candidates = [
        shutil.which("jq"),
        "/opt/homebrew/bin/jq",   # Apple Silicon Homebrew
        "/usr/local/bin/jq",      # Intel Homebrew
        "/usr/bin/jq",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return None


JQ = find_jq()


class UserCancelled(Exception):
    """Raised when the user hits Cancel in any dialog."""


# --------------------------------------------------------------------------- #
# AppleScript / osascript helpers
# --------------------------------------------------------------------------- #
def _osa(script: str) -> str:
    """Run an AppleScript snippet, returning trimmed stdout.

    osascript exits non-zero when the user cancels; we translate that into
    UserCancelled so callers can unwind cleanly.
    """
    proc = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").lower()
        if "cancel" in err or "-128" in err:
            raise UserCancelled()
        # Surface unexpected AppleScript errors instead of failing silently.
        raise RuntimeError(proc.stderr.strip() or "osascript error")
    return proc.stdout.strip()


def _q(s: str) -> str:
    """Quote a Python string for embedding in AppleScript double-quotes."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def alert(message: str, buttons=("OK",), default=None, title=APP_TITLE):
    btns = ", ".join(f'"{_q(b)}"' for b in buttons)
    default = default or buttons[-1]
    script = (
        f'display dialog "{_q(message)}" with title "{_q(title)}" '
        f'buttons {{{btns}}} default button "{_q(default)}"'
    )
    out = _osa(script)
    # out looks like: button returned:OK
    return out.split("button returned:", 1)[-1].strip()


def confirm(message: str, ok="OK") -> bool:
    try:
        choice = alert(message, buttons=("Cancel", ok), default=ok)
        return choice == ok
    except UserCancelled:
        return False


def choose_one(prompt: str, items, title=APP_TITLE, ok="Select"):
    """`choose from list` returning the chosen item (or raise UserCancelled)."""
    if not items:
        raise RuntimeError("nothing to choose from")
    quoted = ", ".join(f'"{_q(i)}"' for i in items)
    script = (
        f'set theList to {{{quoted}}}\n'
        f'set theChoice to choose from list theList '
        f'with title "{_q(title)}" with prompt "{_q(prompt)}" '
        f'OK button name "{_q(ok)}" cancel button name "Cancel"\n'
        f'if theChoice is false then return "__CANCEL__"\n'
        f'return item 1 of theChoice'
    )
    out = _osa(script)
    if out == "__CANCEL__":
        raise UserCancelled()
    return out


def text_input(prompt: str, default: str = "", title=APP_TITLE) -> str:
    script = (
        f'set r to display dialog "{_q(prompt)}" with title "{_q(title)}" '
        f'default answer "{_q(default)}" buttons {{"Cancel", "OK"}} '
        f'default button "OK"\n'
        f'return text returned of r'
    )
    return _osa(script)


# --------------------------------------------------------------------------- #
# Config load / inspect (jq does the listing)
# --------------------------------------------------------------------------- #
def load_config() -> dict:
    if not TEMPLATE.exists():
        alert(f"Template not found:\n{TEMPLATE}", title=APP_TITLE)
        sys.exit(1)
    try:
        return json.loads(TEMPLATE.read_text())
    except json.JSONDecodeError as e:
        alert(f"openclaw-template.json is not valid JSON:\n{e}", title=APP_TITLE)
        sys.exit(1)


def agent_targets(data: dict):
    """Return ordered list of (label, jq_base_filter) for every editable agent."""
    targets = [("defaults (global)", ".agents.defaults")]
    for entry in data.get("agents", {}).get("list", []):
        aid = entry.get("id")
        if aid:
            targets.append((aid, f'(.agents.list[] | select(.id=="{aid}"))'))
    return targets


def jq_get(data: dict, filt: str):
    """Run a jq filter against in-memory `data` (piped over stdin)."""
    if not JQ:
        raise RuntimeError("jq not found")
    proc = subprocess.run(
        [JQ, "-r", filt],
        input=json.dumps(data),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip())
    return proc.stdout


def get_primary(data, base):
    return jq_get(data, f"{base}.model.primary // empty").strip()


def get_fallbacks(data, base):
    out = jq_get(data, f"{base}.model.fallbacks[]? // empty")
    return [line for line in out.splitlines() if line.strip()]


def alias_map(data, base):
    """alias lookup: model-id -> alias, from this agent's `models` block."""
    out = jq_get(
        data,
        f'{base}.models // {{}} | to_entries[] | "\\(.key)\\t\\(.value.alias // "")"',
    )
    m = {}
    for line in out.splitlines():
        if "\t" in line:
            k, a = line.split("\t", 1)
            m[k] = a
    return m


def candidate_models(data, base):
    """Union of every model id seen anywhere, so the user can pick freely."""
    seen = set()
    # this agent's declared pool first (keeps friendly aliases handy)
    for k in alias_map(data, base):
        seen.add(k)
    # plus everything referenced across the whole config
    everywhere = jq_get(
        data,
        '[.. | objects | (.model? // empty) | (.primary?, .fallbacks?)] '
        '| flatten | map(select(. != null)) | unique[]',
    )
    for line in everywhere.splitlines():
        if line.strip():
            seen.add(line.strip())
    return sorted(seen)


def fmt(model_id, aliases):
    a = aliases.get(model_id)
    return f"{model_id}   ·   {a}" if a else model_id


def parse_fmt(display):
    return display.split("   ·   ", 1)[0].strip()


# --------------------------------------------------------------------------- #
# Save: stage -> validate -> backup -> atomic replace
# --------------------------------------------------------------------------- #
def save_config(data: dict):
    new_text = json.dumps(data, indent=2) + "\n"

    # 1. stage to a sibling temp file (same filesystem -> atomic rename later)
    STAGED.write_text(new_text)

    # 2. validate the staged file really parses
    json.loads(STAGED.read_text())

    # 3. back up the current template before we touch it
    if TEMPLATE.exists():
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        shutil.copy2(TEMPLATE, BACKUP_DIR / f"openclaw-template.json.uiedit.{ts}")

    # 4. atomic replace
    os.replace(STAGED, TEMPLATE)


def check_gateway_running():
    """Helper to check if openclaw gateway is currently running."""
    env = dict(os.environ)
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    try:
        proc_status = subprocess.run(
            ["openclaw", "gateway", "status", "--json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc_status.returncode == 0:
            status_data = json.loads(proc_status.stdout)
            state = status_data.get("service", {}).get("runtime", {}).get("status", "")
            return state == "running"
    except Exception:
        pass
    return False


def run_make_restart(gateway_is_running=None):
    """Run `make cold-restart` or launch the gateway if not running.

    `cold-restart` is `sync down up` (no `logs --follow`), so it returns cleanly
    and we can run it inline and report the result -- no Terminal window needed.
    Returns the completed subprocess so the caller can inspect success/failure.
    """
    env = dict(os.environ)
    # Ensure the openclaw binary (Homebrew / npm global) is on PATH even when
    # launched straight from Finder, where the inherited PATH is minimal.
    env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")

    if gateway_is_running is None:
        gateway_is_running = check_gateway_running()

    target_action = "cold-restart" if gateway_is_running else "cold-restart" # wait, cold-restart does sync down up
    # If not running, cold-restart works fine too (sync down up will make sure down is safe and up runs cleanly),
    # but let's be deliberate about what make target/command is used.
    # Actually, cold-restart runs 'sync down up', which is safe to run even if the gateway is stopped
    # (since 'down' is tolerant of already stopped/not-loaded, although it prints an error sometimes).
    # If the user chooses "Save & Launch", we will just run sync down up (cold-restart) because it launches it!
    # Let's check how the caller handles it.
    
    return subprocess.run(
        ["make", "cold-restart"],
        cwd=str(OPENCLAW_DIR),
        env=env,
        capture_output=True,
        text=True,
    )


def restart_and_report(gateway_is_running=None):
    """Run the cold restart/launch and show a success/failure dialog."""
    if gateway_is_running is None:
        gateway_is_running = check_gateway_running()
    proc = run_make_restart(gateway_is_running)
    action_text = "restarted" if gateway_is_running else "launched"
    if proc.returncode == 0:
        alert(f"Saved. The gateway has been {action_text}.")
    else:
        tail = "\n".join(
            (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
        )
        alert(
            f"Saved, but starting/restarting the gateway reported a problem:\n\n"
            f"{tail or '(no output)'}\n\n"
            "Your previous template is backed up in openclaw-backups/."
        )


# --------------------------------------------------------------------------- #
# Editing flows
# --------------------------------------------------------------------------- #
def set_primary(data, base, label):
    aliases = alias_map(data, base)
    current = get_primary(data, base)
    options = [fmt(m, aliases) for m in candidate_models(data, base)]
    options.append("✎  Enter a custom model id…")
    pick = choose_one(
        f"[{label}] Primary model\nCurrent: {current or '(none)'}",
        options,
    )
    custom = pick.startswith("✎")
    if custom:
        new_id = text_input("Enter the full model id:", current).strip()
        if not new_id:
            return False
    else:
        new_id = parse_fmt(pick)
    if new_id == current:
        return False
    _set_in_data(data, base, "primary", new_id)
    # Offer to add a nickname only if this model doesn't already have one.
    if not aliases.get(new_id):
        alias = prompt_alias(new_id)
        if alias:
            set_alias(data, base, new_id, alias)
    return True


def edit_fallbacks(data, base, label):
    """Sub-menu loop for the ordered fallback list."""
    changed = False
    while True:
        aliases = alias_map(data, base)
        fbs = get_fallbacks(data, base)
        shown = "\n".join(f"{i+1}. {fmt(m, aliases)}" for i, m in enumerate(fbs)) or "(empty)"
        action = alert(
            f"[{label}] Fallbacks (tried in order):\n\n{shown}",
            buttons=("Back", "Remove / Reorder", "Add"),
            default="Add",
        )
        if action == "Back":
            return changed
        if action == "Add":
            opts = [fmt(m, aliases) for m in candidate_models(data, base) if m not in fbs]
            opts.append("✎  Enter a custom model id…")
            try:
                pick = choose_one(f"[{label}] Add fallback", opts)
                if pick.startswith("✎"):
                    new_id = text_input("Enter the full model id:").strip()
                else:
                    new_id = parse_fmt(pick)
            except UserCancelled:
                continue
            if not new_id or new_id in fbs:
                continue
            fbs.append(new_id)
            _set_in_data(data, base, "fallbacks", fbs)
            # Offer to add a nickname only if this model doesn't have one yet.
            if not aliases.get(new_id):
                alias = prompt_alias(new_id)
                if alias:
                    set_alias(data, base, new_id, alias)
            changed = True
        elif action == "Remove / Reorder":
            if not fbs:
                continue
            try:
                target = parse_fmt(
                    choose_one(f"[{label}] Pick a fallback to act on",
                               [fmt(m, aliases) for m in fbs])
                )
                op = alert(
                    f"What to do with:\n{target}",
                    buttons=("Move to position…", "Remove", "Cancel"),
                    default="Move to position…",
                )
            except UserCancelled:
                continue
            if op == "Cancel":
                continue
            idx = fbs.index(target)
            if op == "Remove":
                fbs.pop(idx)
            else:  # Move to a chosen position; the rest shift to make room.
                try:
                    dest = choose_position(label, fbs, idx, aliases)
                except UserCancelled:
                    continue
                if dest is None:
                    continue
                moving = fbs.pop(idx)
                fbs.insert(min(dest, len(fbs)), moving)
            _set_in_data(data, base, "fallbacks", fbs)
            changed = True


def _agent_node(data, base):
    """Return the dict for `defaults` or the matching `list` entry (or None)."""
    if base == ".agents.defaults":
        return data["agents"]["defaults"]
    # base looks like (.agents.list[] | select(.id=="X"))
    aid = base.split('id=="', 1)[1].split('"', 1)[0]
    for entry in data["agents"]["list"]:
        if entry.get("id") == aid:
            return entry
    return None


def _set_in_data(data, base, field, value):
    """Write a model field (primary/fallbacks) into the agent's `model` block."""
    node = _agent_node(data, base)
    if node is not None:
        node.setdefault("model", {})[field] = value


def set_alias(data, base, model_id, alias):
    """Set models[model_id].alias for the agent. No-op for an empty alias."""
    if not alias:
        return
    node = _agent_node(data, base)
    if node is None:
        return
    node.setdefault("models", {}).setdefault(model_id, {})["alias"] = alias


def prompt_alias(model_id, current_alias=""):
    """Optional alias entry. Returns the (possibly empty) alias, or None if cancelled."""
    try:
        return text_input(
            f"Optional alias for:\n{model_id}\n\n"
            "A short nickname like 'sonnet'. Leave blank for none.",
            current_alias,
        ).strip()
    except UserCancelled:
        return None


def choose_position(label, fbs, moving_idx, aliases):
    """Ask which final 1-based slot the moving item should occupy.

    Returns a 0-based insertion index for the list AFTER the item is removed,
    or None if the user kept it in place. Raises UserCancelled on Cancel.
    """
    numbered = "\n".join(
        f"{i+1}. {fmt(m, aliases)}" + ("   ← moving" if i == moving_idx else "")
        for i, m in enumerate(fbs)
    )
    labels = [f"Position {p}" for p in range(1, len(fbs) + 1)]
    pick = choose_one(
        f"[{label}] Insert at which position? "
        f"(others shift to make room)\n\n{numbered}",
        labels,
        ok="Move",
    )
    new_pos = int(pick.split()[1]) - 1   # 1-based label -> 0-based final slot
    if new_pos == moving_idx:
        return None
    return new_pos


def model_submenu(data, base, label):
    """Primary + fallbacks editor for one agent. Returns True if anything changed."""
    changed = False
    while True:
        primary = get_primary(data, base)
        fbs = get_fallbacks(data, base)
        summary = (
            f"Agent: {label}\n\n"
            f"Primary:\n  {primary or '(none)'}\n\n"
            "Fallbacks:\n  " + ("\n  ".join(fbs) if fbs else "(none)")
        )
        try:
            action = alert(
                summary,
                buttons=("Back", "Edit Fallbacks", "Change Primary"),
                default="Change Primary",
            )
            if action == "Back":
                return changed
            if action == "Change Primary":
                if set_primary(data, base, label):
                    changed = True
            elif action == "Edit Fallbacks":
                if edit_fallbacks(data, base, label):
                    changed = True
        except UserCancelled:
            # A cancel inside a sub-dialog returns to this menu, never quits.
            return changed


# --------------------------------------------------------------------------- #
# Agent management (create / edit / remove / rename)
# --------------------------------------------------------------------------- #
ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Starter persona files written into a new agent's workspace. Placeholders
# «NAME» and «ID» are substituted per agent. Mirrors the existing workspaces
# (IDENTITY / SOUL / AGENTS / TOOLS / USER / HEARTBEAT).
PERSONA_TEMPLATES = {
    "IDENTITY.md": """# IDENTITY.md — Who Am I?

- **Name:** «NAME»
- **Agent id:** «ID»
- **Creature:** _(describe this agent's archetype)_
- **Vibe:** _(tone and personality in one line)_
- **Emoji:**

---

_Write a short paragraph on what «NAME» is for and how it fits alongside the
other agents in this gateway._
""",
    "SOUL.md": """# SOUL.md — «NAME»

_You are «NAME». Describe your purpose and the principles you operate by._

## Core Truths

- **Be genuinely helpful** — skip filler, get to the point.
- **Be resourceful before asking** — try first, ask when truly blocked.
- **Be cost-conscious** — without compromising safety or quality.
- **STOP & notify immediately if any credentials, API keys, or tokens are
  discovered.** Safety overrides completion.
""",
    "AGENTS.md": """# AGENTS.md — «NAME»

_Define this agent's job: what it does, what it must not do, and how it reports
results._

## Rules
- Be specific and evidence-based.
- State assumptions; flag uncertainty ("likely", "verify").
- End with a clear, one-line outcome.

## 🔁 Callback Protocol — Don't Block, Yield
When you delegate work you'll need to act on, don't sit and wait — package a
callback, dispatch, and end your turn. Full spec: `Skills/miab-broker/CALLBACKS.md` (or `~/.openclaw/workspace/Skills/miab-broker/CALLBACKS.md`).

CLI: `python3 Skills/miab-broker/scripts/bin/claw-callback.py <cmd>`
""",
    "TOOLS.md": """- Skills default dir is ~/.openclaw/workspace/skills. Investigate any skill
  listed outside it.
- This is the **«ID»** agent workspace. Operate on the target repo/files passed
  to you, not on personal files here.
""",
    "USER.md": """# USER.md — About Your Human

_Learn about the person you're helping. Update this as you go._

- **Name:**
- **What to call them:**
- **Pronouns:** _(optional)_
- **Timezone:**
- **Notes:**

## Context

_(What do they care about? What are they working on? Build this over time.)_
""",
    "HEARTBEAT.md": """<!-- Heartbeat template; comments-only content prevents scheduled heartbeat API calls. -->

# Keep this file empty (or with only comments) to skip heartbeat API calls.
# Add tasks below when you want the agent to check something periodically.
""",
    ".gitignore": "*.DS_Store\n.env\n",
}


def scaffold_workspace(ws_path, agent_id, name):
    """Create the workspace dir and write any missing persona files.

    Never overwrites an existing file. Returns (created, skipped) filename lists.
    """
    ws = Path(ws_path).expanduser()
    ws.mkdir(parents=True, exist_ok=True)
    created, skipped = [], []
    for fname, template in PERSONA_TEMPLATES.items():
        dest = ws / fname
        if dest.exists():
            skipped.append(fname)
            continue
        text = template.replace("«NAME»", name).replace("«ID»", agent_id)
        dest.write_text(text)
        created.append(fname)
    return created, skipped


def _find_agent(data, aid):
    for entry in data.get("agents", {}).get("list", []):
        if entry.get("id") == aid:
            return entry
    return None


def bindings_for(data, aid):
    return [b for b in data.get("bindings", []) if b.get("agentId") == aid]


def _binding_line(b):
    m = b.get("match", {})
    peer = m.get("peer", {})
    return f"  • {m.get('channel', '?')}  {peer.get('kind', '?')}:{peer.get('id', '?')}"


def create_agent(data):
    existing = {a.get("id") for a in data["agents"]["list"]}
    try:
        new_id = text_input(
            "New agent id (letters, numbers, . _ - ):"
        ).strip()
        if not new_id:
            return False
        if not ID_RE.match(new_id):
            alert("Invalid id. Use only letters, numbers, dot, dash, underscore.")
            return False
        if new_id in existing:
            alert(f"An agent with id '{new_id}' already exists.")
            return False
        name = text_input("Display name (optional):", new_id).strip()
        # Default workspace follows the existing convention: workspace-<id>.
        default_ws = str(OPENCLAW_DIR / f"workspace-{new_id}")
        ws = text_input("Workspace path:", default_ws).strip() or default_ws
    except UserCancelled:
        return False

    # Minimal seed: defaults' primary, empty fallbacks, empty models.
    default_primary = (
        data["agents"]["defaults"].get("model", {}).get("primary", "")
    )
    data["agents"]["list"].append({
        "id": new_id,
        "model": {"primary": default_primary, "fallbacks": []},
        "models": {},
        "name": name or new_id,
        "workspace": ws,
    })

    # Scaffold the workspace folder with starter persona files.
    scaffold_note = ""
    try:
        created, skipped = scaffold_workspace(ws, new_id, name or new_id)
        if created:
            scaffold_note = (
                f"\n\nWorkspace: {ws}\nCreated: {', '.join(created)}"
            )
        if skipped:
            scaffold_note += f"\nKept existing: {', '.join(skipped)}"
    except Exception as e:  # never let a scaffold hiccup lose the agent entry
        scaffold_note = (
            f"\n\n⚠️ Could not write persona files to {ws}:\n{e}\n"
            "The agent entry was still created."
        )

    alert(
        f"Created agent '{new_id}'.\n\n"
        f"Primary: {default_primary or '(none)'}\n"
        "Fallbacks: (none)"
        f"{scaffold_note}\n\n"
        "Add fallbacks and aliases in the Models tab. The gateway provisions "
        "its data folder automatically on the next restart."
    )
    return True


def remove_agent(data, aid):
    affected = bindings_for(data, aid)
    msg = f"Remove agent '{aid}'?"
    if affected:
        lines = "\n".join(_binding_line(b) for b in affected)
        msg += (
            f"\n\n⚠️  This agent is the target of {len(affected)} binding(s), "
            "which will ALSO be removed:\n" + lines
        )
    if not confirm(msg, ok="Remove"):
        return False
    data["agents"]["list"] = [
        a for a in data["agents"]["list"] if a.get("id") != aid
    ]
    if affected:
        data["bindings"] = [
            b for b in data.get("bindings", []) if b.get("agentId") != aid
        ]
    alert(
        f"Removed agent '{aid}'"
        + (f" and {len(affected)} binding(s)." if affected else ".")
    )
    return True


def rename_agent_id(data, aid):
    existing = {a.get("id") for a in data["agents"]["list"]}
    try:
        new_id = text_input(f"New id for agent '{aid}':", aid).strip()
    except UserCancelled:
        return False
    if not new_id or new_id == aid:
        return False
    if not ID_RE.match(new_id):
        alert("Invalid id. Use only letters, numbers, dot, dash, underscore.")
        return False
    if new_id in existing:
        alert(f"An agent with id '{new_id}' already exists.")
        return False

    entry = _find_agent(data, aid)
    affected = bindings_for(data, aid)
    msg = f"Rename agent '{aid}'  →  '{new_id}'?"
    if affected:
        msg += f"\n\n{len(affected)} binding(s) will be repointed to '{new_id}'."
    if entry.get("agentDir") and f"/agents/{aid}/" in entry["agentDir"]:
        msg += "\n\nIts agentDir path will be updated to match the new id."
    if not confirm(msg, ok="Rename"):
        return False

    entry["id"] = new_id
    if entry.get("agentDir") and f"/agents/{aid}/" in entry["agentDir"]:
        entry["agentDir"] = entry["agentDir"].replace(
            f"/agents/{aid}/", f"/agents/{new_id}/"
        )
    for b in data.get("bindings", []):
        if b.get("agentId") == aid:
            b["agentId"] = new_id
    alert(f"Renamed to '{new_id}'.")
    return True


def edit_agent(data, aid):
    changed = False
    while True:
        entry = _find_agent(data, aid)
        if entry is None:
            return changed
        base = f'(.agents.list[] | select(.id=="{aid}"))'
        prim = get_primary(data, base) or "(none)"
        n_fb = len(get_fallbacks(data, base))
        n_bind = len(bindings_for(data, aid))
        info = (
            f"Editing agent '{aid}'\n"
            f"name:       {entry.get('name', '')}\n"
            f"workspace:  {entry.get('workspace', '')}\n"
            f"primary:    {prim}\n"
            f"fallbacks:  {n_fb}    bindings: {n_bind}"
        )
        opts = [
            "✏️  Rename display name",
            "📁  Change workspace",
            "🧠  Edit models / fallbacks",
            "🆔  Change agent id…",
            "🗑️  Remove this agent",
            "← Back",
        ]
        try:
            choice = choose_one(info + "\n\nChoose an action:", opts)
        except UserCancelled:
            return changed

        if choice.startswith("←"):
            return changed
        if choice.startswith("✏️"):
            try:
                nm = text_input("Display name:", entry.get("name", "")).strip()
            except UserCancelled:
                continue
            if nm and nm != entry.get("name"):
                entry["name"] = nm
                changed = True
        elif choice.startswith("📁"):
            try:
                ws = text_input("Workspace path:", entry.get("workspace", "")).strip()
            except UserCancelled:
                continue
            if ws and ws != entry.get("workspace"):
                entry["workspace"] = ws
                changed = True
        elif choice.startswith("🧠"):
            if model_submenu(data, base, aid):
                changed = True
        elif choice.startswith("🆔"):
            if rename_agent_id(data, aid):
                return True  # aid is now stale; back out to the refreshed list
        elif choice.startswith("🗑️"):
            if remove_agent(data, aid):
                return True


def agents_tab(data):
    """Create / edit / remove agents. Returns True if anything changed."""
    changed = False
    while True:
        agents = data["agents"]["list"]
        id_by_display = {}
        menu = ["➕  Create new agent…"]
        for a in agents:
            disp = f"•  {a.get('id')}   ·   {a.get('name', '')}"
            id_by_display[disp] = a.get("id")
            menu.append(disp)
        menu.append("← Back")
        try:
            pick = choose_one("Agents — pick one to edit, or create:", menu, ok="Open")
        except UserCancelled:
            return changed
        if pick.startswith("←"):
            return changed
        if pick.startswith("➕"):
            if create_agent(data):
                changed = True
            continue
        aid = id_by_display.get(pick)
        if aid and edit_agent(data, aid):
            changed = True


# --------------------------------------------------------------------------- #
# Models tab + main shell
# --------------------------------------------------------------------------- #
def models_tab(data):
    """Pick any agent (defaults + list) and edit its primary/fallbacks."""
    changed = False
    while True:
        targets = agent_targets(data)
        labels = [t[0] for t in targets]
        menu = [f"⚙  {lbl}" for lbl in labels]
        menu.append("← Back")
        try:
            pick = choose_one("Models — pick an agent:", menu, ok="Open")
        except UserCancelled:
            return changed
        if pick.startswith("←"):
            return changed
        label = pick[2:].strip()
        base = dict(zip(labels, [t[1] for t in targets]))[label]
        if model_submenu(data, base, label):
            changed = True


def main():
    if not JQ:
        alert(
            "jq is required but was not found.\n\n"
            "Install it with:  brew install jq\n"
            "then run this again.",
        )
        sys.exit(1)

    data = load_config()
    dirty = False

    try:
        while True:
            # Check gateway status on each loop to update options dynamically
            is_running = check_gateway_running()
            save_text = "💾  Save & Restart gateway" if is_running else "💾  Save & Launch gateway"
            save_text += (" •" if dirty else "")

            menu = [
                "🧠  Models — primary & fallbacks",
                "👥  Agents — create / edit / remove",
                save_text,
                "✕  Quit without saving",
            ]
            pick = choose_one("OpenClaw Configurator", menu, ok="Open")

            if pick.startswith("🧠"):
                if models_tab(data):
                    dirty = True
            elif pick.startswith("👥"):
                if agents_tab(data):
                    dirty = True
            elif pick.startswith("💾"):
                if not dirty:
                    # Let the user launch it even if configuration is not dirty!
                    # "Can you add in a switch to launch gateway, only shown when gateway is not active."
                    # If it's NOT active, they might want to just launch/start it from the menu even if they didn't edit anything,
                    # or they might expect to be able to save and launch.
                    # Let's support launching directly if the config is not dirty or prompt them nicely.
                    if not is_running:
                        if confirm(
                            "The gateway is not currently running. Launch it now?\n\n"
                            "This runs `make cold-restart` to start the Gateway service in the background.",
                            ok="Launch Gateway",
                        ):
                            restart_and_report(gateway_is_running=False)
                            is_running = check_gateway_running() # Recalculate status
                            continue
                    else:
                        alert("No changes to save.")
                        continue
                
                confirm_message = (
                    "Save changes to openclaw-template.json and run "
                    "`make cold-restart` to restart the gateway?"
                    if is_running else
                    "Save changes to openclaw-template.json and run "
                    "`make cold-restart` to launch the gateway?"
                )
                ok_btn = "Save & Restart" if is_running else "Save & Launch"
                
                if confirm(confirm_message, ok=ok_btn):
                    save_config(data)
                    restart_and_report(gateway_is_running=is_running)
                    dirty = False # Reset dirty flag upon save
                    is_running = check_gateway_running() # Recalculate status
            elif pick.startswith("✕"):
                if dirty and not confirm("Discard unsaved changes?", ok="Discard"):
                    continue
                return

    except UserCancelled:
        # Top-level cancel == quit; protect unsaved work.
        if dirty:
            is_running = check_gateway_running()
            ok_btn = "Save & Restart" if is_running else "Save & Launch"
            if confirm("You have unsaved changes. Save now?", ok=ok_btn):
                save_config(data)
                restart_and_report(gateway_is_running=is_running)
        return


if __name__ == "__main__":
    main()
