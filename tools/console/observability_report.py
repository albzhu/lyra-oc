#!/usr/bin/env python3
"""
observability_report.py — generate the combined daily observability report.

Writes ~/.openclaw/workspace/observability/daily/observability_<date>.md from:
  - the day's token_report (regenerated via token_tracker.py if missing),
  - live API health probes,
  - a recent errors + restarts log scan.

Run by the `Daily Observability Report` gateway cron job, or on demand:
    python3 observability_report.py [--date YYYY-MM-DD] [--no-probes]

Exit code is 1 if any health probe failed (so a cron wrapper can alert),
0 otherwise.
"""
import argparse
import sys
from datetime import datetime

import oc_config as C


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD (default: today)")
    ap.add_argument("--no-probes", action="store_true", help="skip live API probes")
    args = ap.parse_args()
    date = args.date or datetime.now().strftime("%Y-%m-%d")

    # Make sure the day's token report exists; regenerate if absent.
    if C.read_token_report(date) is None:
        try:
            C.regenerate_token_report(date)
        except Exception as e:
            print(f"warning: could not regenerate token report: {e}", file=sys.stderr)

    probes = [] if args.no_probes else C.health_probes()
    path = C.generate_observability(date, run_probes=not args.no_probes)
    print(f"wrote {path}")

    failed = [p["name"] for p in probes if not p["ok"]]
    if failed:
        print("PROBE FAILURES: " + ", ".join(failed))
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
