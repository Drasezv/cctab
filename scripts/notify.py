#!/usr/bin/env python3
"""cctab: Telegram notifications for Claude Code hooks."""
import glob
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

if os.environ.get("CC_TG_NOTIFY_CHILD"):
    sys.exit(0)

os.umask(0o077)
HOME = os.path.expanduser("~")
DATA = os.environ.get("CLAUDE_PLUGIN_DATA") or os.path.join(HOME, ".cctab")
STATE_FILE = os.path.join(DATA, "state.json")
STATE_LOCK = os.path.join(DATA, "state.lock")
LOG_FILE = os.path.join(DATA, "cctab.log")
LIMITS_CACHE = os.path.join(DATA, "limits.json")
# wherever the installer chose to put it: PATH first, then the usual homes
CLAUDE_BIN = shutil.which("claude") or next(
    (p for p in (os.path.join(HOME, ".local/bin/claude"),
                 os.path.join(HOME, ".claude/local/claude"),
                 "/opt/homebrew/bin/claude",
                 "/usr/local/bin/claude") if os.path.exists(p)), "")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def saved_config():
    try:
        with open(os.path.join(DATA, "config.json")) as f:
            return json.load(f)
    except Exception:
        return {}


def option(name, fallback=""):
    """plugin option, else config.json"""
    live = os.environ.get(f"CLAUDE_PLUGIN_OPTION_{name.upper()}", "").strip()
    if live:
        return live
    saved = saved_config().get(name, "")
    if isinstance(saved, bool):
        saved = "true" if saved else ""
    return str(saved).strip() or fallback


def flag(name):
    return option(name).lower() in ("1", "true", "yes", "on")


BOT_TOKEN = option("bot_token")
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
ERROR_WINDOW = 8     # how far back a failure may sit and still count

TELEGRAM_API = "https://api.telegram.org/bot"

# Dollars per million tokens, as published by Anthropic.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-mythos-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-opus-4-1": (15.0, 75.0),
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.8, 4.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-3-haiku": (0.25, 1.25),
}

# Rates for anything not in the table.
FALLBACK_PRICE = (5.0, 25.0)

TITLE_PROMPT = (
    "Below is how a working session began. Name the thing being worked on. "
    "It is a label for a phone notification.\n"
    "2-5 words, no quotes, no trailing period, and skip filler words like "
    "'task', 'setup' or 'building'. Reply with the label only.\n\n"
)


def chat_id():
    """chat id: config, then state, then first message to the bot"""
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
    log("no messages to the bot yet")
    return ""


TELEGRAM_MAX = 4000      # the hard limit is 4096; leave room for the ellipsis


def clamp(text):
    """telegram limit is 4096"""
    text = str(text)
    return text if len(text) <= TELEGRAM_MAX else text[:TELEGRAM_MAX] + "…"


def send(text):
    """returns True if telegram accepted it"""
    text = clamp(text)
    token, chat = BOT_TOKEN, chat_id()
    if not token or not chat:
        return False
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
        return True
    except Exception as err:
        detail = err.read().decode()[:300] if hasattr(err, "read") else str(err)
        log(f"send failed: {detail}")
        return False


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
        # offset 0 would replay the whole telegram backlog
        try:
            os.replace(STATE_FILE, STATE_FILE + ".bad")
            log("state file was unreadable, moved aside")
        except OSError:
            pass
        return {"offset": -1}


def save_state(s):
    """two tabs may write at once, so tmp per pid + lock"""
    os.makedirs(os.path.dirname(STATE_FILE), mode=0o700, exist_ok=True)
    tmp = f"{STATE_FILE}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False)
        with open(STATE_LOCK, "w") as lock:
            try:
                import fcntl
                fcntl.flock(lock, fcntl.LOCK_EX)
            except (ImportError, OSError):
                pass                       # Windows: the atomic replace alone
            os.replace(tmp, STATE_FILE)
    except OSError as err:
        log(f"could not save state: {err}")
        try:
            os.remove(tmp)
        except OSError:
            pass


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
    """strip keys/tokens before anything goes to telegram"""
    return SECRET_RE.sub("[redacted]", str(text))


def esc(s):
    s = redact(s)
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def num(n):
    return f"{int(n):,}".replace(",", " ")


def dur(sec, short=False):
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
    """time left until reset"""
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


UNPRICED = set()


