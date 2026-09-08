#!/usr/bin/env python3
"""Telegram notifications for Claude Code. Stop and Notification hooks."""
import glob
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

if os.environ.get("CC_TG_NOTIFY_CHILD"):
    sys.exit(0)

os.umask(0o077)          # what we write is nobody else's business
HOME = os.path.expanduser("~")
DATA = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(HOME, ".cctab")
STATE_FILE = os.path.join(DATA, "state.json")
LOG_FILE = os.path.join(DATA, "cctab.log")
LIMITS_CACHE = os.path.join(DATA, "limits.json")
CLAUDE_BIN = os.path.join(HOME, ".local/bin/claude")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def option(name, fallback=""):
    return os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}", "").strip() or fallback


def flag(name):
    return option(name).lower() in ("1", "true", "yes", "on")


def saved_config():
    try:
        with open(os.path.join(DATA, "config.json")) as f:
            return json.load(f)
    except Exception:
        return {}


BOT_TOKEN = option("bot_token") or saved_config().get("bot_token", "")
CHAT_ID = option("chat_id")
USE_API = flag("use_usage_api")
NAME_TASKS = flag("name_tasks")

try:
    MIN_SECONDS = int(option("min_seconds", "1800"))
except ValueError:
    MIN_SECONDS = 1800

LIMITS_TTL = 3600
API_TTL = 180
NOTIFY_COOLDOWN = 60
LIMIT_STEPS = (80, 95)

TELEGRAM_API = "https://api.telegram.org/bot"

PRICES = {"opus": (5.0, 25.0), "sonnet": (3.0, 15.0), "haiku": (1.0, 5.0), "fable": (10.0, 50.0)}

TITLE_PROMPT = (
    "Below is how a working session began. Name the thing being worked on. "
    "This is a label for a phone notification, so the person can tell one of "
    "their parallel tasks from another.\n"
    "2-5 words, no quotes, no trailing period, and skip filler words like "
    "'task', 'setup' or 'building'. Reply with the label only.\n\n"
)


def chat_id():
    """Where to send. Configured value wins; otherwise the first person who
    wrote to the bot, remembered once so we stop asking Telegram."""
    if CHAT_ID:
        return CHAT_ID
    st = state()
    if st.get("chat_id"):
        return st["chat_id"]
    if not BOT_TOKEN:
        return ""
    try:
        raw = urllib.request.urlopen(
            f"{TELEGRAM_API}{BOT_TOKEN}/getUpdates?limit=1&timeout=0", timeout=10).read()
        body = json.loads(raw)
    except Exception as err:
        log(f"getUpdates failed: {err}")
        return ""
    if not body.get("ok"):
        log(f"getUpdates refused: {str(body)[:160]}")
        return ""
    for update in body.get("result", []):
        found = ((update.get("message") or {}).get("chat") or {}).get("id")
        if found:
            st["chat_id"] = str(found)
            save_state(st)
            log(f"chat captured: {found}")
            return str(found)
    log("nobody has messaged the bot yet")
    return ""


