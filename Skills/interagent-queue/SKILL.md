---
name: interagent-queue
description: Monitor and stream the Message-in-a-Bottle (MIAB) transaction ledger. Converts raw callback log events (create, forward, return, resolve, fail) into human-readable notifications and pipes them to Discord. Use when tracking callback execution trajectories, toggling the stream, checking cursor state, or running manual/peek sweeps of the active transaction queue.
---

# Interagent Queue — Asynchronous Transaction Observer

This skill governs the transaction streaming, processing, formatting, and Discord delivery for the **Message-in-a-Bottle (MIAB) LIFO Callback Stack**. It decouples the observer and human-readability layers from the core `miab-broker` protocol.

---

## 1. What the Interagent Queue Observer Does

The observer (`scripts/interagent_queue.py`) is a transaction monitor that tails the append-only callback events log (`state/callbacks/ledger.jsonl`). It parses events in real-time or via frequent interval sweeps, matches logical agent references (like `main`, `planner`, `coder`, or `reviewer`) with friendly icons, and formats them into clean, compact, human-readable notifications.

If configured and enabled, these structured notifications are piped straight into the designated `#scheduling` Discord channel (<#1517433532518109195>) using the openclaw alerting framework.

---

## 2. Invocations & Commands

The utility script `interagent_queue.py` can be driven from the CLI to enable/disable sweeps, check cursor tracking status, or run isolated manual analysis:

```bash
# Toggle the pipeline streams
python3 Skills/interagent-queue/scripts/interagent_queue.py on
python3 Skills/interagent-queue/scripts/interagent_queue.py off

# Check cursor status, live state file, and target ledger
python3 Skills/interagent-queue/scripts/interagent_queue.py status

# Manually process and sweep all un-processed ledger records
python3 Skills/interagent-queue/scripts/interagent_queue.py process

# Peek at new ledger records inside stdout WITHOUT updating your cursor or piping to Discord
python3 Skills/interagent-queue/scripts/interagent_queue.py peek
```

---

## 3. Storage & State Management

To decouple concerns and ensure multi-platform flexibility (e.g. running under separate folders/accounts/containers like `/Users/lyramini` or `/Users/albertzhu`), all operational locations resolve dynamically relative to home environments:

- **State Document:** `$LYRA_WORKSPACE/state/callbacks/queue_state.json` tracks cursor indexing (`last_processed_line`) and live enabled status.
- **Active Ledger Source:** `$CLAW_HOME/state/callbacks/ledger.jsonl`.
- **Primary Discord Channel:** `#scheduling` (`channel:1517433532518109195`).

---

## Quick Reference

```
on      → enable automated sweeps and active Discord alerting
off     → disable Discord alerting (silent logging)
status  → view live cursor count and active environment parameters
process → sweep the ledger, post new events to Discord, and advance cursor
peek    → inspect new transaction events on stdout without affecting the cursor
```