def price_for(model_id):
    """longest id prefix wins, bedrock/vertex prefixes stripped"""
    name = (model_id or "").strip()
    for prefix in ("us.anthropic.", "eu.anthropic.", "apac.anthropic.",
                   "global.anthropic.", "anthropic."):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.split("@")[0].rsplit("-v", 1)[0] if "@" in name or name.endswith(":0") else name
    for key in sorted(PRICES, key=len, reverse=True):
        if name.startswith(key):
            return PRICES[key]
    if name and name not in UNPRICED:
        UNPRICED.add(name)          # once per model, not once per usage row
        log(f"unknown model {name}, priced at fallback rates")
    return FALLBACK_PRICE


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
    """last real user message and its timestamp"""
    for row in reversed(rows):
        if row.get("type") != "user" or row.get("isSidechain"):
            continue
        text = user_text(row).strip()
        if text and not text.startswith("<"):
            return text, row.get("timestamp")
    return "", None


def title_context(rows):
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
    """text of the last api error"""
    for row in reversed(rows[-ERROR_WINDOW:]):
        if not row.get("isApiErrorMessage"):
            continue
        found = user_text(row).strip()
        if found:
            return found
    return ""


def tally(rows, since):
    """token sums for the turn, cost per row (models can mix)"""
    res = {"turn": {}, "model": "", "error": False, "turn_cost": 0.0}
    seen = set()
    for row in rows[-ERROR_WINDOW:]:
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
        if not since or (row.get("timestamp") or "") >= since:
            bucket = res["turn"]
            for k in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens"):
                bucket[k] = bucket.get(k, 0) + (usage.get(k) or 0)
            res["turn_cost"] += cost(usage, msg.get("model"))
    return res


def cost(t, model_id):
    pin, pout = price_for(model_id)
    return (t.get("input_tokens", 0) * pin
            + t.get("cache_creation_input_tokens", 0) * pin * 1.25
            + t.get("cache_read_input_tokens", 0) * pin * 0.1
            + t.get("output_tokens", 0) * pout) / 1_000_000


def billable(t):
    """everything except cache reads"""
    return (t.get("input_tokens", 0) + t.get("cache_creation_input_tokens", 0)
            + t.get("output_tokens", 0))


def read_windows(source, pct_keys):
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
    """limits saved by statusline.py"""
    try:
        with open(LIMITS_CACHE) as f:
            data = json.load(f)
    except Exception:
        return {}
    if time.time() - data.get("captured_at", 0) > LIMITS_TTL:
        return {}
    return read_windows(data, ("used_percentage",))


def token_from(blob):
    for holder in (blob.get("claudeAiOauth"), blob.get("oauth"), blob):
        if isinstance(holder, dict):
            tok = holder.get("accessToken") or holder.get("access_token")
            if tok:
                return tok
    return ""


def oauth_token():
    """oauth token: keychain, else credentials file"""
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
    """usage endpoint, undocumented, opt-in"""
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


def limits(_transcript_path=None):
    """statusline cache, then api if enabled"""
    out = cached_limits()
    if out:
        return out
    if USE_API:
        return api_limits()
    return {}


def tab_name(path):
    """tab name from ai-title records, last one wins"""
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
    """task name via haiku, cached per session"""
    if not text:
        return "untitled"
    key = session_id or hashlib.sha1(text[:2000].encode()).hexdigest()[:12]
    st = state()
    cache = st.get("titles", {})
    if key in cache:
        return cache[key]

    title = ""
    if NAME_TASKS and os.path.exists(CLAUDE_BIN):
        # no tools and no plugin env for the child: the prompt holds untrusted text
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
    """cost of everything in the current window"""
    try:
        reset = datetime.fromisoformat(str(reset_iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return 0.0
    start = reset - timedelta(hours=hours)
    seen, total = set(), 0.0
    for path in glob.glob(os.path.join(PROJECTS_ROOT, "*", "*.jsonl")):
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), timezone.utc) < start:
                continue
            handle = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle as f:
            for line in f:
                if '"usage"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                stamp = row.get("timestamp")
                try:
                    when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    continue
                if when < start:
                    continue
                msg = row.get("message") or {}
                usage, rid = msg.get("usage"), row.get("requestId") or row.get("uuid")
                if not usage or not rid or rid in seen:
                    continue
                seen.add(rid)
                total += cost(usage, msg.get("model"))
    return total


def limit_share(lim, turn_cost, _model=None):
    """share of the 5h window for this turn"""
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


def metrics_block(turn, model, lim, share, spent_usd):
    spent = [f"<b>{t('tokens')}:</b> {num(billable(turn))} \u00b7 \u2248 ${spent_usd:.2f}"]
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
    """pending tool and its input"""
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
    """last unanswered AskUserQuestion"""
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
    """chosen lang, else telegram lang, else en"""
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
SPEND_ROWS = 6


def money(amount):
    return f"${amount:.0f}" if amount >= 10 else f"${amount:.2f}"


