#!/usr/bin/env python3
"""
token_tracker — daily token utilization & cost compiler for the OpenClaw ensemble.

Recursively parses every agent's session trajectories for a target date, buckets tokens
(Input / Output / Cache / Reasoning), computes micro-dollar costs against a maintained price
catalog, attributes turns to the skill that triggered them, and prints a structured daily
report plus an automated Resource Leak Audit (rogue loops, cache-miss ratio, frontier-model
overkill).

Data source: $CLAW_HOME/agents/<agent>/sessions/<id>.trajectory.jsonl
"""
import argparse
import datetime
import json
import os
from pathlib import Path

try:
    import pytz
    def _localtz(name):
        return pytz.timezone(name)
except ImportError:  # graceful fallback if pytz isn't installed
    pytz = None
    def _localtz(name):
        return datetime.timezone.utc

# --------------------------------------------------------------------------- pricing
# USD per 1,000,000 tokens. Keys are matched as substrings against the model reference,
# so list the most specific keys. Maintain this as models are added or re-tiered.
PRICING = {
    # --- Google Gemini ---
    "google/gemini-3.5-flash":       {"input": 0.075, "output": 0.30, "cache": 0.0375},
    "google/gemini-3.1-flash-lite":  {"input": 0.075, "output": 0.30, "cache": 0.0375},
    "google/gemini-2.5-flash-lite":  {"input": 0.075, "output": 0.30, "cache": 0.0375},
    "gemini-3.5-flash":              {"input": 0.075, "output": 0.30, "cache": 0.0375},
    "gemini-3.1-flash-lite":         {"input": 0.075, "output": 0.30, "cache": 0.0375},
    "gemini-2.5-flash-lite":         {"input": 0.075, "output": 0.30, "cache": 0.0375},
    # --- Anthropic Claude ---
    "anthropic/claude-sonnet-4.6":   {"input": 3.00,  "output": 15.00, "cache": 0.30},
    "anthropic/claude-opus-4.8":     {"input": 15.00, "output": 75.00, "cache": 1.50},
    "anthropic/claude-opus":         {"input": 15.00, "output": 75.00, "cache": 1.50},
    "anthropic/claude-3-5-haiku":    {"input": 0.80,  "output": 4.00,  "cache": 0.08},
    "claude-sonnet-4.6":             {"input": 3.00,  "output": 15.00, "cache": 0.30},
    "claude-haiku":                  {"input": 0.80,  "output": 4.00,  "cache": 0.08},
    # --- DeepSeek ---
    "deepseek/deepseek-v4":          {"input": 0.14,  "output": 0.28, "cache": 0.014},
    "deepseek-v4":                   {"input": 0.14,  "output": 0.28, "cache": 0.014},
    # --- OpenAI GPT-5 ---
    "openai/gpt-5":                  {"input": 5.00,  "output": 15.00, "cache": 0.50},
    "gpt-5":                         {"input": 5.00,  "output": 15.00, "cache": 0.50},
    # --- Local Ollama (no marginal API cost) ---
    "ollama":                        {"input": 0.0,   "output": 0.0,  "cache": 0.0},
    "local":                         {"input": 0.0,   "output": 0.0,  "cache": 0.0},
    # --- Fallback ---
    "default":                       {"input": 1.00,  "output": 5.00, "cache": 0.50},
}

# Frontier/high-reasoning tiers that should NOT be doing routine, low-stakes work.
FRONTIER_HINTS = ("claude-sonnet", "claude-opus", "gpt-5")
# Preferred cheap tiers to recommend in their place.
CHEAP_SUGGESTION = "gemini-3.1-flash-lite / gemini-2.5-flash-lite, or a local Ollama model"

# Leak-detection thresholds (see SKILL.md checklist).
LOOP_WINDOW_SEC = 180     # 3 minutes
LOOP_MIN_TURNS = 10       # 10+ rapid cycles
LOOP_SMALL_TOKENS = 5000  # each turn "small"
CACHE_RATIO_FLOOR = 0.40  # below this on a high-volume agent => flag
CACHE_MIN_INPUT = 200_000 # only judge cache ratio on agents with real input volume
OVERKILL_MAX_TOKENS = 4000  # frontier turn this small on routine work => overkill


def get_price_info(model_id):
    if not model_id:
        return PRICING["default"]
    for key, info in PRICING.items():
        if key == "default":
            continue
        if key in model_id or model_id in key:
            return info
    return PRICING["default"]


