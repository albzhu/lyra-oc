#!/bin/bash
# Double-clickable launcher for the OpenClaw web Console.
# Starts the local server (127.0.0.1, random per-launch token) and opens the
# browser. Close the window or press Ctrl-C to stop.

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"

cd "$DIR" || exit 1
"$PY" oc_web.py
