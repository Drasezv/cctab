#!/usr/bin/env python3
"""SessionStart: tell Claude when cctab has nowhere to send anything."""
import json
import os

DATA = os.path.expanduser("~/.cctab")


def option(name):
    live = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}", "").strip()
    if live:
        return live
    try:
        with open(os.path.join(DATA, "config.json")) as f:
            return str(json.load(f).get(name, "")).strip()
    except Exception:
        return ""


def paired():
    try:
        with open(os.path.join(DATA, "state.json")) as f:
            return bool(json.load(f).get("chat_id"))
    except Exception:
        return False


def main():
    if not option("bot_token"):
        print("cctab is installed but has no Telegram bot yet, so no notification "
              "can arrive. If the user brings up notifications, or wonders why a "
              "finished task sent nothing, offer the cctab setup skill.")
    elif not (option("chat_id") or paired()):
        print("cctab has a bot token, but nobody has opened its pairing link yet, "
              "so it has no chat to write to. The cctab setup skill prints the "
              "link and the QR code again.")


if __name__ == "__main__":
    main()
