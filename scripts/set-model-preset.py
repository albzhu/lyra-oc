#!/usr/bin/env python3
"""Patches openclaw-template.json to set primary model and fallbacks for a chosen provider.

Usage: python3 set-model-preset.py [gemini|claude|openai]
"""
import sys
import json
from pathlib import Path

PRESETS = {
    "gemini": {
        "primary": "openrouter/google/gemini-3-flash-preview",
        "fallbacks": [
            "openrouter/google/gemini-3.5-flash",
            "openrouter/google/gemini-3.1-flash-lite",
            "openrouter/anthropic/claude-sonnet-4.6",
            "openrouter/openai/gpt-5",
        ],
    },
    "claude": {
        "primary": "openrouter/anthropic/claude-sonnet-4.6",
        "fallbacks": [
            "openrouter/anthropic/claude-opus-4.8",
            "openrouter/google/gemini-3.5-flash",
            "openrouter/google/gemini-3.1-flash-lite",
            "openrouter/openai/gpt-5",
        ],
    },
    "openai": {
        "primary": "openrouter/openai/gpt-5",
        "fallbacks": [
            "openrouter/anthropic/claude-sonnet-4.6",
            "openrouter/google/gemini-3.5-flash",
            "openrouter/google/gemini-3.1-flash-lite",
        ],
    },
}

def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PRESETS:
        print(f"Usage: {sys.argv[0]} [gemini|claude|openai]", file=sys.stderr)
        sys.exit(1)

    preset_name = sys.argv[1]
    preset = PRESETS[preset_name]

    template_path = Path(__file__).parent.parent / "openclaw-template.json"
    if not template_path.exists():
        print(f"error: {template_path} not found", file=sys.stderr)
        sys.exit(1)

    config = json.loads(template_path.read_text())

    # Patch global defaults
    config.setdefault("agents", {}).setdefault("defaults", {})["model"] = {
        "primary": preset["primary"],
        "fallbacks": preset["fallbacks"],
    }

    # Patch main agent specifically (it's the user-facing orchestrator)
    for agent in config.get("agents", {}).get("list", []):
        if agent.get("id") == "main":
            agent["model"] = {
                "primary": preset["primary"],
                "fallbacks": preset["fallbacks"],
            }
            break

    template_path.write_text(json.dumps(config, indent=2) + "\n")
    print(f"  Model preset applied: {preset_name} (primary: {preset['primary']})")

if __name__ == "__main__":
    main()