def short_tokens(n):
    n = int(n)
    for cut, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if n >= cut:
            scaled = n / cut
            return f"{scaled:.1f}{suffix}" if scaled < 100 else f"{scaled:.0f}{suffix}"
    return str(n)


TITLE_RE = re.compile(r'"aiTitle":\s*"((?:[^"\\]|\\.)*)"')


def scan_session(path):
    """one transcript: title, tokens, cost"""
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
            usage, rid = msg.get("usage"), row.get("requestId") or row.get("uuid")
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
    """all sessions, cached 5 min"""
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
        return 1
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


# approvals are off by default: the hook blocks the session while waiting
EVENT_DEFAULTS = {"approve": False}


def prefs():
    """prefs from state, defaults from config"""
    saved = state().get("prefs") or {}
    out = {"min_seconds": saved.get("min_seconds", MIN_SECONDS)}
    events = saved.get("events") or {}
    out["events"] = {key: events.get(key, EVENT_DEFAULTS.get(key, True))
                     for key, _, _ in EVENTS}
    return out


def wants(kind):
    return prefs()["events"].get(kind, True)


def quiet_label(secs):
    for _, key, value in MENU_STEPS:
        if value == secs:
            return t(key)
    if secs and secs % 60 == 0:      # a custom value is always whole minutes
        return f"{secs // 60} {t('u_min')}"
    return dur(secs)


TABS = (("time", "tab_time"), ("events", "tab_messages"), ("spend", "tab_spend"))


def view():
    return state().get("view", "home")


def tab_row():
    here = view()
    return [{"text": ("\u2039 " + t(name) + " \u203a") if key == here else t(name),
             "callback_data": f"v:{key}"}
            for key, name in TABS]


def keyboard():
    """keyboard for the current view"""
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
        # language switch only on the home screen
        rows.append([{"text": ("‹ " + name.upper() + " ›") if name == lang()
                      else name.upper(), "callback_data": f"l:{name}"}
                     for name in STRINGS])
    return {"inline_keyboard": rows}


def head(tab):
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
    """first message after pairing"""
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
    """handle one button press"""
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
        refreshing = want.endswith("!")
        if refreshing:                  # rescan, but stay where we are
            want = want[:-1]
            try:
                os.remove(SPEND_CACHE)
            except OSError:
                pass
        # tapping the open tab again folds it away, back to the home text
        st["view"] = want if refreshing else ("home" if want == st.get("view") else want)
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
        # flip from the shown value; approve defaults to off
        events[key] = not events.get(key, EVENT_DEFAULTS.get(key, True))
        saved["events"] = events
        st["prefs"] = saved
        save_state(st)
        return t("on") if events[key] else t("off")
    return ""


def poll():
    """getUpdates: pairing, taps, commands"""
    if not BOT_TOKEN:
        return
    if os.path.exists(POLL_LOCK):
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
                # an approval waiter needs this tap; pass it through state
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
            # telegram sends language_code with the first message is on.
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
    for data in st.pop("queued", []) or []:
        apply_choice(data)
        touched = True
    st = dict(state(), queued=[])
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
        except (ValueError, AttributeError, TypeError):
            pass

    # limits first, they matter even for short stops
    lim = limits()
    if wants("limits"):
        limit_alerts(lim)

    threshold = prefs()["min_seconds"]
    if tal["error"]:
        if not wants("failed"):
            return
    elif elapsed < threshold or not wants("done"):
        return

    # expensive part: scans recent transcripts
    share = limit_share(lim, tal["turn_cost"])

    head = ("🔴 " + t("failed")) if tal["error"] else ("🟢 " + t("done"))
    name = tab_name(path) or title_for(ctx, data.get("session_id"))
    # failed run: show the error, not the last answer
    last = summary(error_text(rows) if tal["error"] else "") \
        or summary(data.get("last_assistant_message") or "")

    msg = [
        f"<b>{head}</b>",
        f"<i>{esc(name)}</i>",
        "",
        metrics_block(tal["turn"], tal["model"], lim, share, tal["turn_cost"]),
        "",
        f"<i>{esc(model_name(tal['model']))} \u00b7 {t('ran_for')} {dur(elapsed)}</i>",
    ]
    if last:
        msg += ["", f"<blockquote expandable>{esc(last)}</blockquote>"]
    send("\n".join(msg))


