#!/usr/bin/env python3
"""
generate_observability_manifest — weekly token/cost aggregator.

Aggregates a week of daily trajectory records (by reusing token_tracker.compile_report per
day, so pricing and leak heuristics stay single-sourced) into one clean markdown manifest under
observability/weekly/observability_manifest_<end-date>.md.

Default range: the previous completed Mon–Sun week relative to the run date. Override with
--start/--end or anchor with --date.
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_tracker import compile_report, _localtz  # noqa: E402


def weekly_range(run_date):
    """Previous completed Monday..Sunday before run_date."""
    end = run_date - datetime.timedelta(days=run_date.weekday() + 1)  # last Sunday
    start = end - datetime.timedelta(days=6)                          # that Monday
    return start, end


def merge_agent(dst, src):
    for ag, a in src.items():
        d = dst.setdefault(ag, {k: 0 for k in ("requests", "input", "output", "cache", "reasoning", "total")})
        d.setdefault("cost", 0.0)
        for k in ("requests", "input", "output", "cache", "reasoning", "total", "cost"):
            d[k] += a.get(k, 0)


def merge_model(dst, src):
    for m, md in src.items():
        d = dst.setdefault(m, {"requests": 0, "total": 0, "cost": 0.0})
        for k in ("requests", "total", "cost"):
            d[k] += md.get(k, 0)


def compile_manifest(start_date, end_date, tz_name="America/Los_Angeles"):
    grand = {k: 0 for k in ("requests", "input", "output", "cache", "reasoning", "total")}
    grand["cost"] = 0.0
    by_date, by_agent, by_model = {}, {}, {}
    all_turns, all_leaks = [], {"loops": [], "cache": [], "overkill": []}

    cur = start_date
    while cur <= end_date:
        ds = cur.strftime("%Y-%m-%d")
        data, _ = compile_report(ds, tz_name=tz_name, return_data=True)
        if isinstance(data, dict) and data.get("grand"):
            g = data["grand"]
            for k in grand:
                grand[k] += g.get(k, 0)
            by_date[ds] = {"requests": g["requests"], "total": g["total"], "cost": g["cost"]}
            merge_agent(by_agent, data["by_agent"])
            merge_model(by_model, data["by_model"])
            for t in data["intensive_turns"]:
                all_turns.append({**t, "date": ds})
            for cat in ("loops", "cache", "overkill"):
                for f in data["leaks"][cat]:
                    all_leaks[cat].append({**f, "date": ds})
        else:
            by_date[ds] = {"requests": 0, "total": 0, "cost": 0.0}
        cur += datetime.timedelta(days=1)

    return render(start_date, end_date, grand, by_date, by_agent, by_model, all_turns, all_leaks)


def render(start_date, end_date, grand, by_date, by_agent, by_model, turns, leaks):
    L = []
    L.append(f"# 📊 Weekly Observability Manifest — {start_date} to {end_date}")
    L.append("")
    L.append("## Global Summary")
    L.append(f"- **Total Completed Turns:** {grand['requests']}")
    L.append(f"- **Total Est. Cost:** ${grand['cost']:.4f}")
    L.append(f"- **Total Token Consumption:** {grand['total']:,}")
    L.append(f"  - Input: {grand['input']:,}")
    L.append(f"  - Output: {grand['output']:,}")
    L.append(f"  - Cache Read: {grand['cache']:,}")
    L.append(f"  - Reasoning: {grand['reasoning']:,}")
    denom = grand["cache"] + grand["input"]
    if denom:
        L.append(f"  - Cache Hit Ratio: {grand['cache'] / denom:.0%}")
    days = max(1, (end_date - start_date).days + 1)
    L.append(f"  - Avg Daily Cost: ${grand['cost'] / days:.4f}")
    L.append("")

    L.append("## Daily Breakdown")
    for ds in sorted(by_date):
        d = by_date[ds]
        L.append(f"- **{ds}**: {d['requests']} turns | {d['total']:,} tokens | ${d['cost']:.4f}")
    L.append("")

    L.append("## Agent Breakdown")
    if by_agent:
        for ag, a in sorted(by_agent.items(), key=lambda x: x[1]["cost"], reverse=True):
            L.append(f"**{ag.upper()}**")
            L.append(f"- Turns: {a['requests']} | Total Tokens: {a['total']:,}")
            L.append(f"- Input: {a['input']:,} | Output: {a['output']:,} | Cache: {a['cache']:,}")
            L.append(f"- Cost: ${a['cost']:.4f}")
    else:
        L.append("- No agent activity recorded this week.")
    L.append("")

    L.append("## Model Breakdown")
    if by_model:
        for m, md in sorted(by_model.items(), key=lambda x: x[1]["cost"], reverse=True):
            L.append(f"- **{m}**: {md['requests']} turns | {md['total']:,} tokens | ${md['cost']:.4f}")
    else:
        L.append("- No model usage recorded this week.")
    L.append("")

    L.append("## Top 10 Most Resource-Intensive Turns")
    if turns:
        for i, t in enumerate(sorted(turns, key=lambda x: x["cost"], reverse=True)[:10], 1):
            L.append(f"{i}. **{t['agent'].upper()}** on {t['date']} at {t['time']} — `{t['session_name']}`")
            L.append(f"   Model: `{t['model']}` | Tokens: {t['total_tokens']:,} | Cost: ${t['cost']:.4f}")
    else:
        L.append("- No turns recorded this week.")
    L.append("")

    L.append("## ⚠️ Weekly Resource Leak Findings")
    total_flags = sum(len(leaks[c]) for c in leaks)
    if total_flags == 0:
        L.append("- ✅ No resource leaks detected this week.")
    else:
        if leaks["loops"]:
            L.append("**Rogue model loops:**")
            for f in leaks["loops"]:
                L.append(f"- {f['date']} — {f['agent'].upper()} `{f['session']}`: "
                         f"{f['count']} cycles {f['window']} on `{f['model']}`")
        if leaks["cache"]:
            L.append("**Low cache-hit efficiency:**")
            for f in leaks["cache"]:
                L.append(f"- {f['date']} — {f['agent'].upper()}: {f['ratio']:.0%} hit "
                         f"(cache {f['cache']:,} vs input {f['input']:,})")
        if leaks["overkill"]:
            L.append("**Frontier-model overkill:**")
            for f in leaks["overkill"]:
                L.append(f"- {f['date']} — {f['agent'].upper()} ran `{f['model']}` for "
                         f"`{f['skill']}` × {f['turns']} small turns (${f['cost']:.4f})")
    L.append("")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Aggregate daily trajectories into a weekly manifest")
    ap.add_argument("--date", help="Anchor date YYYY-MM-DD; reports the prior Mon–Sun week (default: today)")
    ap.add_argument("--start", help="Explicit start date YYYY-MM-DD (use with --end)")
    ap.add_argument("--end", help="Explicit end date YYYY-MM-DD (use with --start)")
    ap.add_argument("--tz", default="America/Los_Angeles", help="Local timezone")
    args = ap.parse_args()

    if args.start and args.end:
        start = datetime.datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.datetime.strptime(args.end, "%Y-%m-%d").date()
    else:
        run_date = (datetime.datetime.strptime(args.date, "%Y-%m-%d").date()
                    if args.date else datetime.datetime.now(_localtz(args.tz)).date())
        start, end = weekly_range(run_date)

    manifest = compile_manifest(start, end, tz_name=args.tz)

    ws = Path(os.environ.get("LYRA_WORKSPACE", "~/.openclaw/workspace")).expanduser()
    out_dir = ws / "observability" / "weekly"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"observability_manifest_{end}.md"
    out_path.write_text(manifest)
    print(manifest)
    print(f"\n[saved] {out_path}")