def calculate_cost(model_id, input_tokens, output_tokens, cache_tokens):
    p = get_price_info(model_id)
    return (input_tokens / 1e6) * p["input"] \
         + (output_tokens / 1e6) * p["output"] \
         + (cache_tokens / 1e6) * p.get("cache", p["input"])


def agents_dir():
    home = Path(os.environ.get("CLAW_HOME", "~/.openclaw")).expanduser()
    return os.environ.get("CLAW_AGENTS_DIR", str(home / "agents"))


def normalize_model_ref(provider, model_id):
    """Avoid double provider prefixes while preserving the existing report style."""
    model_id = model_id or "unknown"
    provider = provider or "unknown"
    if provider == "unknown" or provider in model_id:
        return model_id
    return f"{provider}/{model_id}"


def attribute_skill(current_skill, display_name):
    """Resolve the skill bucket, with display-name fallback heuristics."""
    if current_skill != "Interactions":
        return current_skill
    d = display_name.lower()
    if "portfolio" in d or "schwab" in d:
        return "portfolio-check"
    if "sync" in d or "swift" in d:
        return "repo-sync"
    if "wake-up" in d or "wake up" in d:
        return "daily-wake-up"
    if "maintenance" in d or "archive" in d:
        return "self-maintenance"
    if "hygiene" in d:
        return "process-hygiene"
    return "Interactions"


def parse_skill_tag(prompt):
    """Extract a skill/trigger name from a leading [bracket] tag in a prompt."""
    if not (prompt and prompt.startswith("[")):
        return None
    close = prompt.find("]")
    if close == -1:
        return None
    tag = prompt[1:close]
    if "cron:" in tag:
        parts = tag.split(" ")
        return " ".join(parts[1:]) if len(parts) > 1 else tag
    if ":" in tag:
        return tag.split(":")[0]
    return tag


def compile_report(target_date_str, tz_name="America/Los_Angeles", return_data=False):
    local_tz = _localtz(tz_name)
    adir = agents_dir()

    grand = {k: 0 for k in ("requests", "input", "output", "cache", "reasoning", "total")}
    grand["cost"] = 0.0
    by_agent, by_model, by_skill = {}, {}, {}
    intensive_turns = []
    # turn timeline per (agent, session) for loop detection
    timelines = {}

    if not os.path.exists(adir):
        msg = f"Error: agents directory not found at {adir}."
        return ({}, msg) if return_data else msg

    for agent in sorted(os.listdir(adir)):
        sessions_path = os.path.join(adir, agent, "sessions")
        if not os.path.isdir(sessions_path):
            continue

        meta = {}
        meta_path = os.path.join(sessions_path, "sessions.json")
        if os.path.exists(meta_path):
            try:
                meta = json.load(open(meta_path))
            except Exception:
                pass

        for fname in os.listdir(sessions_path):
            if not fname.endswith(".trajectory.jsonl"):
                continue
            session_id = fname.split(".")[0]
            display_name = session_id
            for _, m in (meta.items() if isinstance(meta, dict) else []):
                if isinstance(m, dict) and m.get("sessionId") == session_id:
                    display_name = m.get("displayName") or m.get("displayNameRaw") or display_name
                    break

            try:
                with open(os.path.join(sessions_path, fname)) as f:
                    current_skill = "Interactions"
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except Exception:
                            continue

                        if row.get("type") == "prompt.submitted":
                            tag = parse_skill_tag(row.get("data", {}).get("prompt", ""))
                            if tag:
                                current_skill = tag
                            continue

                        if row.get("type") != "model.completed":
                            continue
                        ts_str = row.get("ts")
                        if not ts_str:
                            continue
                        dt_utc = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        dt_local = dt_utc.astimezone(local_tz)
                        if dt_local.strftime("%Y-%m-%d") != target_date_str:
                            continue

                        usage = row.get("data", {}).get("usage", {}) or {}
                        last = (row.get("data", {}).get("promptCache", {}) or {}).get("lastCallUsage", {}) or {}
                        inp = usage.get("input", 0) or 0
                        out = usage.get("output", 0) or 0
                        cache = usage.get("cacheRead", 0) or 0
                        reasoning = usage.get("reasoningTokens", last.get("reasoningTokens", 0)) or 0
                        total = usage.get("total", inp + out) or 0
                        ref = normalize_model_ref(row.get("provider"), row.get("modelId"))
                        cost = calculate_cost(ref, inp, out, cache)

                        skill = attribute_skill(current_skill, display_name)
                        s = by_skill.setdefault(skill, {"turns": 0, "total_tokens": 0, "cost": 0.0})
                        s["turns"] += 1; s["total_tokens"] += total; s["cost"] += cost

                        grand["requests"] += 1; grand["input"] += inp; grand["output"] += out
                        grand["cache"] += cache; grand["reasoning"] += reasoning
                        grand["total"] += total; grand["cost"] += cost

                        a = by_agent.setdefault(agent, {k: 0 for k in ("requests", "input", "output", "cache", "reasoning", "total")})
                        a.setdefault("cost", 0.0)
                        a["requests"] += 1; a["input"] += inp; a["output"] += out
                        a["cache"] += cache; a["reasoning"] += reasoning
                        a["total"] += total; a["cost"] += cost

                        md = by_model.setdefault(ref, {"requests": 0, "total": 0, "cost": 0.0})
                        md["requests"] += 1; md["total"] += total; md["cost"] += cost

                        turn = {"agent": agent, "session_name": display_name, "model": ref,
                                "total_tokens": total, "cost": cost, "skill": skill,
                                "ts": dt_local, "time": dt_local.strftime("%H:%M:%S")}
                        intensive_turns.append(turn)
                        timelines.setdefault((agent, session_id), []).append(turn)
            except Exception:
                pass

    leaks = detect_leaks(by_agent, timelines, intensive_turns)
    data = {"grand": grand, "by_agent": by_agent, "by_model": by_model,
            "by_skill": by_skill, "intensive_turns": intensive_turns, "leaks": leaks}
    report = render_report(target_date_str, data)
    return (data, report) if return_data else report


