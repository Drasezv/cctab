#!/usr/bin/env python3
"""Walks a new user from nothing to a working bot.

The slow part of BotFather has never been the commands, it is inventing a
username nobody has taken. So we hand one over, already free.
"""
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request

TELEGRAM_API = "https://api.telegram.org/bot"
DATA = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.cctab")


def suggest():
    return f"cctab_{secrets.token_hex(3)}_bot"


def qr(text):
    """A QR block, but only if the user happens to have the library.

    The link alone is one click on a desktop, so this stays a bonus and never
    a dependency.
    """
    try:
        import qrcode
    except ImportError:
        return ""
    code = qrcode.QRCode(border=1)
    code.add_data(text)
    code.make(fit=True)
    grid = code.get_matrix()
    lines = []
    for y in range(0, len(grid), 2):
        row = ""
        for x in range(len(grid[y])):
            top = grid[y][x]
            bottom = grid[y + 1][x] if y + 1 < len(grid) else False
            row += {(True, True): " ", (True, False): "▄",
                    (False, True): "▀", (False, False): "█"}[(top, bottom)]
        lines.append(row)
    return "\n".join(lines)


def whoami(token):
    try:
        body = json.loads(urllib.request.urlopen(
            f"{TELEGRAM_API}{token}/getMe", timeout=10).read())
    except Exception as err:
        return "", str(err)
    if not body.get("ok"):
        return "", body.get("description", "token refused")
    return (body.get("result") or {}).get("username", ""), ""


def webhook_blocks_us(token):
    """getUpdates goes silent when a webhook is set, and says nothing about it."""
    try:
        body = json.loads(urllib.request.urlopen(
            f"{TELEGRAM_API}{token}/getWebhookInfo", timeout=10).read())
    except Exception:
        return ""
    return ((body.get("result") or {}).get("url") or "")


def remember(token):
    """Keep the token so the plugin works before anyone edits settings."""
    os.makedirs(DATA, exist_ok=True)
    path = os.path.join(DATA, "config.json")
    try:
        saved = json.load(open(path))
    except Exception:
        saved = {}
    saved["bot_token"] = token
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(saved, f)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def main():
    token = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if not token:
        name = suggest()
        print("Open @BotFather and send these three, one after another:\n")
        print("  /newbot")
        print("  cctab notifications")
        print(f"  {name}\n")
        print("It replies with a token. Run this again with the token to finish:")
        print("  setup.py <token>")
        return

    username, problem = whoami(token)
    if problem:
        print(f"Telegram would not take that token: {problem}")
        return
    hooked = webhook_blocks_us(token)
    if hooked:
        print(f"Heads up: this bot has a webhook at {hooked}")
        print("Updates go there instead of to us. Remove it with deleteWebhook first.\n")

    remember(token)
    link = f"https://t.me/{username}?start=cctab"
    print(f"Bot is alive: @{username}\n")
    print(f"Open {link} and press Start.")
    print("Whatever you send first tells cctab where to reach you.\n")
    art = qr(link)
    if art:
        print(art)
    print("Token saved, so this already works. To keep it in your system keychain")
    print("instead of a file, paste it into the plugin's bot_token setting.")


if __name__ == "__main__":
    main()