def send(text):
    token, chat = BOT_TOKEN, chat_id()
    if not token or not chat:
        return
    body = urllib.parse.urlencode({
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"{TELEGRAM_API}{token}/sendMessage", data=body)
    try:
        answer = json.loads(urllib.request.urlopen(req, timeout=10).read())
        log(f"sent, id={answer.get('result', {}).get('message_id')}")
    except Exception as err:
        detail = err.read().decode()[:300] if hasattr(err, "read") else str(err)
        log(f"send failed: {detail}")


def log(line):
    stamp = datetime.now().strftime("%d.%m %H:%M:%S")
    try:
        with open(LOG_FILE, "a") as f:
            f.write(f"{stamp}  {line}\n")
    except OSError:
        pass


def state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        # A half-written file must not read as "offset 0": Telegram would then
        # replay the whole backlog, including yesterday's approval taps.
        try:
            os.replace(STATE_FILE, STATE_FILE + ".bad")
            log("state file was unreadable, moved aside")
        except OSError:
            pass
        return {"offset": -1}


def save_state(s):
    os.makedirs(os.path.dirname(STATE_FILE), mode=0o700, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


SECRET_RE = re.compile(
    r"(?i)("
    r"sk-[A-Za-z0-9_-]{16,}"                       # OpenAI-shaped keys
    r"|gh[pousr]_[A-Za-z0-9]{20,}"                 # GitHub tokens
    r"|xox[baprs]-[\w-]{10,}"                      # Slack
    r"|AKIA[0-9A-Z]{16}"                           # AWS access key id
    r"|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{6,}"       # JWT
    r"|\d{8,10}:AA[\w-]{30,}"                      # Telegram bot token
    r"|(?:password|passwd|token|secret|api[_-]?key|bearer)\s*[=:]\s*\S+"
    r"|://[^/\s:@]+:[^@\s]+@"                      # user:pass in a URL
    r")")


def redact(text):
    """Commands, errors and answers all travel to a chat that lives on someone
       else's servers forever. A key pasted into a command must not go with
       them."""
    return SECRET_RE.sub("[redacted]", str(text))


def esc(s):
    s = redact(s)
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def num(n):
    return f"{int(n):,}".replace(",", " ")


def dur(sec, short=False):
    """Short form is for tight spots (a bar label); long form reads as prose.
       Units come from the table, or a Russian message ends in '36s'."""
    sec = int(sec)
    if sec < 60:
        return f"{sec}{t('u_s')}" if short else f"{sec} {t('u_sec')}"
    if sec < 3600:
        if short:
            return f"{sec // 60}{t('u_m')} {sec % 60}{t('u_s')}"
        return f"{sec // 60} {t('u_min')} {sec % 60} {t('u_sec')}"
    if sec < 48 * 3600:
        if short:
            return f"{sec // 3600}{t('u_h')} {(sec % 3600) // 60}{t('u_m')}"
        return f"{sec // 3600} {t('u_hr')} {(sec % 3600) // 60} {t('u_min')}"
    if short:
        return f"{sec // 86400}{t('u_d')} {(sec % 86400) // 3600}{t('u_h')}"
    return f"{sec // 86400} {t('u_day')} {(sec % 86400) // 3600} {t('u_hr')}"


def bar(pct, width=10):
    try:
        pct = float(pct or 0)
    except (TypeError, ValueError):
        pct = 0
    pct = max(0, min(100, pct))
    return "▰" * int(round(pct / 100 * width)) + "▱" * (width - int(round(pct / 100 * width)))


def left(when):
    """Time until a reset, given either an epoch second or an ISO string."""
    if when is None or when == "":
        return "?"
    try:
        if isinstance(when, (int, float)):
            target = datetime.fromtimestamp(float(when), timezone.utc)
        else:
            target = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        return "?"
    sec = (target - datetime.now(timezone.utc)).total_seconds()
    return dur(sec, short=True) if sec > 0 else t("any_moment")


def model_name(model_id):
    m = (model_id or "").replace("claude-", "").replace("-", " ")
    return m.title() if m else "unknown model"


def price_for(model_id):
    for key, val in PRICES.items():
        if key in (model_id or ""):
            return val
    return PRICES["opus"]


def parse(path):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return rows


def user_text(row):
    content = (row.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text")
    return ""


def last_request(rows):
    """Text and timestamp of the last real thing the user asked for."""
    for row in reversed(rows):
        if row.get("type") != "user" or row.get("isSidechain"):
            continue
        text = user_text(row).strip()
        if text and not text.startswith("<"):
            return text, row.get("timestamp")
    return "", None


def title_context(rows):
    """How the session opened, for naming it."""
    asks = []
    for row in rows:
        if row.get("type") != "user" or row.get("isSidechain"):
            continue
        text = user_text(row).strip()
        if text and not text.startswith("<"):
            asks.append(text)
    asks = [a for a in asks if len(a) > 25]
    if not asks:
        return ""
    return "\n\n".join(asks[:2])[:1400]


def error_text(rows):
    """What actually went wrong, in Claude Code's own words. A failure message
       that only says "failed" sends the person back to the terminal to find
       out whether it was the rate limit, the network or their own code."""
    for row in reversed(rows[-8:]):
        if not row.get("isApiErrorMessage"):
            continue
        found = user_text(row).strip()
        if found:
            return found
    return ""


def tally(rows, since):
    res = {"all": {}, "turn": {}, "model": "", "error": False}
    seen = set()
    for row in rows[-5:]:
        if row.get("isApiErrorMessage") and (not since or (row.get("timestamp") or "") >= since):
            res["error"] = True

    for row in rows:
        if row.get("type") != "assistant":
            continue
        msg = row.get("message") or {}
        usage = msg.get("usage")
        rid = row.get("requestId") or row.get("uuid")
        if not usage or rid in seen:
            continue
        seen.add(rid)
        if msg.get("model"):
            res["model"] = msg["model"]
        targets = ["all"]
        if since and (row.get("timestamp") or "") >= since:
            targets.append("turn")
        for t in targets:
            d = res[t]
            for k in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
                d[k] = d.get(k, 0) + (usage.get(k) or 0)
    return res


def cost(t, model_id):
    pin, pout = price_for(model_id)
    return (t.get("input_tokens", 0) * pin
            + t.get("cache_creation_input_tokens", 0) * pin * 1.25
            + t.get("cache_read_input_tokens", 0) * pin * 0.1
            + t.get("output_tokens", 0) * pout) / 1_000_000


def billable(t):
    """Tokens a person thinks of as spent: everything except cache reads."""
    return (t.get("input_tokens", 0) + t.get("cache_creation_input_tokens", 0)
            + t.get("output_tokens", 0))


def read_windows(source, pct_keys):
    """Both limit windows in the shape the message builder expects."""
    out = {}
    for window, use, reset in (("five_hour", "sessionUsage", "sessionResetAt"),
                               ("seven_day", "weeklyUsage", "weeklyResetAt")):
        block = source.get(window) or {}
        for key in pct_keys:
            if block.get(key) is not None:
                out[use] = block[key]
                out[reset] = block.get("resets_at")
                break
    return out


def cached_limits():
    """Numbers our statusline wrapper saw last. Official, local, free."""
    try:
        with open(LIMITS_CACHE) as f:
            data = json.load(f)
    except Exception:
        return {}
    if time.time() - data.get("captured_at", 0) > LIMITS_TTL:
        return {}
    return read_windows(data, ("used_percentage",))


def token_from(blob):
    """Pull the access token out of whichever shape the credentials came in."""
    for holder in (blob.get("claudeAiOauth"), blob.get("oauth"), blob):
        if isinstance(holder, dict):
            tok = holder.get("accessToken") or holder.get("access_token")
            if tok:
                return tok
    return ""


def oauth_token():
    """The user's token, from the two places Claude Code actually keeps it.

    Deliberately narrow: the exact keychain service, or the credentials file.
    Never a keychain dump: a plugin has no business reading the whole ring.
    """
    try:
        with open(os.path.join(HOME, ".claude/.credentials.json")) as f:
            found = token_from(json.load(f))
        if found:
            return found
    except Exception:
        pass
    if sys.platform != "darwin":
        return ""
    try:
        r = subprocess.run(["security", "find-generic-password",
                            "-s", "Claude Code-credentials", "-w"],
                           capture_output=True, text=True, timeout=10)
        return token_from(json.loads(r.stdout.strip()))
    except Exception:
        return ""


def api_limits():
    """Undocumented usage endpoint. Off unless the user switched it on."""
    st = state()
    hit = st.get("usage_api") or {}
    if time.time() - hit.get("at", 0) < API_TTL:
        return hit.get("data") or {}
    token = oauth_token()
    if not token:
        return {}
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": f"claude-code/{cli_version()}",
    })
    try:
        body = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as err:
        log(f"usage api failed: {err}")
        return {}
    out = read_windows(body, ("utilization", "used_percentage"))
    st["usage_api"] = {"at": time.time(), "data": out}
    save_state(st)
    return out


def cli_version():
    st = state()
    if st.get("cli_version"):
        return st["cli_version"]
    ver = "0.0.0"
    if os.path.exists(CLAUDE_BIN):
        try:
            r = subprocess.run([CLAUDE_BIN, "--version"], capture_output=True,
                               text=True, timeout=15)
            found = re.search(r"\d+\.\d+\.\d+", r.stdout)
            if found:
                ver = found.group(0)
        except Exception:
            pass
    st["cli_version"] = ver
    save_state(st)
    return ver


def limits(transcript_path):
    """Best source first: our wrapper, then the endpoint if allowed, else nothing.

    Nothing is a fine answer. The message still carries what the task cost.
    """
    out = cached_limits()
    if out:
        return out
    if USE_API:
        return api_limits()
    return {}


def tab_name(path):
    """The name Claude Code gave this tab. It writes one into the transcript as
       `ai-title` and rewrites it as the work turns, so the last wins. Free,
       instant, and the same words the person sees in their own sidebar."""
    found = ""
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return ""
    with handle as f:
        for line in f:
            if '"aiTitle"' not in line:
                continue
            hit = TITLE_RE.search(line)
            if hit:
                try:
                    found = json.loads('"' + hit.group(1) + '"')
                except ValueError:
                    found = hit.group(1)
    return found


def title_for(text, session_id):
    """A short task name. One Haiku call per session, cached after that."""
    if not text:
        return "untitled"
    key = session_id or hashlib.sha1(text[:2000].encode()).hexdigest()[:12]
    st = state()
    cache = st.get("titles", {})
    if key in cache:
        return cache[key]

    title = ""
    if NAME_TASKS and os.path.exists(CLAUDE_BIN):
        # The prompt carries text from the transcript, which may itself have
        # come off a web page or a file, so the child gets no tools to be
        # talked into using — and none of our settings, which include the bot
        # token, to leak into whatever it spawns.
        child = {k: v for k, v in os.environ.items()
                 if not k.startswith("CLAUDE_PLUGIN_OPTION_")}
        child["CC_TG_NOTIFY_CHILD"] = "1"
        try:
            r = subprocess.run(
                [CLAUDE_BIN, "-p", TITLE_PROMPT + text[:1500],
                 "--model", "haiku", "--disable-slash-commands",
                 "--permission-mode", "plan", "--allowed-tools", ""],
                capture_output=True, text=True, timeout=60, env=child, cwd=HOME)
            title = r.stdout.strip().strip('"').split("\n")[0][:60]
        except Exception:
            title = ""
    if not title:
        flat = re.sub(r"\s+", " ", text).strip()
        title = flat[:55]
        if len(flat) > 55:
            space = title.rfind(" ")
            title = (title[:space] if space > 20 else title).rstrip(" ,.:;-") + "..."

    cache[key] = title
    st["titles"] = dict(list(cache.items())[-40:])
    save_state(st)
    return title


def window_cost(reset_iso, hours):
    """What the current limit window has cost, across every project at once."""
    try:
        start = datetime.fromisoformat(reset_iso.replace("Z", "+00:00")) - timedelta(hours=hours)
    except (ValueError, AttributeError):
        return 0.0
    mark = start.isoformat()
    total = 0.0
    seen = set()
    for path in glob.glob(os.path.join(HOME, ".claude/projects/*/*.jsonl")):
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < start:
                continue
        except OSError:
            continue
        for row in parse(path):
            if row.get("type") != "assistant" or (row.get("timestamp") or "") < mark:
                continue
            msg = row.get("message") or {}
            usage = msg.get("usage")
            rid = row.get("requestId") or row.get("uuid")
            if not usage or rid in seen:
                continue
            seen.add(rid)
            total += cost(usage, msg.get("model"))
    return total


def limit_share(lim, turn_cost, model):
    """This task's share of the 5-hour limit, finer than the whole percent we get.

    Usage arrives rounded to integers, so one percent of a window is worth
    knowing: divide what the window has cost by the percent it reports. That
    rate is kept in state, which carries us through the start of a new window
    while the reported percent is still zero.
    """
    st = state()
    used = lim.get("sessionUsage")
    per_pct = st.get("dollars_per_pct")
    if used and used >= 1:
        spent = window_cost(lim.get("sessionResetAt"), 5)
        if spent > 0:
            per_pct = spent / used
            st["dollars_per_pct"] = per_pct
            save_state(st)
    if not per_pct:
        return None
    return turn_cost / per_pct


def metrics_block(turn, model, lim, share):
    spent = [f"<b>{t('tokens')}:</b> {num(billable(turn))} \u00b7 \u2248 ${cost(turn, model):.2f}"]
    if share is not None:
        txt = f"{share:.2f}%" if share >= 0.01 else "< 0.01%"
        spent.append(f"<b>{t('share')}:</b> {txt} {t('of_window')}")
    rows = ["<blockquote>" + "\n".join(spent) + "</blockquote>", ""]
    s5, sw = lim.get("sessionUsage"), lim.get("weeklyUsage")
    if s5 is not None:
        rows.append(f"<b>{t('five_hours')}</b> {bar(s5)} {s5}% \u00b7 "
                    f"{t('resets_in')} {left(lim.get('sessionResetAt'))}")
    if sw is not None:
        rows.append(f"<b>{t('week')}</b> {bar(sw)} {sw}% \u00b7 "
                    f"{t('resets_in')} {left(lim.get('weeklyResetAt'))}")
    if s5 is None and sw is None:
        rows.append(f"<i>{t('no_limits')}</i>")
    return "\n".join(rows)


TOOL_EN = {
    "Bash": "run a command",
    "Edit": "change a file",
    "Write": "create a file",
    "Read": "read a file",
    "WebFetch": "open a link",
    "WebSearch": "search the web",
    "Artifact": "publish a page",
    "Agent": "start a subagent",
    "Workflow": "start a workflow",
}


def pending_tool(rows):
    """The tool waiting on permission, and what it means to do."""
    for row in reversed(rows):
        if row.get("type") != "assistant":
            continue
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            inp = b.get("input") or {}
            detail = (inp.get("command") or inp.get("file_path")
                      or inp.get("url") or inp.get("query") or inp.get("description") or "")
            first = str(detail).strip().split("\n")[0]
            return b.get("name", ""), first[:90] + ("..." if len(first) > 90 else "")
    return "", ""


def pending_question(rows):
    """The most recent question that nobody has answered yet."""
    answered = set()
    for row in rows:
        content = (row.get("message") or {}).get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    answered.add(b.get("tool_use_id"))
    for row in reversed(rows):
        if row.get("type") != "assistant":
            continue
        content = (row.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict) or b.get("type") != "tool_use":
                continue
            if b.get("id") in answered:
                continue
            if b.get("name") == "AskUserQuestion":
                qs = (b.get("input") or {}).get("questions") or []
                if qs:
                    q = qs[0]
                    return q.get("question", ""), (q.get("options") or [])[:4]
            if b.get("name") == "ExitPlanMode":
                return "A plan is ready and waiting on your go-ahead", []
    return "", []


STRINGS = {
    "en": {
        "done": "TASK DONE", "failed": "TASK FAILED",
        "tokens": "Tokens", "share": "Share of limit",
        "of_window": "of the 5-hour window",
        "five_hours": "5 hours", "week": "Week",
        "resets_in": "resets in", "ran_for": "ran for",
        "no_limits": "rate limits unavailable",
        "wants_answer": "WANTS YOUR ANSWER",
        "needs_permission": "NEEDS PERMISSION",
        "waiting": "WAITING ON YOU",
        "go_terminal": "GO TO THE TERMINAL",
        "stopped_alone": "Claude stopped and cannot go on alone.",
        "wants_to": "Wants to",
        "allow": "Allow", "deny": "Deny",
        "allowed_here": "Allowed from here.",
        "denied_here": "Denied from here.",
        "no_answer": "No answer — it went to the terminal instead.",
        "bot_busy": "Another session is holding the bot — answer this one in the terminal.",
        "limit_reached": "LIMIT REACHED",
        "window_5h": "5-hour window", "window_week": "weekly limit",
        "nothing_until": "Nothing will run until it resets in",
        "connected": "Connected",
        "greet_body": ("The usual reason people bin a notifier is that it fires at "
                       "everything. This one stays quiet until a task has run for "
                       "half an hour."),
        "greet_note": "Failures always come through, whatever the threshold.",
        "greet_hint": "/settings to change · /spend to see the bill",
        "tab_time": "Time", "tab_messages": "Messages", "tab_spend": "Spend",
        "settings": "Settings",
        "defaults_line": "Running on defaults: quiet below {q}, all {n} messages on.",
        "custom_line": "Quiet below {q}, {on} of {n} messages on.",
        "pick_tab": "Pick a tab to make it yours.",
        "quiet_below": "Quiet below {q}",
        "time_note": ("Shorter tasks pass in silence. Failures always come through, "
                      "whatever the threshold."),
        "custom": "Custom", "refresh": "Refresh",
        "send_minutes": "send me a number of minutes",
        "counting": "counting…", "saved": "saved", "on": "on", "off": "off",
        "col_tab": "Tab", "col_cost": "Cost", "col_tokens": "Tokens",
        "quieter": "quieter", "tabs_total": "{n} tabs, {sum}, {days} days",
        "nothing_yet": "Nothing recorded yet.",
        "ev_done": "a long task reached the end",
        "ev_failed": "it stopped on an error",
        "ev_question": "Claude is waiting on your answer",
        "ev_permission": "it wants to run something",
        "ev_approve": "answer permission asks from here",
        "ev_limits": "your window is running out",
        "step_all": "everything", "step_5m": "5 min", "step_30m": "30 min",
        "step_1h": "1 hour", "step_2h": "2 hours",
        "b_all": "all", "b_5m": "5m", "b_30m": "30m", "b_1h": "1h", "b_2h": "2h",
        "u_s": "s", "u_m": "m", "u_h": "h", "u_d": "d",
        "u_sec": "sec", "u_min": "min", "u_hr": "hr", "u_day": "days",
        "any_moment": "any moment",
        "e_done": "done", "e_failed": "failed", "e_question": "questions",
        "e_permission": "permissions", "e_approve": "approvals", "e_limits": "limits",
    },
    "ru": {
        "done": "ЗАДАЧА ГОТОВА", "failed": "ЗАДАЧА УПАЛА",
        "tokens": "Токенов", "share": "Доля лимита",
        "of_window": "пятичасового окна",
        "five_hours": "5 часов", "week": "Неделя",
        "resets_in": "сбросится через", "ran_for": "работал",
        "no_limits": "лимиты недоступны",
        "wants_answer": "ЖДЁТ ТВОЕГО ОТВЕТА",
        "needs_permission": "НУЖНО РАЗРЕШЕНИЕ",
        "waiting": "ЖДЁТ ТЕБЯ",
        "go_terminal": "ИДИ К ТЕРМИНАЛУ",
        "stopped_alone": "Claude остановился и дальше сам не пойдёт.",
        "wants_to": "Хочет",
        "allow": "Разрешить", "deny": "Отказать",
        "allowed_here": "Разрешено отсюда.",
        "denied_here": "Отказано отсюда.",
        "no_answer": "Ответа не было — ушло в терминал.",
        "bot_busy": "Бот занят другой сессией — ответь в терминале.",
        "limit_reached": "ЛИМИТ ИСЧЕРПАН",
        "window_5h": "пятичасовое окно", "window_week": "недельный лимит",
        "nothing_until": "Ничего не запустится, пока не сбросится через",
        "connected": "Подключено",
        "greet_body": ("Обычно такие боты сносят за то, что они дёргают по любому "
                       "поводу. Этот молчит, пока задача не проработает полчаса."),
        "greet_note": "Упавшие приходят всегда, какой бы порог ни стоял.",
        "greet_hint": "/settings — настройки · /spend — расход",
        "tab_time": "Время", "tab_messages": "Сообщения", "tab_spend": "Расход",
        "settings": "Настройки",
        "defaults_line": "Настройки по умолчанию: тихо до {q}, включены все {n}.",
        "custom_line": "Тихо до {q}, включено {on} из {n}.",
        "pick_tab": "Выбери вкладку, чтобы поменять под себя.",
        "quiet_below": "Тихо до {q}",
        "time_note": ("Задачи короче проходят молча. Упавшие приходят всегда, "
                      "какой бы порог ни стоял."),
        "custom": "Своё", "refresh": "Обновить",
        "send_minutes": "пришли число минут",
        "counting": "считаю…", "saved": "готово", "on": "шлю", "off": "молчу",
        "col_tab": "Вкладка", "col_cost": "Цена", "col_tokens": "Токены",
        "quieter": "тише", "tabs_total": "{n} вкладок, {sum}, {days} дней",
        "nothing_yet": "Пока ничего не записано.",
        "ev_done": "долгая задача дошла до конца",
        "ev_failed": "оборвалась на ошибке",
        "ev_question": "Claude ждёт твоего ответа",
        "ev_permission": "хочет что-то запустить",
        "ev_approve": "отвечать на запросы разрешений отсюда",
        "ev_limits": "окно лимита кончается",
        "step_all": "всё", "step_5m": "5 мин", "step_30m": "30 мин",
        "step_1h": "1 час", "step_2h": "2 часа",
        "b_all": "всё", "b_5m": "5м", "b_30m": "30м", "b_1h": "1ч", "b_2h": "2ч",
        "u_s": "с", "u_m": "м", "u_h": "ч", "u_d": "д",
        "u_sec": "сек", "u_min": "мин", "u_hr": "ч", "u_day": "дн",
        "any_moment": "вот-вот",
        "e_done": "готово", "e_failed": "упала", "e_question": "вопросы",
        "e_permission": "разрешения", "e_approve": "подтверждения", "e_limits": "лимиты",
    },
}


def lang():
    """Whatever the person picked, else the language their Telegram is set to.
       Telegram hands us `language_code` with the very first message, so the
       right language is on screen before anyone opens the settings."""
    saved = state().get("lang")
    if saved in STRINGS:
        return saved
    guess = (state().get("tg_lang") or "").split("-")[0].lower()
    return guess if guess in STRINGS else "en"


def t(key, **kw):
    table = STRINGS.get(lang(), STRINGS["en"])
    text = table.get(key) or STRINGS["en"].get(key, key)
    return text.format(**kw) if kw else text


PROJECTS_ROOT = os.path.join(HOME, ".claude", "projects")
SPEND_CACHE = os.path.join(DATA, "spend.json")
SPEND_TTL = 300
SPEND_ROWS = 8


def money(amount):
    """Cents only where they carry information. A column of $254.44 next to
       $0.92 wastes three characters of a phone's width on noise."""
    return f"${amount:.0f}" if amount >= 10 else f"${amount:.2f}"


def short_tokens(n):
    """51.6M, 570K, 940 - the width of the number matters more than its tail."""
    n = int(n)
    for cut, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= cut:
            scaled = n / cut
            return f"{scaled:.1f}{suffix}" if scaled < 100 else f"{scaled:.0f}{suffix}"
    return str(n)


TITLE_RE = re.compile(r'"aiTitle":\s*"((?:[^"\\]|\\.)*)"')


def scan_session(path):
    """One session: what it was called, what it cost. Claude Code writes the
       tab name into the transcript as `ai-title` and rewrites it as the work
       turns, so the last one wins. Dedup by requestId: usage repeats in every
       record of a single request, and counting rows double-bills."""
    seen, tokens, spent, title, first, last = set(), 0, 0.0, "", None, None
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle as f:
        for line in f:
            if '"aiTitle"' in line:
                found = TITLE_RE.search(line)
                if found:
                    try:
                        title = json.loads('"' + found.group(1) + '"')
                    except ValueError:
                        title = found.group(1)
            if '"usage"' not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            stamp = row.get("timestamp")
            if stamp:
                first = stamp if first is None or stamp < first else first
                last = stamp if last is None or stamp > last else last
            msg = row.get("message") or {}
            usage, rid = msg.get("usage"), row.get("requestId")
            if not usage or not rid or rid in seen:
                continue
            seen.add(rid)
            tokens += billable(usage)
            spent += cost(usage, msg.get("model"))
    if not tokens:
        return None
    return {"name": title or "untitled", "tokens": tokens, "cost": spent,
            "first": first, "last": last}


def scan_all():
    """Every session across every project, dearest first. Cached: the
       transcripts run to hundreds of megabytes and a tap should not stall."""
    try:
        with open(SPEND_CACHE) as f:
            blob = json.load(f)
        if time.time() - blob.get("at", 0) < SPEND_TTL:
            return blob["rows"], blob["first"]
    except Exception:
        pass
    rows, first = [], None
    for path in glob.glob(os.path.join(PROJECTS_ROOT, "*", "*.jsonl")):
        got = scan_session(path)
        if not got:
            continue
        rows.append(got)
        if got["first"] and (first is None or got["first"] < first):
            first = got["first"]
    rows.sort(key=lambda r: r["cost"], reverse=True)
    try:
        os.makedirs(DATA, mode=0o700, exist_ok=True)
        tmp = SPEND_CACHE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"at": time.time(), "rows": rows, "first": first}, f)
        os.replace(tmp, SPEND_CACHE)   # a reader must never see half a file
    except OSError:
        pass
    return rows, first


