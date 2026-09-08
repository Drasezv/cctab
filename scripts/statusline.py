#!/usr/bin/env python3
"""Statusline wrapper.

Claude Code hands rate limits to the statusline command and nowhere else,
hooks never see them. So we sit in that slot, keep a copy of the numbers, and
hand the payload on to whatever statusline the user already had.
"""
import json
import os
import subprocess
import sys
import time

DATA = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.cctab")
CACHE = os.path.join(DATA, "limits.json")


def saved_config():
    """Claude Code does not pass plugin options to the statusline slot — only
       hooks get those — so anything we need has to be read from disk."""
    try:
        with open(os.path.join(DATA, "config.json")) as f:
            return json.load(f)
    except Exception:
        return {}


INNER = (os.environ.get("CLAUDE_PLUGIN_OPTION_STATUSLINE_COMMAND", "").strip()
         or saved_config().get("statusline_command", "").strip())


def store(payload):
    limits = payload.get("rate_limits")
    if not isinstance(limits, dict):
        return
    keep = {"captured_at": time.time()}
    for window in ("five_hour", "seven_day"):
        block = limits.get(window)
        if isinstance(block, dict):
            keep[window] = {
                "used_percentage": block.get("used_percentage"),
                "resets_at": block.get("resets_at"),
            }
    if len(keep) == 1:
        return
    os.makedirs(DATA, exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(keep, f)
    os.replace(tmp, CACHE)


def chain(raw):
    """Hand the untouched payload to the statusline the user configured before us."""
    if not INNER:
        return
    try:
        r = subprocess.run(INNER, shell=True, input=raw, text=True,
                           capture_output=True, timeout=10)
        sys.stdout.write(r.stdout)
    except Exception:
        pass


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception:
        chain(raw)
        return
    try:
        store(payload)
    except OSError:
        pass
    chain(raw)


if __name__ == "__main__":
    main()
