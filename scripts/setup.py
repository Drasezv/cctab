#!/usr/bin/env python3
"""setup: bot name, token check, statusline wiring"""
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

os.umask(0o077)

TELEGRAM_API = "https://api.telegram.org/bot"
DATA = os.path.expanduser("~/.cctab")


# имя занято почти всегда, поэтому генерим с хвостом
def suggest():
    return f"cctab_{secrets.token_hex(3)}_bot"


TOKEN_SHAPE = re.compile(r"^\d{6,12}:[A-Za-z0-9_-]{30,}$")


def looks_like_a_token(token):
    """cheap sanity check before hitting telegram"""
    if ":" not in token:
        return "that has no colon in it, copy the whole line BotFather sent"
    if any(ch.isspace() for ch in token):
        return "there is a space in there, copy it again without breaks"
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


def claim(code, until):
    """(token, owner, lang, why): why is empty, 'expired' or 'unreachable'"""
    while time.time() < until:
        try:
            with urllib.request.urlopen(CLAIM + code, timeout=10) as r:
                body = json.loads(r.read())
            if body.get("token"):
                return (body["token"], str(body.get("owner") or ""),
                        body.get("lang") or "", "")
        except urllib.error.HTTPError:
            pass                         # 404 until they confirm the window
        except Exception:
            return "", "", "", "unreachable"
        time.sleep(3)
    return "", "", "", "expired"


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


CREATE_TTL = 180         # обе ссылки живут три минуты, дальше пишем, что истекли
PAIRING_TTL = 180
GREETING = ("cctab is connected. I'll write here when a Claude Code task runs "
            "past 30 minutes, with what it cost. /settings changes that.")
GREETING_RU = ("cctab подключён. Напишу сюда, когда задача в Claude Code идёт "
               "дольше 30 минут, и сколько она стоила. /settings, чтобы поменять.")


