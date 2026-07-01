---
name: token-observability
description: Compile daily token utilization, calculate USD costs, and monitor multi-agent resource performance across the OpenClaw ensemble. Use to audit per-skill / per-agent / per-model spend, detect rogue model loops and token waste, check cache-hit efficiency, flag frontier-model overkill, and roll daily trajectories up into weekly markdown reports.
---

# Token Observability — Multi-Agent Cost & Resource Auditing

## Mission

Continuous, near-real-time auditing of every multi-agent and API execution across OpenClaw.
The goal is threefold: **verify costs** (turn-level micro-dollar accounting against a maintained
price catalog), **establish baselines** (average spend and token shape per skill, per agent, per
model), and **catch waste before it compounds** — rogue loop runs, cache thrash, and high-cost
frontier models doing low-stakes work that a cheap tier should handle.

Observability is the feedback loop that keeps a sovereign multi-agent network economical: you
cannot optimize what you do not measure, and an unattended agent ensemble can quietly burn
dollars in retry loops or mis-routed model selection. This skill turns the raw execution
trajectories every agent already writes into actionable cost intelligence.

---

## Data Source: Session Trajectories

Every agent persists an append-only execution trace per session under:

```
~/.openclaw/agents/<agent-name>/sessions/<session-id>.trajectory.jsonl
```

(Root is `$CLAW_HOME/agents`, default `~/.openclaw/agents`.) Agents include `main` (LYRA),
`planner` (SPECTRE), `coder` (Cinder), `reviewer` (ECHO), `sigma`, `utility` (Swift), `free`
(VOID), `debug` (Zero), and others.

### How to read a `.trajectory.jsonl`

One JSON object per line. The two row types that matter for cost accounting:

- **`prompt.submitted`** — carries `data.prompt`. The leading bracket tag identifies the
  trigger/skill, e.g. `[cron:<uuid> Daily Wake-up]` or `[portfolio-check: ...]`. This is how a
  burst of `model.completed` turns gets attributed to the skill that caused them.
- **`model.completed`** — the billable unit. Key fields:
  - `ts` — ISO-8601 UTC timestamp (convert to local TZ to bucket by day).
  - `provider` + `modelId` — the model reference (note `modelId` is *sometimes already*
    provider-prefixed, e.g. `openrouter/owl-alpha`; normalize to avoid a double prefix).
  - `data.usage` — the token buckets: `input`, `output`, `cacheRead`, `reasoningTokens`,
    `total`. Reasoning tokens may also live under `data.promptCache.lastCallUsage.reasoningTokens`
    — fall back to it when the top-level field is absent.

`sessions.json` in the same folder maps `sessionId → displayName`, giving human-readable session
labels (channel names, cron labels) for the report.

### Cross-referencing cost

For each `model.completed`, resolve the model against the **price catalog** (USD per 1,000,000
tokens, split into `input` / `output` / `cache`) and compute:

```
cost = input/1e6 * price.input  +  output/1e6 * price.output  +  cacheRead/1e6 * price.cache
```

Unknown models fall back to a conservative `default` price so spend is never silently zeroed.
Keep the catalog in `scripts/token_tracker.py` (`PRICING`) current as models are added or
re-tiered.

---

## Resource-Leak Audit Checklist

Run this checklist daily (the tracker emits a **Resource Leak Audit** section automatically;
the thresholds below are the heuristics it applies). Treat any flag as a prompt to investigate
the offending session, not as a hard failure.

### 1. Rogue model loop detection

A healthy turn does real work. A loop is a flurry of tiny, near-identical completions — usually
an agent stuck re-trying, re-reading, or ping-ponging with a tool.

- **Flag when:** a single agent/session produces **10+ `model.completed` turns within a
  3-minute sliding window**, each with **small token counts** (total < ~5k), i.e. rapid
  low-yield cycles.
- **Why it matters:** loops scale linearly in cost while producing ~no incremental output; left
  overnight they are the single biggest avoidable burn.
- **Action:** inspect the session's `prompt.submitted`/tool rows for the cycle; add a guard, a
  retry cap, or a circuit-breaker to the skill that triggered it.

### 2. Cache-miss efficiency ratio

Prompt caching is the main lever on input cost. A low cache-read share means the agent is
re-sending uncached context every turn.

- **Compute:** `cache_hit_ratio = cacheRead / (cacheRead + input)` per agent and globally.
- **Flag when:** ratio is **low on a high-volume agent** (e.g. < ~0.40 with substantial input
  volume) — context isn't being reused.
- **Why it matters:** cache reads are typically priced far below fresh input; a poor ratio
  inflates input spend several-fold.
- **Action:** stabilize system prompts / preamble ordering, batch related turns, and avoid
  cache-busting churn in long-lived sessions.

### 3. Frontier-model overkill

The most expensive failure mode is silent: a frontier reasoning model doing repetitive,
low-stakes work.

- **Flag when:** a **high-cost frontier model** (e.g. `claude-sonnet-4.6`, `claude-opus`,
  `gpt-5`) is invoked for **low-stakes / repetitive** turns — short outputs, cron-driven
  boilerplate, or skills tagged as routine (pings, status checks, simple syncs).
- **Preferred tiers for routine work:** `gemini-3.1-flash-lite` / `gemini-2.5-flash-lite`, or a
  **local Ollama** model (≈ $0 marginal cost) for anything that doesn't need frontier reasoning.
- **Why it matters:** a frontier model can cost 20–40× a flash-lite tier for output of similar
  utility on routine tasks.
- **Action:** re-route the offending skill/channel to a cheaper model group; reserve frontier
  models for genuine deep-reasoning turns.

---

## Scripts

### `scripts/token_tracker.py` — daily compiler

Recursively parses all agents' trajectories for a target date, buckets tokens, computes
micro-dollar costs, attributes turns to skills, and prints a structured daily report (Global
Summary, Usage & Averages by Skill, Agent Breakdown, Model Breakdown, Top 5 Intensive Turns, and
the Resource Leak Audit).

```bash
python3 scripts/token_tracker.py                       # today (America/Los_Angeles)
python3 scripts/token_tracker.py --date 2026-06-22     # a specific day
python3 scripts/token_tracker.py --date 2026-06-22 --tz America/New_York
python3 scripts/token_tracker.py --date 2026-06-22 --save   # also write observability/daily/token_report_<date>.md
```

Skill attribution recognizes bracket trigger tags and falls back to display-name heuristics for
`portfolio-check`, `daily-wake-up`, `repo-sync`, `self-maintenance`, and `process-hygiene`;
untagged interactive turns bucket as `Interactions`.

### `scripts/generate_observability_manifest.py` — weekly aggregator

Aggregates a week of daily trajectory records into a single clean markdown manifest written to
`observability/weekly/`. Imports the price catalog and cost function from `token_tracker.py` so
pricing stays single-sourced.

```bash
python3 scripts/generate_observability_manifest.py                 # previous Mon–Sun week
python3 scripts/generate_observability_manifest.py --date 2026-06-26
python3 scripts/generate_observability_manifest.py --start 2026-06-15 --end 2026-06-21
```

Output: `observability/weekly/observability_manifest_<end-date>.md` with global totals, a daily
breakdown, agent/model breakdowns, week-level leak findings, and the top resource-intensive
turns of the week.

---

## Operating Cadence

Wire `token_tracker.py --save` to a daily cron (after midnight local, targeting the prior day)
to post the report to `#scheduling` and persist it under `observability/daily/`. Wire
`generate_observability_manifest.py` to a weekly cron (Monday) to roll the week up into
`observability/weekly/`. The daily report is the tripwire; the weekly manifest is the trend line.