def days_since(iso):
    try:
        began = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0
    return max(1, int((datetime.now(timezone.utc) - began).total_seconds() // 86400))


def spend_text():
    rows, first = scan_all()
    if not rows:
        return head(t("tab_spend")) + f"<i>{t('nothing_yet')}</i>"
    total = sum(r["cost"] for r in rows)
    lines = [f"{t('col_tab'):<17}{t('col_cost'):>6}{t('col_tokens'):>7}"]
    for r in rows[:SPEND_ROWS]:
        name = r["name"]
        name = (name[:16] + "…") if len(name) > 17 else name
        lines.append(f"{name:<17}{money(r['cost']):>6}{short_tokens(r['tokens']):>7}")
    rest = rows[SPEND_ROWS:]
    if rest:
        lines.append(f"{'+ ' + str(len(rest)) + ' ' + t('quieter'):<17}"
                     f"{money(sum(r['cost'] for r in rest)):>6}"
                     f"{short_tokens(sum(r['tokens'] for r in rest)):>7}")
    tail = t("tabs_total", n=len(rows), sum=money(total), days=days_since(first))
    return (head(t("tab_spend")) + "<pre>" + esc("\n".join(lines)) + "</pre>\n"
            + esc(tail))


MENU_STEPS = (("b_all", "step_all", 0), ("b_5m", "step_5m", 300),
              ("b_30m", "step_30m", 1800), ("b_1h", "step_1h", 3600),
              ("b_2h", "step_2h", 7200))

EVENTS = (("done", "e_done", "ev_done"),
          ("failed", "e_failed", "ev_failed"),
          ("question", "e_question", "ev_question"),
          ("permission", "e_permission", "ev_permission"),
          ("approve", "e_approve", "ev_approve"),
          ("limits", "e_limits", "ev_limits"))

APPROVE_WAIT = 60        # how long an approval button stays worth pressing
APPROVE_POLL = 2


def prefs():
    """What the user picked in the bot, falling back to plugin config."""
    saved = state().get("prefs") or {}
    out = {"min_seconds": saved.get("min_seconds", MIN_SECONDS)}
    events = saved.get("events") or {}
    out["events"] = {key: events.get(key, True) for key, _, _ in EVENTS}
    return out


def wants(kind):
    return prefs()["events"].get(kind, True)


def quiet_label(secs):
    for _, key, value in MENU_STEPS:
        if value == secs:
            return t(key)
    return dur(secs)


TABS = (("time", "tab_time"), ("events", "tab_messages"), ("spend", "tab_spend"))


def view():
    return state().get("view", "home")


def tab_row():
    """The tabs, with the open one held in brackets. Telegram cannot colour a
       button, and a tick here read as "Time is switched on" rather than "you
       are standing in Time"."""
    here = view()
    return [{"text": ("\u2039 " + t(name) + " \u203a") if key == here else t(name),
             "callback_data": f"v:{key}"}
            for key, name in TABS]


def keyboard():
    """One message, four faces. The tab row is always there; the controls under
       it belong to the open tab only, so nothing is on screen that the person
       is not looking at."""
    here = view()
    rows = [tab_row()]
    now = prefs()
    if here == "time":
        rows.append([{"text": ("\u2022 " if now["min_seconds"] == value else "") + t(short),
                      "callback_data": f"q:{value}"}
                     for short, _, value in MENU_STEPS])
        rows.append([{"text": t("custom"), "callback_data": "q:custom"}])
    elif here == "events":
        marks = [{"text": ("\u2713 " if now["events"].get(key, True) else "\u2717 ") + t(short),
                  "callback_data": f"e:{key}"}
                 for key, short, _ in EVENTS]
        for i in range(0, len(marks), 2):
            rows.append(marks[i:i + 2])
    elif here == "spend":
        rows.append([{"text": t("refresh"), "callback_data": "v:spend!"}])
    else:
        # only on the home screen: language is a once-in-a-lifetime choice and
        # does not deserve a slot next to the things people actually tune
        rows.append([{"text": ("‹ " + name.upper() + " ›") if name == lang()
                      else name.upper(), "callback_data": f"l:{name}"}
                     for name in STRINGS])
    return {"inline_keyboard": rows}


def head(tab):
    """The tab name leads and carries the weight; the product name trails it.
       Reading a lowercase word first made the whole thing look cheap."""
    return f"<b>{tab}</b>\n\n"


def home_text():
    now = prefs()
    on = sum(1 for key, _, _ in EVENTS if now["events"].get(key, True))
    default = (now["min_seconds"] == MIN_SECONDS and on == len(EVENTS))
    quiet = esc(quiet_label(now["min_seconds"]))
    state_line = (t("defaults_line", q=quiet, n=len(EVENTS)) if default
                  else t("custom_line", q=quiet, on=on, n=len(EVENTS)))
    return (head(t("settings")) + state_line
            + f"\n\n<blockquote>{t('pick_tab')}</blockquote>")


def time_text():
    now = prefs()
    return (head(t("tab_time"))
            + f"<b>{t('quiet_below', q=esc(quiet_label(now['min_seconds'])))}</b>\n\n"
            + f"<blockquote>{t('time_note')}</blockquote>")


def events_text():
    """What each message means. Which ones are on is already written on the
       buttons, so repeating it above them only crowds the screen."""
    legend = "\n".join(f"{t(short).capitalize()} \u2014 {t(note)}"
                       for _, short, note in EVENTS)
    return head(t("tab_messages")) + "<blockquote>" + esc(legend) + "</blockquote>"


def menu_text():
    here = view()
    if here == "time":
        return time_text()
    if here == "events":
        return events_text()
    if here == "spend":
        return spend_text()
    return home_text()


def api(method, payload):
    if not BOT_TOKEN:
        return {}
    body = urllib.parse.urlencode(
        {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
         for k, v in payload.items()}).encode()
    req = urllib.request.Request(f"{TELEGRAM_API}{BOT_TOKEN}/{method}", data=body)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception as err:
        log(f"{method} failed: {err}")
        return {}


def greet():
    """Said once, when the bot and the person first meet. It answers the thing
       they are actually worried about — being pestered — before listing
       anything the plugin can do."""
    send("\n".join([
        f"<b>{t('connected')}</b>",
        "",
        t("greet_body"),
        "",
        f"<blockquote>{t('greet_note')}</blockquote>",
        t("greet_hint"),
    ]))


def show_menu(edit_id=None):
    chat = chat_id()
    if not chat:
        return
    payload = {"chat_id": chat, "text": menu_text(), "parse_mode": "HTML",
               "reply_markup": keyboard()}
    if edit_id:
        payload["message_id"] = edit_id
        api("editMessageText", payload)
    else:
        answer = api("sendMessage", payload)
        mid = (answer.get("result") or {}).get("message_id")
        if mid:
            st = state()
            st["menu_id"] = mid
            save_state(st)


def apply_choice(data):
    """One tap: a new quiet threshold, one event switched over, or a change of
       face between settings and the spend report."""
    st = state()
    saved = st.get("prefs") or {}
    if data.startswith("l:"):
        want = data[2:]
        if want in STRINGS:
            st["lang"] = want
            save_state(st)
        return ""
    if data.startswith("v:"):
        want = data[2:]
        if want.rstrip("!") not in {k for k, _ in TABS} | {"home"}:
            return ""
        if want.endswith("!"):          # refresh: drop the cache, rescan
            want = want[:-1]
            try:
                os.remove(SPEND_CACHE)
            except OSError:
                pass
        # tapping the open tab again folds it away, back to the home text
        st["view"] = "home" if want == st.get("view") else want
        st.pop("awaiting", None)
        save_state(st)
        return t("counting") if want == "spend" else ""
    if data == "q:custom":
        st["awaiting"] = "minutes"
        save_state(st)
        return t("send_minutes")
    if data.startswith("q:"):
        try:
            saved["min_seconds"] = int(data[2:])
        except ValueError:
            return ""
        st["prefs"] = saved
        st.pop("awaiting", None)
        save_state(st)
        return t("saved")
    if data.startswith("e:"):
        key = data[2:]
        if key not in {k for k, _, _ in EVENTS}:
            return ""
        events = saved.get("events") or {}
        events[key] = not events.get(key, True)
        saved["events"] = events
        st["prefs"] = saved
        save_state(st)
        return t("on") if events[key] else t("off")
    return ""


def poll():
    """Read what came in from the phone: the first hello, taps, /settings."""
    if not BOT_TOKEN:
        return
    st = state()
    offset = st.get("offset", 0)
    try:
        raw = urllib.request.urlopen(
            f"{TELEGRAM_API}{BOT_TOKEN}/getUpdates?timeout=0&offset={offset}", timeout=10).read()
        body = json.loads(raw)
    except Exception as err:
        log(f"getUpdates failed: {err}")
        return
    if not body.get("ok"):
        log(f"getUpdates refused: {str(body)[:160]}")
        return
    seen = offset
    touched = False
    for update in body.get("result", []):
        seen = update.get("update_id", seen - 1) + 1
        tap = update.get("callback_query")
        if tap:
            if not from_owner(tap):
                continue
            data = tap.get("data", "")
            if data.startswith("p:"):
                # an approval waiter is after this one, but the offset moves on
                # either way, so hand it over through the state file
                parts = data.split(":")
                if len(parts) == 3:
                    st3 = state()
                    pending = st3.get("approvals") or {}
                    pending[parts[2]] = parts[1]
                    st3["approvals"] = pending
                    save_state(st3)
                    api("answerCallbackQuery", {"callback_query_id": tap.get("id"),
                                                "text": parts[1]})
                continue
            note = apply_choice(data)
            api("answerCallbackQuery", {"callback_query_id": tap["id"], "text": note})
            touched = True
            continue
        message = update.get("message") or {}
        text = (message.get("text") or "").strip().lower()
        chat = (message.get("chat") or {}).get("id")
        if chat and not state().get("chat_id") and not CHAT_ID:
            st2 = state()
            st2["chat_id"] = str(chat)
            # Telegram hands the person's own language over with their first
            # message, so the right one is on screen before they open settings
            st2["tg_lang"] = ((message.get("from") or {}).get("language_code") or "")
            save_state(st2)
            log(f"chat captured: {chat}")
            greet()
            show_menu()
        elif state().get("awaiting") == "minutes" and text.strip().rstrip("m").isdigit():
            st2 = state()
            saved2 = st2.get("prefs") or {}
            saved2["min_seconds"] = max(0, int(text.strip().rstrip("m")) * 60)
            st2["prefs"] = saved2
            st2["view"] = "time"
            st2.pop("awaiting", None)
            save_state(st2)
            show_menu(st2.get("menu_id"))
        elif text in ("/settings", "/start", "settings", "/spend", "spend"):
            st2 = state()
            st2["view"] = "spend" if text.lstrip("/") == "spend" else "home"
            save_state(st2)
            show_menu()
    st = state()
    st["offset"] = seen
    save_state(st)
    if touched:
        show_menu(state().get("menu_id"))


def on_stop(data):
    poll()
    path = data.get("transcript_path", "")
    rows = parse(path)
    text, since = last_request(rows)
    tal = tally(rows, since)
    ctx = title_context(rows)

    elapsed = 0
    if since:
        try:
            start = datetime.fromisoformat(since.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        except ValueError:
            pass

    lim = limits(path)
    share = limit_share(lim, cost(tal["turn"], tal["model"]), tal["model"])
    for alert in limit_alerts(lim):
        if wants("limits"):
            send(alert)

    threshold = prefs()["min_seconds"]
    if tal["error"]:
        if not wants("failed"):
            return
    elif elapsed < threshold or not wants("done"):
        return

    head = ("🔴 " + t("failed")) if tal["error"] else ("🟢 " + t("done"))
    name = tab_name(path) or title_for(ctx, data.get("session_id"))
    # a failure ends on an error, not on an answer: show the error instead
    last = (error_text(rows) if tal["error"] else "") \
        or summary(data.get("last_assistant_message") or "")

    msg = [
        f"<b>{head}</b>",
        f"<i>{esc(name)}</i>",
        "",
        metrics_block(tal["turn"], tal["model"], lim, share),
        "",
        f"<i>{esc(model_name(tal['model']))} \u00b7 {t('ran_for')} {dur(elapsed)}</i>",
    ]
    if last:
        msg += ["", f"<blockquote expandable>{esc(last)}</blockquote>"]
    send("\n".join(msg))


def summary(text, max_lines=10, max_chars=700):
    """The tail of the answer: ten lines at most, cut on a sentence end."""
    lines = [re.sub(r"[ \t]+", " ", ln).strip()
             for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    out, total = [], 0
    for ln in lines[:max_lines]:
        if total + len(ln) > max_chars:
            room = max_chars - total
            if room > 80:
                cut = ln[:room]
                dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
                out.append(cut[:dot + 1] if dot > 40 else cut.rstrip() + "...")
            break
        out.append(ln)
        total += len(ln)
    return "\n".join(out)


def on_notification(data):
    poll()
    st = state()
    now = time.time()
    if now - st.get("last_notify", 0) < NOTIFY_COOLDOWN:
        return
    st["last_notify"] = now
    save_state(st)

    path = data.get("transcript_path", "")
    rows = parse(path)
    ctx = title_context(rows)
    question, opts = pending_question(rows)
    raw = (data.get("message") or "").strip()

    if question:
        if not wants("question"):
            return
        head = f"🟠 <b>{t('wants_answer')}</b>"
        body = f"<blockquote>{esc(question)}</blockquote>"
        if opts:
            # each choice on its own line: a slash-joined string read as one
            # long option and the descriptions had nowhere to go
            picks = []
            for i, o in enumerate(opts, 1):
                block = f"<b>{i}. {esc(o.get('label', ''))}</b>"
                note = (o.get("description") or "").strip()
                if note:
                    block += f"\n<i>{esc(note[:90])}</i>"
                picks.append(block)
            # a blank line between choices: run together they read as one
            # paragraph and the eye cannot find where an option starts
            body += "\n" + "\n\n".join(picks)
        # no buttons here on purpose: a hook can return yes or no, never an
        # answer, so a tap could not reach the session. Say where to go instead.
        body += f"\n\n<b>{t('go_terminal')}</b>"
    elif "permission" in raw.lower():
        if not wants("permission"):
            return
        head = f"🟡 <b>{t('needs_permission')}</b>"
        tool, detail = pending_tool(rows)
        what = TOOL_EN.get(tool, f"call {tool}" if tool else "do something")
        body = f"{t('wants_to')} <b>{esc(what)}</b>"
        if detail:
            body += f"\n<blockquote>{esc(detail)}</blockquote>"
    else:
        head = f"🟠 <b>{t('waiting')}</b>"
        body = f"{t('stopped_alone')}\n\n<b>{t('go_terminal')}</b>"

    name = tab_name(path) or title_for(ctx, data.get("session_id"))
    send("\n".join([head, f"<i>{esc(name)}</i>", "", body]))


def limit_alerts(lim):
    """One message, and only once a window is actually spent. Warning at 80 and
       again at 95 filled the chat with things nobody could act on; being out is
       the only moment that changes what the person does next. Both bars ride
       along, because the answer to "what now" is whether the other one holds."""
    st = state()
    fired = st.get("limit_alerts", {})
    spent = []
    for key, label, reset_key in (("sessionUsage", t("window_5h"), "sessionResetAt"),
                                  ("weeklyUsage", t("window_week"), "weeklyResetAt")):
        pct = lim.get(key)
        if pct is None or pct < 100:
            continue
        reset = lim.get(reset_key) or ""
        if fired.get(key, {}).get("reset") == reset:
            continue                      # already said so for this window
        fired[key] = {"reset": reset, "step": 100}
        spent.append((label, reset))
    st["limit_alerts"] = fired
    save_state(st)
    if not spent:
        return []

    label, reset = spent[0]
    rows = [f"🔴 <b>{t('limit_reached')}</b>", f"<i>{label}</i>", ""]
    s5, sw = lim.get("sessionUsage"), lim.get("weeklyUsage")
    if s5 is not None:
        rows.append(f"<b>5 hours</b> {bar(s5)} {s5}% · resets in "
                    f"{left(lim.get('sessionResetAt'))}")
    if sw is not None:
        rows.append(f"<b>Week</b> {bar(sw)} {sw}% · resets in "
                    f"{left(lim.get('weeklyResetAt'))}")
    rows.append("")
    rows.append(f"<i>{t('nothing_until')} {left(reset)}.</i>")
    return ["\n".join(rows)]


POLL_LOCK = os.path.join(DATA, "poll.lock")


def take_poll_lock(tag):
    """The newest ask owns the bot. An older waiter whose prompt the person
       already answered in the terminal would otherwise sit there for two
       minutes holding the only long poll Telegram allows, and every ask after
       it would fall through to the terminal as well."""
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(POLL_LOCK, "w") as f:
            f.write(tag)
        return True
    except OSError:
        return True          # cannot lock: better to ask than to go silent


def holds_poll_lock(tag):
    try:
        with open(POLL_LOCK) as f:
            return f.read().strip() == tag
    except OSError:
        return False


def drop_poll_lock():
    try:
        os.remove(POLL_LOCK)
    except OSError:
        pass


def from_owner(tap):
    """A tap is only ours if it came from the chat we write to. Without this
       anyone who reaches the bot — a group member, or whoever pressed Start
       first — could approve a command on this machine."""
    owner = str(chat_id() or "")
    if not owner:
        return False
    who = str((tap.get("from") or {}).get("id") or "")
    where = str(((tap.get("message") or {}).get("chat") or {}).get("id") or "")
    return owner in (who, where)


def verdict(decision):
    """Both spellings on purpose. The docs call this field `decision` in one
       place and `permissionDecision` in another; whichever Claude Code reads,
       it finds the same answer, and the spare key is ignored."""
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                   "decision": decision,
                                   "permissionDecision": decision}}


def ask_and_wait(text, tag):
    """Put the ask in front of the person with two buttons and wait for a tap.
       Returns "allow", "deny", or "" when nobody answered in time."""
    chat = chat_id()
    if not chat:
        return ""
    payload = {"chat_id": chat, "text": text, "parse_mode": "HTML",
               "reply_markup": {"inline_keyboard": [[
                   {"text": t("allow"), "callback_data": f"p:allow:{tag}"},
                   {"text": t("deny"), "callback_data": f"p:deny:{tag}"}]]}}
    answer = api("sendMessage", payload)
    mid = (answer.get("result") or {}).get("message_id")
    if not mid:
        return ""

    # Telegram allows one long poll per bot, so only one waiter may run. This
    # ask claims it; any older waiter sees the claim and steps aside.
    take_poll_lock(tag)

    st = state()
    offset = st.get("offset", 0)
    deadline = time.time() + APPROVE_WAIT
    picked, misses = "", 0
    while time.time() < deadline and not picked:
        handed = (state().get("approvals") or {}).pop(tag, "")
        if handed:
            picked = handed
            break
        if not holds_poll_lock(tag):
            api("editMessageText", {"chat_id": chat, "message_id": mid,
                                    "parse_mode": "HTML",
                                    "text": text + f"\n\n<i>{t('bot_busy')}</i>"})
            return ""
        try:
            raw = urllib.request.urlopen(
                f"{TELEGRAM_API}{BOT_TOKEN}/getUpdates?timeout=0&offset={offset}",
                timeout=10).read()
            body = json.loads(raw)
        except Exception as err:
            misses += 1
            log(f"approval poll failed: {err}")
            if misses >= 3:
                break
            time.sleep(APPROVE_POLL)
            continue
        for update in body.get("result", []):
            offset = update.get("update_id", offset - 1) + 1
            tap = update.get("callback_query") or {}
            data = tap.get("data", "")
            if not data.startswith("p:") or not data.endswith(f":{tag}"):
                continue
            if not from_owner(tap):
                log("approval tap from a stranger, ignored")
                continue
            if ((tap.get("message") or {}).get("message_id")) != mid:
                continue
            picked = data.split(":")[1]
            api("answerCallbackQuery", {"callback_query_id": tap.get("id"),
                                        "text": picked})
        if not picked:
            time.sleep(APPROVE_POLL)

    if holds_poll_lock(tag):
        drop_poll_lock()
    st = state()
    st["offset"] = max(offset, st.get("offset", 0))
    pending = st.get("approvals") or {}
    pending.pop(tag, None)
    st["approvals"] = pending
    save_state(st)

    closing = {"allow": t("allowed_here"), "deny": t("denied_here")}.get(
        picked, t("no_answer"))
    api("editMessageText", {"chat_id": chat, "message_id": mid, "parse_mode": "HTML",
                            "text": text + f"\n\n<i>{closing}</i>"})
    return picked


def on_permission_request(data):
    """Claude Code wants to run something and is waiting on a decision. We only
       ever carry a yes or a no: nothing can be typed into the session from the
       phone, so a stolen chat cannot compose its own commands."""
    if not BOT_TOKEN or not wants("approve"):
        return verdict("prompt")
    tool = data.get("tool_name") or ""
    args = data.get("tool_input") or {}
    detail = args.get("command") or args.get("file_path") or args.get("url") or ""
    what = TOOL_EN.get(tool, f"call {tool}" if tool else "do something")
    path = data.get("transcript_path", "")
    name = tab_name(path) or title_for(title_context(parse(path)),
                                       data.get("session_id"))

    text = "\n".join([
        f"🟡 <b>{t('needs_permission')}</b>",
        f"<i>{esc(name)}</i>",
        "",
        f"{t('wants_to')} <b>{esc(what)}</b>",
    ])
    if detail:
        text += f"\n<blockquote>{esc(detail[:600])}</blockquote>"

    # a fresh tag per ask: with a shared fallback like "ask", a stale tap on
    # yesterday's message would approve today's command
    picked = ask_and_wait(text, secrets.token_urlsafe(6))
    if picked in ("allow", "deny"):
        log(f"approval {picked} for {tool}")
        return verdict(picked)
    return verdict("prompt")


def detach(payload):
    """A hook must not hold Claude Code up, so the work happens detached."""
    try:
        child = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=dict(os.environ, CC_TG_BG="1"), start_new_session=True)
        child.stdin.write(payload.encode())
        child.stdin.close()
        return True
    except Exception:
        return False


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:
        return
    event = data.get("hook_event_name")
    # An approval has to answer Claude Code, so this one stays in the
    # foreground and prints its verdict. Everything else is fire-and-forget.
    if event == "PermissionRequest":
        print(json.dumps(on_permission_request(data)))
        return
    if not os.environ.get("CC_TG_BG") and detach(raw):
        return
    if event == "Stop":
        on_stop(data)
    elif event == "Notification":
        on_notification(data)


if __name__ == "__main__":
    main()