def detect_leaks(by_agent, timelines, turns):
    findings = {"loops": [], "cache": [], "overkill": []}

    # 1. Rogue loops: 10+ small turns in a 3-min sliding window per session.
    for (agent, sid), tl in timelines.items():
        tl = sorted(tl, key=lambda t: t["ts"])
        small = [t for t in tl if t["total_tokens"] < LOOP_SMALL_TOKENS]
        i = 0
        for j in range(len(small)):
            while (small[j]["ts"] - small[i]["ts"]).total_seconds() > LOOP_WINDOW_SEC:
                i += 1
            if j - i + 1 >= LOOP_MIN_TURNS:
                findings["loops"].append({
                    "agent": agent, "session": small[i]["session_name"],
                    "count": j - i + 1,
                    "window": f"{small[i]['time']}–{small[j]['time']}",
                    "model": small[j]["model"]})
                break  # one flag per session is enough

    # 2. Cache-miss ratio on high-volume agents.
    for agent, d in by_agent.items():
        denom = d["cache"] + d["input"]
        if d["input"] >= CACHE_MIN_INPUT and denom > 0:
            ratio = d["cache"] / denom
            if ratio < CACHE_RATIO_FLOOR:
                findings["cache"].append({"agent": agent, "ratio": ratio,
                                          "cache": d["cache"], "input": d["input"]})

    # 3. Frontier-model overkill on small/routine turns.
    overkill_cost = {}
    for t in turns:
        if (any(h in t["model"] for h in FRONTIER_HINTS)
                and 0 < t["total_tokens"] < OVERKILL_MAX_TOKENS):
            key = (t["agent"], t["model"], t["skill"])
            agg = overkill_cost.setdefault(key, {"turns": 0, "cost": 0.0})
            agg["turns"] += 1; agg["cost"] += t["cost"]
    for (agent, model, skill), agg in overkill_cost.items():
        if agg["turns"] >= 3:  # repetitive
            findings["overkill"].append({"agent": agent, "model": model, "skill": skill,
                                         "turns": agg["turns"], "cost": agg["cost"]})
    return findings


