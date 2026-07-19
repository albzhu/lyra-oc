#!/usr/bin/env python3
"""
interagent_queue — live transaction observer for the MIAB callback ledger.

Tails the append-only callback ledger (state/callbacks/ledger.jsonl), converts raw
create / forward / return / resolve / fail events into beautiful rich-text logs using
the agent identity map, advances a once-only cursor, and — when the live toggle is on —
pipes the formatted batch to the designated Discord channel (#scheduling).

This is a READ-ONLY observer: it never mutates the ledger or envelopes. The only file it
writes is its own queue_state.json (toggle + cursor), saved atomically.

Path resolution (portable, sovereign):
  - CLAW_HOME    : broker root          (default: ~/.openclaw)
  - CLAW_LEDGER  : explicit ledger path (overrides CLAW_HOME/state/callbacks/ledger.jsonl)
  - LYRA_WORKSPACE / CLAW_QUEUE_STATE : where queue_state.json lives
                   (default: ~/.openclaw/workspace/state/callbacks/queue_state.json)
"""
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

# --------------------------------------------------------------------------- paths
def claw_home() -> Path:
    return Path(os.environ.get("CLAW_HOME", "~/.openclaw")).expanduser()

def workspace_dir() -> Path:
    return Path(os.environ.get("LYRA_WORKSPACE", "~/.openclaw/workspace")).expanduser()

def ledger_file() -> Path:
    env = os.environ.get("CLAW_LEDGER")
    if env:
        return Path(env).expanduser()
    return claw_home() / "state" / "callbacks" / "ledger.jsonl"

def state_file() -> Path:
    env = os.environ.get("CLAW_QUEUE_STATE")
    if env:
        return Path(env).expanduser()
    return workspace_dir() / "state" / "callbacks" / "queue_state.json"

DEFAULT_CHANNEL = "channel:1517433532518109195"  # #scheduling

# --------------------------------------------------------------- agent identity map
AGENT_MAP = {
    "main": "✨ LYRA (Main)",
    "planner": "🥷⚔️ SPECTRE (Planner)",
    "coder": "💥 Cinder (Coder)",
    "reviewer": "🥷👁️ ECHO (Reviewer)",
    "debug": "🔬 Zero (Debug)",
    "utility": "🛠️ Swift (Utility)",
    "sigma": "⚡ SIGMA (Portfolio)",
    "free": "🌌 VOID (Scout)",
    "sweep": "🧹 Callback Reaper"
}

def who(name):
    """Friendly display for a logical agent name, falling back to the raw name."""
    return AGENT_MAP.get(name, name or "Unknown")

# --------------------------------------------------------------------------- state
def load_state() -> dict:
    p = state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"enabled": False, "last_processed_line": 0, "target_channel": DEFAULT_CHANNEL}

def save_state(state: dict) -> None:
    p = state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)

# ----------------------------------------------------------------- summarization
def sanitize_and_summarize(text, limit=350):
    """Clean up and produce a beautiful concise summary of task/result text."""
    if not text:
        return ""
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        l = line.strip()
        if not l:
            continue
        # Exclude bulky checklist / setup blueprints in summaries
        if any(kw in l.lower() for kw in ["callback://", "python3", "claw-callback", "mkdir", "chmod", "curl"]):
            continue
        cleaned_lines.append(line)
        
    cleaned_text = " ".join(cleaned_lines)
    cleaned_text = " ".join(cleaned_text.split())
    
    if len(cleaned_text) > limit:
        return cleaned_text[:limit-3] + "..."
    return cleaned_text

