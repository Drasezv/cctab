#!/usr/bin/env python3
"""setup: bot name, token check, statusline wiring"""
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

os.umask(0o077)

TELEGRAM_API = "https://api.telegram.org/bot"
DATA = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.expanduser("~/.cctab")


# имя занято почти всегда, поэтому генерим с хвостом
def suggest():
    return f"cctab_{secrets.token_hex(3)}_bot"


def qr(text):
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


TOKEN_SHAPE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


def looks_like_a_token(token):
    """cheap sanity check before hitting telegram"""
    if ":" not in token:
        return "that has no colon in it — copy the whole line BotFather sent"
    if any(ch.isspace() for ch in token):
        return "there is a space in there — copy it again without breaks"
    if not TOKEN_SHAPE.match(token):
        return "that is not the shape of a token (digits, a colon, then letters)"
    return ""


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
    """a webhook would eat getUpdates"""
    try:
        body = json.loads(urllib.request.urlopen(
            f"{TELEGRAM_API}{token}/getWebhookInfo", timeout=10).read())
    except Exception:
        return ""
    return ((body.get("result") or {}).get("url") or "")


def remember(token):
    """save token to config.json"""
    os.makedirs(DATA, mode=0o700, exist_ok=True)
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


MANAGER = "cctab_manager_bot"
CLAIM = "https://helsinki.lunace.ru/cctab/claim/"


def managed_link(code):
    """the window where the username and the name are already filled in"""
    return f"https://t.me/newbot/{MANAGER}/cctab_{code}_bot?name=cctab"


