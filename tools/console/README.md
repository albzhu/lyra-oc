# OpenClaw Config Tools

Two UIs over the same shared core (`oc_config.py`) for editing OpenClaw config
without hand-editing JSON. Both edit `openclaw-template.json` and restart via
`make cold-restart`.

- **`OpenClaw-Console.command`** — a local **web app** (recommended for the
  richer tabs). Python stdlib server bound to `127.0.0.1` with a per-launch
  token; opens in your browser. Tabs: **Models**, **Agents**, **Reports**,
  **Terminal** — all working. No build step, no dependencies.

  The **Reports** tab reads the daily `observability/daily/token_report_*.md`
  files (cost / tokens / per-model / per-agent), runs live API health probes
  (independent of the cron health-check, so it won't disturb its log-scan
  offsets), and scans logs for recent errors + restarts. Buttons regenerate a
  day's token report (`token_tracker.py`), write a combined
  `observability_<date>.md`, or schedule a daily run via `openclaw cron add`.
  The standalone generator is `observability_report.py`.

  The **Terminal** tab is a low-volume **gateway configuration assistant**: a
  direct streaming LLM chat (via your `OPENROUTER_API_KEY` from `.env`) primed
  with the OpenClaw schema rules and a secret-free summary of your live config.
  It's advisory — it helps you reason about and compose gateway config; apply
  actual changes in the Models/Agents tabs. (Agent natural-language replies
  aren't recoverable locally — they're delivered out to channels via the
  `message` tool — so the assistant is a dedicated helper rather than a relay to
  an existing gateway agent.)
- **`OpenClaw-Configurator.command`** — the original **AppleScript-dialog** tool,
  kept for quick keyboard-free model edits. Same Models + Agents capabilities.

The sections below document the dialog tool; the web console mirrors it.

---

## OpenClaw Model Config (dialog tool)

A lightweight macOS utility for changing which models each OpenClaw agent uses —
its **primary** model and its ordered **fallbacks** — without touching JSON by
hand.

## How to use

1. Double-click **`OpenClaw-Configurator.command`**.
   *(First time only: right-click → Open to clear Gatekeeper, or run
   `chmod +x` on the two files — see below.)*
2. Pick a tab:
   - **🧠 Models** — pick an agent (`defaults (global)`, `main`, etc.), then
     **Change Primary** or **Edit Fallbacks** (add, remove, or move to a chosen
     position — the rest shift to make room). Adding a model (or typing a custom
     id) lets you set an optional **alias** (a short nickname).
   - **👥 Agents** — **create** a new agent (minimal seed: the defaults' primary
     with empty fallbacks; flesh it out in the Models tab), **edit** its name /
     workspace / models, **rename** its id, or **remove** it.
3. Choose **💾 Save & Restart gateway**.

### Agents tab notes

- **Create** adds an entry to `agents.list` and scaffolds a workspace folder at
  `~/.openclaw/workspace-<id>` (editable at the prompt) seeded with starter
  persona files — `IDENTITY.md`, `SOUL.md`, `AGENTS.md`, `TOOLS.md`, `USER.md`,
  `HEARTBEAT.md`, and `.gitignore`. Existing files are never overwritten. The
  gateway provisions the agent's data folder (`agents/<id>/`) on the next
  restart — no manual setup needed.
- **Remove** also deletes any Discord **bindings** that target the agent (it
  shows you exactly which ones first), so no orphaned `agentId` references are
  left behind.
- **Rename id** repoints those bindings to the new id and updates the agent's
  `agentDir` path if it followed the `agents/<id>/` convention.

On save, the tool writes your changes and runs **`make cold-restart`** from
`~/.openclaw` in the background, then reports success or any error in a dialog.
(`cold-restart` is `sync down up` with no `logs --follow`, so it returns cleanly
instead of hanging on a log tail.)

## What it edits, and why

It edits **`openclaw-template.json`**, *not* `openclaw.json`.

`make restart` runs `make sync` first, and `sync` (`scripts/sync-env.sh`)
**regenerates `openclaw.json` from the template** (injecting `env:` secrets).
Any direct edit to `openclaw.json` would be overwritten on the next restart, so
the template is the only correct place to make a durable change.

## File-manipulation approach (and why not plain `jq -i`)

This is a slightly hardened version of the `discord-add-channel` skill's
`modify_json.py` pattern:

- **Listing** the current primary/fallbacks uses **`jq`** (as requested). The
  in-memory config is piped to `jq` over stdin, so the display always reflects
  your *pending* edits, not just what's on disk.
- **Writing** uses **stage → validate → backup → atomic replace**:
  1. Write the new config to a sibling `openclaw-template_staged.json`.
  2. `json.loads` it to prove it parses before committing.
  3. Copy the current template into `openclaw-backups/` with a timestamp.
  4. `os.replace()` the staged file over the template — an **atomic** rename on
     the same filesystem.

Why not edit in place with `jq -i` / a redirect? `jq '...' file > file` truncates
the file *before* jq writes, so a crash or a bad filter can leave the config
half-written or empty. The staged-and-replace flow guarantees the template is
either the old valid file or the new valid file — never a corrupt in-between —
and always leaves a backup.

## Requirements

- macOS (uses built-in `osascript` for the dialogs)
- `python3` (system Python is fine)
- `jq` — install with `brew install jq` if missing

## Make the files executable (one-time)

```bash
chmod +x ~/.openclaw/tools/console/*.command
chmod +x ~/.openclaw/tools/console/*.py
```

## Safety notes

- Nothing is written until you choose **Save**; quitting discards edits.
- Every save leaves a timestamped backup in `~/.openclaw/openclaw-backups/`.
- If the gateway fails to start after a change, restore the newest
  `openclaw-template.json.uiedit.*` backup and run `make restart`.