# ----------------------------------------------------------------- event rendering
def format_event(rec):
    """Render one ledger record into a rich-text Discord/stdout message (or None)."""
    event = rec.get("event")
    cid = rec.get("id", "unknown")[:14]
    by = who(rec.get("by"))

    if event == "create":
        target = who(rec.get("to"))
        task_summary = sanitize_and_summarize(rec.get("task", ""))
        return (
            f"📥 **[Enqueued Task]** `{cid}`\n"
            f"**From:** {by}\n"
            f"**To:** {target}\n"
            f"**Task Assigned:** {task_summary}"
        )
    if event == "forward":
        target = who(rec.get("to"))
        return (
            f"➡️ **[Forwarded Task]** `{cid}`\n"
            f"**By:** {by}\n"
            f"**Forwarded To:** {target}\n"
            f"*Packaged parent callback frame onto LIFO stack.*"
        )
    if event == "return":
        wake_target = who(rec.get("wake"))
        return (
            f"↩️ **[Returning Task]** `{cid}`\n"
            f"**From:** {by}\n"
            f"**Waking:** {wake_target}\n"
            f"*Handing execution results back up the stack.*"
        )
    if event == "resolve":
        task_summary = sanitize_and_summarize(rec.get("task", ""))
        result_summary = sanitize_and_summarize(rec.get("result", ""))
        return (
            f"✅ **[Resolved Task]** `{cid}`\n"
            f"**By:** {by}\n"
            f"**Task:** {task_summary}\n"
            f"**Resolution Outcome:** {result_summary}"
        )
    if event == "cancel":
        reason = rec.get("reason", "Cancelled by user / system command")
        return (
            f"❌ **[Cancelled Task]** `{cid}`\n"
            f"**By:** {by}\n"
            f"**Reason:** {reason}"
        )
    if event == "fail":
        reason = rec.get("reason", "stale")
        holder = who(rec.get("holder"))
        return (
            f"⚠️ **[Callback Failed/Reaped]** `{cid}`\n"
            f"**By:** {by}\n"
            f"**Reason:** {reason}\n"
            f"**Last Holder:** {holder}"
        )
    return None

# ----------------------------------------------------------------- ledger sweep
def collect_new(state, advance):
    """Read the ledger from the cursor, return (messages, status, new_cursor).
    If advance is False the cursor is returned unchanged (peek mode)."""
    lf = ledger_file()
    cursor = state.get("last_processed_line", 0)
    if not lf.exists():
        return [], f"Ledger not found at {lf}; nothing to process.", cursor
    try:
        with lf.open("r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
    except Exception as e:
        return [], f"Error reading ledger: {e}", cursor

    total = len(lines)
    if cursor > total:
        cursor = 0
    if cursor >= total:
        return [], "No new callback events.", total

    messages = []
    for line in lines[cursor:]:
        try:
            msg = format_event(json.loads(line))
            if msg:
                messages.append(msg)
        except Exception as e:
            print(f"skip malformed ledger line: {e}", file=sys.stderr)

    new_cursor = total if advance else cursor
    status = f"Read {total - cursor} new entries, rendered {len(messages)} message(s)."
    return messages, status, new_cursor

# ----------------------------------------------------------------- Discord delivery
def deliver(messages, channel):
    """Pipe formatted logs to the target Discord channel via the openclaw CLI.

    Degrades gracefully: if `openclaw` isn't on PATH (e.g. running off-host), returns
    False so the caller falls back to stdout — the observer never hard-fails on delivery.
    """
    if not messages:
        return True
    if shutil.which("openclaw") is None:
        return True
    chan = channel.split(":", 1)[-1] if ":" in channel else channel
    body = "\n\n".join(messages)
    try:
        subprocess.run(
            ["openclaw", "notify", "--channel", f"discord:{chan}", "--text", body],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return True
    except Exception as e:
        print(f"discord delivery failed: {e}", file=sys.stderr)
        return False

# --------------------------------------------------------------------------- main
def main():
    cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "process"
    state = load_state()

    if cmd == "on":
        state["enabled"] = True
        save_state(state)
        print(json.dumps({"enabled": True, "status": "ON"}))
        return

    if cmd == "off":
        state["enabled"] = False
        save_state(state)
        print(json.dumps({"enabled": False, "status": "OFF"}))
        return

    if cmd == "status":
        print(json.dumps({
            "enabled": state.get("enabled", False),
            "status": "ON" if state.get("enabled") else "OFF",
            "last_processed_line": state.get("last_processed_line", 0),
            "target_channel": state.get("target_channel", DEFAULT_CHANNEL),
            "ledger": str(ledger_file()),
            "state_file": str(state_file())
        }, indent=2))
        return

    if cmd == "peek":
        messages, status, _ = collect_new(state, advance=False)
        print(json.dumps({"messages": messages, "status": status, "delivered": False}, indent=2))
        return

    if cmd == "process":
        if not state.get("enabled", False):
            print(json.dumps({"messages": [], "status": "Queue DISABLED — skipping sweep.",
                              "delivered": False}))
            return
        messages, status, new_cursor = collect_new(state, advance=True)
        delivered = deliver(messages, state.get("target_channel", DEFAULT_CHANNEL))
        if delivered or not messages:
            state["last_processed_line"] = new_cursor
            save_state(state)
        print(json.dumps({"messages": messages, "status": status, "delivered": delivered},
                         indent=2))
        return

    print(json.dumps({"ok": False, "error": f"unknown command: {cmd}",
                      "commands": ["on", "off", "status", "process", "peek"]}), file=sys.stderr)
    sys.exit(2)

if __name__ == "__main__":
    main()