def claim(code, seconds=180):
    """our side of the handover: the manager leaves the token under the code"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(CLAIM + code, timeout=10) as r:
                body = json.loads(r.read())
            if body.get("token"):
                return body["token"]
        except urllib.error.HTTPError:
            pass                         # 404 until they finish the window
        except Exception:
            return ""                    # nothing listening, take the long way
        time.sleep(3)
    return ""


AVATAR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "avatar.jpg")
COMMANDS = [("settings", "thresholds, messages, spend"),
            ("spend", "what every tab cost")]


def call(token, method, payload):
    data = urllib.parse.urlencode(payload).encode()
    try:
        body = json.loads(urllib.request.urlopen(
            f"{TELEGRAM_API}{token}/{method}", data=data, timeout=10).read())
        return bool(body.get("ok"))
    except Exception:
        return False


def upload_photo(token, path):
    """setMyProfilePhoto wants multipart, so build one by hand"""
    line = "--" + secrets.token_hex(16)
    head = (f'{line}\r\nContent-Disposition: form-data; name="photo"\r\n\r\n'
            '{"type":"static","photo":"attach://pic"}\r\n'
            f'{line}\r\nContent-Disposition: form-data; name="pic"; filename="a.jpg"\r\n'
            "Content-Type: image/jpeg\r\n\r\n")
    try:
        with open(path, "rb") as f:
            blob = head.encode() + f.read() + f"\r\n{line}--\r\n".encode()
        req = urllib.request.Request(
            f"{TELEGRAM_API}{token}/setMyProfilePhoto", data=blob,
            headers={"Content-Type": f"multipart/form-data; boundary={line[2:]}"})
        return bool(json.loads(urllib.request.urlopen(req, timeout=20).read()).get("ok"))
    except Exception:
        return False


def dress_up(token):
    """name, blurbs, commands and the picture, so BotFather is not needed for them"""
    done = []
    if call(token, "setMyName", {"name": "cctab"}):
        done.append("name")
    if call(token, "setMyShortDescription", {
            "short_description": "Says when a long Claude Code task is done, "
                                 "and what it cost."}):
        done.append("blurb")
    if call(token, "setMyDescription", {
            "description": "I write here when a task in Claude Code has been "
                           "running long enough to be worth telling you about: "
                           "what it was, what it cost, and how much of your rate "
                           "limit is left. Send /settings to change any of that."}):
        done.append("about")
    if call(token, "setMyCommands", {"commands": json.dumps(
            [{"command": c, "description": d} for c, d in COMMANDS])}):
        done.append("commands")
    if os.path.exists(AVATAR) and upload_photo(token, AVATAR):
        done.append("picture")
    return done


PAIRING_TTL = 900        # ссылка живёт столько же, сколько окно установки


def arm_pairing():
    """one-shot payload for the link, so a stranger cannot claim the chat"""
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    path = os.path.join(DATA, "config.json")
    try:
        saved = json.load(open(path))
    except Exception:
        saved = {}
    nonce = secrets.token_urlsafe(9)
    saved["pair_nonce"] = nonce
    saved["pair_until"] = int(time.time()) + PAIRING_TTL
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(saved, f)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    return nonce


SETTINGS = os.path.expanduser("~/.claude/settings.json")
# path changes with every plugin version, so take it from __file__
WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "statusline.py")


def saved_statusline():
    try:
        return json.load(open(os.path.join(DATA, "config.json"))).get("statusline_command", "")
    except Exception:
        return ""


def wire_statusline(wrapper_path):
    """put our wrapper in statusLine, remember the old one"""
    try:
        with open(SETTINGS) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        cfg = {}
    except Exception as err:
        return f"settings.json is unreadable ({err}), left alone"

    if not os.path.exists(os.path.expanduser(wrapper_path)):
        return f"no such file: {wrapper_path}. Nothing was changed."
    current = cfg.get("statusLine") or {}
    existing = (current.get("command") or "").strip()
    if wrapper_path in existing:
        return "already in place"
    if "statusline.py" in existing and "cctab" in existing:
        # a wrapper from an older install: swap it, do not chain into it
        existing = saved_statusline() or ""

    path = os.path.join(DATA, "config.json")
    try:
        saved = json.load(open(path))
    except Exception:
        saved = {}
    if existing:
        saved["statusline_command"] = existing
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(saved, f)
    os.replace(tmp, path)

    cfg["statusLine"] = {"type": "command", "command": wrapper_path}
    tmp = SETTINGS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SETTINGS)
    return ("wired, and your old statusline still runs after ours"
            if existing else "wired")


def main():
    if "--statusline" in sys.argv:
        where = sys.argv[sys.argv.index("--statusline") + 1] \
            if len(sys.argv) > sys.argv.index("--statusline") + 1 else WRAPPER
        print(wire_statusline(where))
        return
    token = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
    if token == "-":
        # an argument is visible in `ps` and lands in the shell history
        token = sys.stdin.readline().strip()
    if not token:
        code = secrets.token_hex(3)
        link = managed_link(code)
        print("Open this, or scan it with your phone, and confirm the window:\n")
        print(f"  {link}\n")
        art = qr(link)
        if art:
            print(art)
        print("The name and the username are filled in already. Waiting for it...")
        token = claim(code)
    if not token:
        name = suggest()
        print("\nNothing came back, so here is the long way.")
        print("Open @BotFather and send these three, one after another:\n")
        print("  /newbot")
        print("  cctab notifications")
        print(f"  {name}\n")
        print("It replies with a token. Run this again with the token to finish:")
        print("  setup.py <token>")
        return

    problem = looks_like_a_token(token)
    if problem:
        print(f"That token will not do: {problem}")
        return

    username, problem = whoami(token)
    if problem:
        if "401" in problem:
            problem = "Telegram does not know it — check you pasted the newest one"
        print(f"Telegram would not take that token: {problem}")
        return
    hooked = webhook_blocks_us(token)
    if hooked:
        print(f"Heads up: this bot has a webhook at {hooked}")
        print("Updates go there instead of to us. Remove it with deleteWebhook first.\n")

    remember(token)
    dressed = dress_up(token)
    link = f"https://t.me/{username}?start={arm_pairing()}"
    print(f"Bot is alive: @{username}\n")
    if dressed:
        print(f"Set for you, no BotFather needed: {', '.join(dressed)}.\n")
    print(f"Open {link} and press Start.")
    print("The link carries a one-off code and stops working in 15 minutes, so")
    print("only whoever opens it becomes the chat cctab writes to.\n")
    art = qr(link)
    if art:
        print(art)
    print("Token saved, so this already works. To keep it in your system keychain")
    print("instead of a file, paste it into the plugin's bot_token setting.\n")
    print("One more thing, for the rate-limit numbers:")
    print("  setup.py --statusline")
    print("It puts cctab in the statusline slot — the only place Claude Code")
    print("hands limits to — and keeps your own statusline running after it.")


if __name__ == "__main__":
    main()
