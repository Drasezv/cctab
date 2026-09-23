#!/usr/bin/env python3
"""statusline wrapper: saves rate limits, chains to the old statusline"""
import json
import os
import subprocess
import sys
import time

os.umask(0o077)
DATA = os.path.expanduser("~/.cctab")
CACHE = os.path.join(DATA, "limits.json")


def saved_config():
    """statusline gets no plugin env, read config.json"""
    try:
        with open(os.path.join(DATA, "config.json")) as f:
            return json.load(f)
    except Exception:
        return {}


INNER = (os.environ.get("CLAUDE_PLUGIN_OPTION_STATUSLINE_COMMAND", "").strip()
         or saved_config().get("statusline_command", "").strip())


def store(payload):
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        return
    keep = {"captured_at": time.time()}
    for window in ("five_hour", "seven_day"):
        block = limits.get(window)
        if isinstance(block, dict):
            try:
                pct = float(block.get("used_percentage"))
                pct = max(0.0, min(100.0, pct))
            except (TypeError, ValueError):
                pct = None
            keep[window] = {"used_percentage": pct, "resets_at": block.get("resets_at")}
    if len(keep) == 1:
        return
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(keep, f)
    os.replace(tmp, CACHE)


# без этого пользователь потеряет свой statusline
def chain(raw):
    """pass payload to the previous statusline"""
    if not INNER:
        return
    try:
        r = subprocess.run(INNER, shell=True, input=raw, text=True,
                           capture_output=True, timeout=10)
        sys.stdout.write(r.stdout)
    except Exception:
        pass


def own_line(payload):
    """what to show when the user had no statusline of their own"""
    bits = []
    name = ((payload.get("model") or {}).get("display_name") or "").strip()
    if name:
        bits.append(name)
    limits = payload.get("rate_limits") or {}
    for window, label in (("five_hour", "5h"), ("seven_day", "week")):
        block = limits.get(window)
        if not isinstance(block, dict):
            continue
        try:
            bits.append(f"{label} {float(block.get('used_percentage')):.0f}%")
        except (TypeError, ValueError):
            pass
    return "  ".join(bits)


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
    if INNER:
        chain(raw)
        return
    # an empty status bar would look like cctab broke it
    line = own_line(payload)
    if line:
        sys.stdout.write(line)


if __name__ == "__main__":
    main()