def config():
    try:
        with open(os.path.join(DATA, "config.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    path = os.path.join(DATA, "config.json")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def save_qr(text, name):
    """png next to the config, opened on a mac so there is nothing to find"""
    try:
        import qrcode
    except ImportError:
        return ""
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    path = os.path.join(DATA, name)
    qrcode.make(text).save(path)
    if sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    return path


def save_chat(chat, lang):
    path = os.path.join(DATA, "state.json")
    try:
        with open(path) as f:
            st = json.load(f)
    except Exception:
        st = {}
    st["chat_id"] = str(chat)
    if lang:
        st["tg_lang"] = lang
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def wait_for_start(token, nonce, until, owner=""):
    """no offset, so the hook still sees these updates afterwards"""
    while time.time() < until:
        try:
            body = json.loads(urllib.request.urlopen(
                f"{TELEGRAM_API}{token}/getUpdates?limit=100&timeout=0", timeout=10).read())
        except Exception:
            body = {}
        for update in body.get("result", []):
            message = update.get("message") or {}
            sender = str((message.get("from") or {}).get("id") or "")
            if owner:
                if sender != owner:
                    continue             # only the person who created the bot
            elif (message.get("text") or "").strip() != f"/start {nonce}":
                continue
            chat = (message.get("chat") or {}).get("id")
            lang = (message.get("from") or {}).get("language_code") or ""
            save_chat(chat, lang)
            call(token, "sendMessage", {
                "chat_id": chat,
                "text": GREETING_RU if lang.startswith("ru") else GREETING})
            return chat
        time.sleep(2)
    return ""


def greet_owner(token, owner, lang):
    """a bot made through the manager may write to its creator first, no Start"""
    if not call(token, "sendMessage", {
            "chat_id": owner,
            "text": GREETING_RU if lang.startswith("ru") else GREETING}):
        return False
    save_chat(owner, lang)
    return True


def print_botfather():
    print("UNREACHABLE the manager did not answer, so here is the long way.")
    print("Open @BotFather and send these three, one after another:\n")
    print("  /newbot")
    print("  cctab notifications")
    print(f"  {suggest()}\n")
    print("It replies with a token. Pass it on stdin:")
    print('  echo "<token>" | setup.py -')


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


def pair_link(token, username, want_qr):
    link = f"https://t.me/{username}?start={arm_pairing()}"
    print(f"PAIR {link}")
    if want_qr:
        path = save_qr(link, "pair.png")
        if path:
            print(f"QR {path}")


def finish(token, want_qr, link=True):
    """a token in hand: check it, dress the bot, hand out the Start link"""
    problem = looks_like_a_token(token)
    if problem:
        print(f"BAD_TOKEN {problem}")
        return 1
    username, problem = whoami(token)
    if problem:
        if "401" in problem:
            problem = "Telegram does not know it, check you pasted the newest one"
        print(f"BAD_TOKEN {problem}")
        return 1
    hooked = webhook_blocks_us(token)
    if hooked:
        print(f"WEBHOOK {hooked} eats our updates, remove it with deleteWebhook")
    remember(token)
    print(f"BOT @{username}")
    dressed = dress_up(token)
    if dressed:
        print(f"DRESSED {', '.join(dressed)}")
    if link:
        pair_link(token, username, want_qr)
    return 0


def main():
    args = sys.argv[1:]
    want_qr = "--qr" in args
    args = [a for a in args if a != "--qr"]
    step = args[0] if args else "status"

    if step == "--statusline":
        print(wire_statusline(args[1] if len(args) > 1 else WRAPPER))
        return 0

    if step == "status":
        token = config().get("bot_token")
        username, problem = whoami(token) if token else ("", "none")
        if problem:
            print("NO_BOT")
            return 0
        try:
            with open(os.path.join(DATA, "state.json")) as f:
                paired = bool(json.load(f).get("chat_id"))
        except Exception:
            paired = False
        how = "owner" if config().get("owner_id") else "link"
        print(f"READY @{username}" if paired else f"NOT_PAIRED @{username} {how}")
        return 0

    if step == "link":
        code = secrets.token_hex(3)
        cfg = config()
        cfg["create_code"] = code
        cfg["create_until"] = int(time.time()) + CREATE_TTL
        save_config(cfg)
        link = managed_link(code)
        print(f"LINK {link}")
        if want_qr:
            path = save_qr(link, "create.png")
            if path:
                print(f"QR {path}")
        return 0

    if step == "wait":
        cfg = config()
        if not cfg.get("create_code"):
            print("NO_LINK run setup.py link first")
            return 1
        token, owner, lang, why = claim(cfg["create_code"], float(cfg.get("create_until", 0)))
        if why == "expired":
            print("EXPIRED nobody confirmed the window within 3 minutes")
            return 2
        if why == "unreachable":
            print_botfather()
            return 3
        if not owner:
            return finish(token, want_qr)       # older manager: fall back to a Start link
        failed = finish(token, want_qr, link=False)
        if failed:
            return failed
        cfg = config()
        cfg["owner_id"] = owner
        save_config(cfg)
        if greet_owner(token, owner, lang):
            print("CONNECTED")
            return 0
        # Telegram would not let the bot speak first, so a Start is needed after all
        print("START the bot could not write first, waiting for Start in its chat")
        if not wait_for_start(token, "", time.time() + PAIRING_TTL, owner=owner):
            print("EXPIRED nobody pressed Start within 3 minutes")
            return 2
        print("CONNECTED")
        return 0

    if step == "relink":
        token = config().get("bot_token")
        username, problem = whoami(token) if token else ("", "none")
        if problem:
            print("NO_BOT")
            return 1
        pair_link(token, username, want_qr)
        return 0

    if step == "pair":
        cfg = config()
        owner = cfg.get("owner_id", "")
        if not cfg.get("bot_token") or not (owner or cfg.get("pair_nonce")):
            print("NO_BOT run setup.py wait first")
            return 1
        if owner and greet_owner(cfg["bot_token"], owner, ""):
            print("CONNECTED")
            return 0
        # with the owner known a plain Start is enough, and the clock starts now
        until = time.time() + PAIRING_TTL if owner else float(cfg.get("pair_until", 0))
        if not wait_for_start(cfg["bot_token"], cfg.get("pair_nonce", ""), until, owner=owner):
            print("EXPIRED nobody pressed Start within 3 minutes")
            return 2
        print("CONNECTED")
        return 0

    if step == "-":
        # an argument is visible in `ps` and lands in the shell history
        return finish(sys.stdin.readline().strip(), want_qr)
    return finish(step, want_qr)


if __name__ == "__main__":
    sys.exit(main())