def summary(text, max_lines=10, max_chars=700):
    """up to 10 lines / 700 chars, cut at a sentence"""
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

    path = data.get("transcript_path", "")
    rows = parse(path)
    ctx = title_context(rows)
    question, opts = pending_question(rows)
    raw = (data.get("message") or "").strip()

    if question:
        if not wants("question"):
            return
        head = f"🟠 <b>{t('wants_answer')}</b>"
        body = f"<blockquote>{esc(question[:600])}</blockquote>"
        if opts:
            # one option per line
            picks = []
            for i, o in enumerate(opts, 1):
                block = f"<b>{i}. {esc((o.get('label') or '')[:120])}</b>"
                note = (o.get("description") or "").strip()
                if note:
                    block += f"\n<i>{esc(note[:90])}</i>"
                picks.append(block)
            # blank line between options
            body += "\n" + "\n\n".join(picks)
        # no buttons: a hook can only answer yes/no, not pick an option not reach.
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

    # cooldown only after a real send
    st["last_notify"] = now
    save_state(st)

    name = tab_name(path) or title_for(ctx, data.get("session_id"))
    send("\n".join([head, f"<i>{esc(name)}</i>", "", body]))


def limit_alerts(lim):
    """one message per window, only at 100%"""
    st = state()
    fired = st.get("limit_alerts", {})
    spent = []
    marks = {}
    for key, label, reset_key in (("sessionUsage", t("window_5h"), "sessionResetAt"),
                                  ("weeklyUsage", t("window_week"), "weeklyResetAt")):
        pct = lim.get(key)
        if pct is None or pct < 100:
            continue
        reset = lim.get(reset_key) or ""
        if fired.get(key, {}).get("reset") == reset:
            continue                      # already said so for this window
        marks[key] = {"reset": reset}
        spent.append((label, reset))
    if not spent:
        return

    label, reset = spent[0]
    rows = [f"🔴 <b>{t('limit_reached')}</b>", f"<i>{label}</i>", ""]
    s5, sw = lim.get("sessionUsage"), lim.get("weeklyUsage")
    if s5 is not None:
        rows.append(f"<b>{t('five_hours')}</b> {bar(s5)} {s5}% · "
                    f"{t('resets_in')} {left(lim.get('sessionResetAt'))}")
    if sw is not None:
        rows.append(f"<b>{t('week')}</b> {bar(sw)} {sw}% · "
                    f"{t('resets_in')} {left(lim.get('weeklyResetAt'))}")
    rows.append("")
    rows.append(f"<i>{t('nothing_until')} {left(reset)}.</i>")
    if not send("\n".join(rows)):
        return
    st = state()                          # send may have just captured the chat
    fired = st.get("limit_alerts", {})
    fired.update(marks)
    st["limit_alerts"] = fired
    save_state(st)


POLL_LOCK = os.path.join(DATA, "poll.lock")


def take_poll_lock(tag):
    """newest ask takes the poll lock"""
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
    """only taps from our chat count"""
    owner = str(chat_id() or "")
    if not owner:
        return False
    who = str((tap.get("from") or {}).get("id") or "")
    where = str(((tap.get("message") or {}).get("chat") or {}).get("id") or "")
    return owner in (who, where)


def verdict(behavior, message=""):
    """hook answer; decision is an object with behavior"""
    if behavior not in ("allow", "deny"):
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest"}}
    block = {"behavior": behavior}
    if message:
        block["message"] = message
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                   "decision": block}}


def ask_and_wait(text, tag):
    """send allow/deny buttons and wait"""
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

    # Telegram allows one long poll per bot, so only one waiter may run.
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
            if misses in (1, 10):        # one line, not one per retry
                log(f"approval poll failed: {err}")
            if misses >= 20:
                break
            time.sleep(APPROVE_POLL)
            continue
        for update in body.get("result", []):
            offset = update.get("update_id", offset - 1) + 1
            tap = update.get("callback_query") or {}
            data = tap.get("data", "")
            if not data.startswith("p:") or not data.endswith(f":{tag}"):
                # menu tap during a wait: keep it for poll()
                if data and from_owner(tap):
                    stash = state()
                    queued = stash.get("queued") or []
                    queued.append(data)
                    stash["queued"] = queued[-20:]
                    save_state(stash)
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
            misses = 0                   # a good round clears the streak
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
    """PermissionRequest hook"""
    if not BOT_TOKEN or not wants("approve"):
        return verdict("")
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

    # fresh tag per ask, or an old tap could approve a new command
    picked = ask_and_wait(text, secrets.token_urlsafe(6))
    if picked in ("allow", "deny"):
        log(f"approval {picked} for {tool}")
        return verdict(picked, "" if picked == "allow" else "Denied from Telegram")
    return verdict("")


def detach(payload):
    """re-exec detached so the hook returns fast"""
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
    try:
        run()
    except Exception:
        # stderr is closed in the child, so log the crash
        import traceback
        log("crashed: " + traceback.format_exc(limit=6).replace("\n", " | "))


def run():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except Exception:
        return
    event = data.get("hook_event_name")
    # approvals must answer synchronously, everything else detaches
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