def render_report(date_str, d):
    g, by_agent, by_model = d["grand"], d["by_agent"], d["by_model"]
    by_skill, turns, leaks = d["by_skill"], d["intensive_turns"], d["leaks"]
    L = []
    L.append(f"📊 **Daily Token Usage Report — {date_str}**")
    L.append("")
    L.append("__**Global Summary**__")
    L.append(f"- **Total Completed Turns:** {g['requests']}")
    L.append(f"- **Total Est. Cost:** ${g['cost']:.4f}")
    L.append(f"- **Total Token Consumption:** {g['total']:,}")
    L.append(f"  - Input: {g['input']:,}")
    L.append(f"  - Output: {g['output']:,}")
    L.append(f"  - Cache Read: {g['cache']:,}")
    L.append(f"  - Reasoning: {g['reasoning']:,}")
    denom = g["cache"] + g["input"]
    if denom:
        L.append(f"  - Cache Hit Ratio: {g['cache'] / denom:.0%}")
    L.append("")

    L.append("__**Usage & Averages by Skill / Task**__")
    if by_skill:
        for skill, s in sorted(by_skill.items(), key=lambda x: x[1]["cost"], reverse=True):
            avg = s["total_tokens"] / s["turns"] if s["turns"] else 0
            L.append(f"- **{skill}**")
            L.append(f"  - Total Turns: {s['turns']} | Est. Total Cost: ${s['cost']:.4f}")
            L.append(f"  - Total Tokens: {s['total_tokens']:,} (Avg: {int(avg):,} per turn)")
    else:
        L.append("- No skill attribution recorded today.")
    L.append("")

    L.append("__**Agent Breakdown**__")
    if by_agent:
        for ag, a in sorted(by_agent.items(), key=lambda x: x[1]["cost"], reverse=True):
            L.append(f"**{ag.upper()}**")
            L.append(f"- Turns: {a['requests']} | Total Tokens: {a['total']:,}")
            L.append(f"- Input: {a['input']:,} | Output: {a['output']:,} | Cache: {a['cache']:,}")
            L.append(f"- Cost: ${a['cost']:.4f}")
    else:
        L.append("- No agent activity recorded today.")
    L.append("")

    L.append("__**Model Breakdown**__")
    if by_model:
        for m, md in sorted(by_model.items(), key=lambda x: x[1]["cost"], reverse=True):
            L.append(f"- **{m}**: {md['requests']} turns | {md['total']:,} tokens | ${md['cost']:.4f}")
    else:
        L.append("- No model usage recorded today.")
    L.append("")

    L.append("__**Top 5 Most Resource-Intensive Turns**__")
    if turns:
        for i, t in enumerate(sorted(turns, key=lambda x: x["cost"], reverse=True)[:5], 1):
            L.append(f"{i}. **{t['agent'].upper()}** at {t['time']} — `{t['session_name']}`")
            L.append(f"   Model: `{t['model']}` | Tokens: {t['total_tokens']:,} | Cost: ${t['cost']:.4f}")
    else:
        L.append("- No turns recorded today.")
    L.append("")

    L.append("__**⚠️ Resource Leak Audit**__")
    any_flag = False
    if leaks["loops"]:
        any_flag = True
        L.append("**Rogue model loops (10+ small turns < 3 min):**")
        for f in leaks["loops"]:
            L.append(f"- {f['agent'].upper()} `{f['session']}`: {f['count']} cycles "
                     f"{f['window']} on `{f['model']}`")
    if leaks["cache"]:
        any_flag = True
        L.append("**Low cache-hit efficiency (high-volume agents):**")
        for f in leaks["cache"]:
            L.append(f"- {f['agent'].upper()}: {f['ratio']:.0%} hit "
                     f"(cache {f['cache']:,} vs input {f['input']:,})")
    if leaks["overkill"]:
        any_flag = True
        L.append(f"**Frontier-model overkill (prefer {CHEAP_SUGGESTION}):**")
        for f in leaks["overkill"]:
            L.append(f"- {f['agent'].upper()} ran `{f['model']}` for `{f['skill']}` "
                     f"× {f['turns']} small turns (${f['cost']:.4f})")
    if not any_flag:
        L.append("- ✅ No resource leaks detected today.")

    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Compile daily agent token usage report")
    ap.add_argument("--date", help="Target date YYYY-MM-DD (default: today)")
    ap.add_argument("--tz", default="America/Los_Angeles", help="Local timezone")
    ap.add_argument("--save", action="store_true",
                    help="Also write observability/daily/token_report_<date>.md")
    args = ap.parse_args()

    target = args.date
    if not target:
        now = datetime.datetime.now(_localtz(args.tz))
        target = now.strftime("%Y-%m-%d")

    report = compile_report(target, tz_name=args.tz)
    print(report)

    if args.save:
        ws = Path(os.environ.get("LYRA_WORKSPACE", "~/.openclaw/workspace")).expanduser()
        out_dir = ws / "observability" / "daily"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"token_report_{target}.md"
        out_path.write_text(report)
        print(f"\n[saved] {out_path}")
