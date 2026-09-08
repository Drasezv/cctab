#!/usr/bin/env python3
"""tests: python3 tests/test_cctab.py"""
import importlib.util
import io
import json
import urllib.parse
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location(
        "notify", os.path.join(ROOT, "scripts", "notify.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["notify"] = mod
    spec.loader.exec_module(mod)
    return mod


PLAIN = re.compile(r"<[^>]+>")


def plain(html):
    return PLAIN.sub("", html)


class Base(unittest.TestCase):
    """fresh module, fake state, no network"""

    def setUp(self):
        self.m = load()
        self.state = {"prefs": {}, "chat_id": "1"}
        self.m.state = lambda: self.state
        self.m.save_state = lambda s: self.state.update(s)
        self.sent = []
        self.m.send = lambda text: self.sent.append(text) or True
        self.calls = []
        self.m.api = lambda method, payload: (
            self.calls.append((method, payload)) or {"result": {"message_id": 1}})
        self.m.BOT_TOKEN = "test-token"
        self.tmp = tempfile.mkdtemp()
        self.m.DATA = self.tmp
        self.m.STATE_FILE = os.path.join(self.tmp, "state.json")
        self.m.LOG_FILE = os.path.join(self.tmp, "log")
        self.m.SPEND_CACHE = os.path.join(self.tmp, "spend.json")
        self.m.POLL_LOCK = os.path.join(self.tmp, "poll.lock")

    def transcript(self, rows):
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return path


class Money(Base):
    def test_cache_reads_are_cheap(self):
        """cache read = 0.1 of input price"""
        turn = {"input_tokens": 1_000_000, "output_tokens": 0,
                "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        full = self.m.cost(turn, "claude-opus-5")
        cached = self.m.cost({"input_tokens": 0, "output_tokens": 0,
                              "cache_creation_input_tokens": 0,
                              "cache_read_input_tokens": 1_000_000}, "claude-opus-5")
        self.assertAlmostEqual(full, 5.0, places=6)
        self.assertAlmostEqual(cached, 0.5, places=6)

    def test_billable_excludes_cache_reads(self):
        turn = {"input_tokens": 10, "output_tokens": 20,
                "cache_creation_input_tokens": 30, "cache_read_input_tokens": 9000}
        self.assertEqual(self.m.billable(turn), 60)

    def test_unknown_model_falls_back(self):
        turn = {"input_tokens": 1_000_000, "output_tokens": 0}
        self.assertAlmostEqual(self.m.cost(turn, "claude-something-new"), 5.0, places=6)

    def test_sonnet_versions_are_priced_apart(self):
        """sonnet 5 and 4.6 have different prices"""
        million_in = {"input_tokens": 1_000_000, "output_tokens": 0}
        self.assertAlmostEqual(self.m.cost(million_in, "claude-sonnet-5"), 2.0, places=6)
        self.assertAlmostEqual(self.m.cost(million_in, "claude-sonnet-4-6"), 3.0, places=6)
        million_out = {"input_tokens": 0, "output_tokens": 1_000_000}
        self.assertAlmostEqual(self.m.cost(million_out, "claude-sonnet-5"), 10.0, places=6)
        self.assertAlmostEqual(self.m.cost(million_out, "claude-haiku-4-5"), 5.0, places=6)
        self.assertAlmostEqual(self.m.cost(million_out, "claude-fable-5-1"), 50.0, places=6)

    def test_money_keeps_cents_only_where_they_matter(self):
        self.assertEqual(self.m.money(0.92), "$0.92")
        self.assertEqual(self.m.money(254.44), "$254")


class Tally(Base):
    def test_usage_counted_once_per_request(self):
        """usage repeats per record, count once per requestId"""
        usage = {"input_tokens": 100, "output_tokens": 200,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        rows = [{"type": "assistant", "requestId": "r1", "timestamp": "2026-01-01T00:00:00Z",
                 "message": {"model": "claude-opus-5", "usage": usage}} for _ in range(4)]
        got = self.m.tally(rows, None)
        self.assertEqual(got["turn"]["output_tokens"], 200)

    def test_a_mixed_turn_is_priced_per_model(self):
        big = {"input_tokens": 0, "output_tokens": 1_000_000,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        small = {"input_tokens": 0, "output_tokens": 1_000,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        rows = [
            {"type": "assistant", "requestId": "r1", "timestamp": "2026-01-01T00:00:00Z",
             "message": {"model": "claude-opus-5", "usage": big}},
            {"type": "assistant", "requestId": "r2", "timestamp": "2026-01-01T00:00:01Z",
             "message": {"model": "claude-haiku-4-5", "usage": small}},
        ]
        got = self.m.tally(rows, None)
        self.assertAlmostEqual(got["turn_cost"], 25.0 + 0.005, places=3)

    def test_error_seen(self):
        rows = [{"type": "assistant", "requestId": "r1",
                 "message": {"model": "claude-opus-5", "usage": {}}},
                {"type": "user", "isApiErrorMessage": True,
                 "message": {"content": [{"type": "text", "text": "API Error: 529"}]}}]
        self.assertTrue(self.m.tally(rows, None)["error"])
        self.assertIn("529", self.m.error_text(rows))


class Naming(Base):
    def test_tab_name_takes_the_last_title(self):
        """last ai-title wins"""
        path = self.transcript([
            {"type": "ai-title", "aiTitle": "первое имя"},
            {"type": "assistant", "message": {}},
            {"type": "ai-title", "aiTitle": "Игра промыты и генерация"},
        ])
        self.assertEqual(self.m.tab_name(path), "Игра промыты и генерация")

    def test_tab_name_missing_is_empty(self):
        self.assertEqual(self.m.tab_name(self.transcript([{"type": "user"}])), "")

    def test_tab_name_survives_a_broken_file(self):
        path = os.path.join(self.tmp, "broken.jsonl")
        with open(path, "w") as f:
            f.write("{not json at all\n")
        self.assertEqual(self.m.tab_name(path), "")


class Language(Base):
    def test_every_key_exists_in_both(self):
        en, ru = set(self.m.STRINGS["en"]), set(self.m.STRINGS["ru"])
        self.assertEqual(en - ru, set(), "нет в русском")
        self.assertEqual(ru - en, set(), "нет в английском")

    def test_telegram_language_is_used_before_any_choice(self):
        self.state["tg_lang"] = "ru-RU"
        self.assertEqual(self.m.lang(), "ru")

    def test_choice_beats_telegram(self):
        self.state.update({"tg_lang": "ru", "lang": "en"})
        self.assertEqual(self.m.lang(), "en")

    def test_unknown_language_falls_back_to_english(self):
        self.state["tg_lang"] = "pt-BR"
        self.assertEqual(self.m.lang(), "en")

    def test_no_raw_keys_leak_into_screens(self):
        """no raw keys like ev_done on screen"""
        stray = re.compile(r"\b(?:ev_|step_|b_|e_|u_)[a-z_0-9]+")
        for code in ("en", "ru"):
            self.state["lang"] = code
            for face in ("home", "time", "events"):
                self.state["view"] = face
                screen = plain(self.m.menu_text())
                self.assertIsNone(stray.search(screen), f"{code}/{face}: {screen}")
                for row in self.m.keyboard()["inline_keyboard"]:
                    for button in row:
                        self.assertIsNone(stray.search(button["text"]), button["text"])
                        self.assertTrue(button["text"].strip())


class Menu(Base):
    def test_tabs_fold_and_unfold(self):
        self.state["view"] = "time"
        self.assertEqual(self.m.apply_choice("v:time"), "")
        self.assertEqual(self.state["view"], "home", "повторный тап должен свернуть вкладку")

    def test_threshold_is_saved(self):
        self.m.apply_choice("q:300")
        self.assertEqual(self.state["prefs"]["min_seconds"], 300)

    def test_event_toggles(self):
        self.m.apply_choice("e:done")
        self.assertFalse(self.state["prefs"]["events"]["done"])
        self.m.apply_choice("e:done")
        self.assertTrue(self.state["prefs"]["events"]["done"])

    def test_approve_turns_on_in_one_tap(self):
        """approve starts off, first tap must turn it on"""
        self.m.apply_choice("e:approve")
        self.assertTrue(self.state["prefs"]["events"]["approve"],
                        "первый тап обязан включить, а не оставить как было")

    def test_language_switch(self):
        self.m.apply_choice("l:ru")
        self.assertEqual(self.state["lang"], "ru")

    def test_nonsense_callback_is_ignored(self):
        self.assertEqual(self.m.apply_choice("garbage"), "")
        self.assertEqual(self.m.apply_choice("q:not-a-number"), "")


class Escaping(Base):
    def test_html_in_a_tab_name_cannot_break_the_message(self):
        path = self.transcript([{"type": "ai-title", "aiTitle": "<b>evil</b> & co"}])
        name = self.m.tab_name(path)
        self.assertIn("&lt;b&gt;", self.m.esc(name))
        self.assertIn("&amp;", self.m.esc(name))


class Limits(Base):
    def test_only_fires_when_spent(self):
        soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        self.m.limit_alerts({"sessionUsage": 95, "weeklyUsage": 10,
                             "sessionResetAt": soon, "weeklyResetAt": soon})
        self.assertEqual(self.sent, [])
        self.m.limit_alerts({"sessionUsage": 100, "weeklyUsage": 10,
                             "sessionResetAt": soon, "weeklyResetAt": soon})
        self.assertEqual(len(self.sent), 1)

    def test_said_once_per_window(self):
        soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        lim = {"sessionUsage": 100, "weeklyUsage": 10,
               "sessionResetAt": soon, "weeklyResetAt": soon}
        self.m.limit_alerts(lim)
        self.m.limit_alerts(lim)
        self.assertEqual(len(self.sent), 1, "второй раз слать нельзя")

    def test_a_new_window_speaks_again(self):
        first = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        later = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        self.m.limit_alerts({"sessionUsage": 100, "weeklyUsage": 10,
                             "sessionResetAt": first, "weeklyResetAt": first})
        self.m.limit_alerts({"sessionUsage": 100, "weeklyUsage": 10,
                             "sessionResetAt": later, "weeklyResetAt": later})
        self.assertEqual(len(self.sent), 2)

    def test_a_failed_send_keeps_the_warning(self):
        """mark sent only after telegram accepted"""
        soon = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        lim = {"sessionUsage": 100, "weeklyUsage": 10,
               "sessionResetAt": soon, "weeklyResetAt": soon}
        self.m.send = lambda text: False          # чат ещё не привязан
        self.m.limit_alerts(lim)
        self.m.send = lambda text: self.sent.append(text) or True
        self.m.limit_alerts(lim)
        self.assertEqual(len(self.sent), 1,
                         "после провала отправки предупреждение обязано повториться")


class Cooldown(Base):
    def test_a_muted_kind_does_not_eat_the_cooldown(self):
        """muted event must not start the cooldown"""
        open(self.m.POLL_LOCK, "w").close()       # poll стоит в стороне
        self.state["prefs"] = {"events": {"permission": False}}
        self.m.on_notification({"transcript_path": "", "session_id": "s",
                                "message": "Claude needs your permission"})
        self.assertEqual(self.sent, [])
        self.assertFalse(self.state.get("last_notify"),
                         "заглушённое событие не должно заводить кулдаун")


class Approvals(Base):
    def test_decision_is_an_object_with_a_behavior(self):
        self.assertEqual(self.m.verdict("allow"),
                         {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                                 "decision": {"behavior": "allow"}}})
        denied = self.m.verdict("deny", "no thanks")
        self.assertEqual(denied["hookSpecificOutput"]["decision"],
                         {"behavior": "deny", "message": "no thanks"})

    def test_no_answer_leaves_the_flow_alone(self):
        self.assertNotIn("decision", self.m.verdict("")["hookSpecificOutput"])
        self.state["prefs"] = {"events": {"approve": True}}
        self.m.APPROVE_WAIT = 0
        got = self.m.on_permission_request(
            {"tool_name": "Bash", "transcript_path": "",
             "tool_input": {"command": "ls"}, "tool_use_id": "toolu_1"})
        self.assertEqual(got["hookSpecificOutput"]["hookEventName"], "PermissionRequest")
        self.assertNotIn("decision", got["hookSpecificOutput"])

    def test_approvals_are_off_until_asked_for(self):
        """approve is off by default"""
        self.assertFalse(self.m.prefs()["events"]["approve"])
        self.assertTrue(self.m.prefs()["events"]["done"])

    def test_switched_off_means_straight_to_the_terminal(self):
        self.state["prefs"] = {"events": {"approve": False}}
        got = self.m.on_permission_request(
            {"tool_name": "Bash", "transcript_path": "",
             "tool_input": {"command": "ls"}, "tool_use_id": "toolu_1"})
        self.assertNotIn("decision", got["hookSpecificOutput"])
        self.assertEqual(self.calls, [], "выключенная фича не должна писать в чат")

    def test_the_command_is_shown(self):
        self.state["prefs"] = {"events": {"approve": True}}
        self.m.APPROVE_WAIT = 0
        self.m.on_permission_request(
            {"tool_name": "Bash", "transcript_path": "",
             "tool_input": {"command": "rm -rf /tmp/thing"}, "tool_use_id": "toolu_2"})
        first = self.calls[0][1]["text"]
        self.assertIn("rm -rf /tmp/thing", first)

    def test_the_newest_ask_takes_the_bot(self):
        self.m.take_poll_lock("older")
        self.assertTrue(self.m.holds_poll_lock("older"))
        self.m.take_poll_lock("newer")
        self.assertFalse(self.m.holds_poll_lock("older"), "старый ждущий должен уступить")
        self.assertTrue(self.m.holds_poll_lock("newer"))


class Summary(Base):
    def test_cut_on_a_sentence(self):
        text = "Первое предложение. " + "x" * 900
        got = self.m.summary(text, max_chars=200)
        self.assertLessEqual(len(got), 210)
        self.assertTrue(got.endswith(".") or got.endswith("…"))

    def test_keeps_the_shape_of_short_answers(self):
        text = "Готово.\n\nВторая строка."
        self.assertEqual(self.m.summary(text), "Готово.\nВторая строка.")


class Spend(Base):
    def test_reads_a_session_and_prices_it(self):
        usage = {"input_tokens": 1000, "output_tokens": 1000,
                 "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        path = self.transcript([
            {"type": "ai-title", "aiTitle": "проверка"},
            {"type": "assistant", "requestId": "r1", "timestamp": "2026-09-01T00:00:00Z",
             "message": {"model": "claude-opus-5", "usage": usage}},
        ])
        got = self.m.scan_session(path)
        self.assertEqual(got["name"], "проверка")
        self.assertEqual(got["tokens"], 2000)
        self.assertGreater(got["cost"], 0)

    def test_a_session_with_no_usage_is_skipped(self):
        self.assertIsNone(self.m.scan_session(self.transcript([{"type": "user"}])))

    def test_empty_report_says_so_rather_than_crashing(self):
        self.m.PROJECTS_ROOT = os.path.join(self.tmp, "nothing-here")
        self.assertIn(plain(self.m.spend_text()).strip().split("\n")[-1],
                      [self.m.STRINGS["en"]["nothing_yet"], self.m.STRINGS["ru"]["nothing_yet"]])


class BrokenInput(Base):
    def test_unreadable_state_is_moved_aside(self):
        """broken state must not reset offset to 0"""
        fresh = load()
        fresh.STATE_FILE = os.path.join(self.tmp, "bad.json")
        fresh.LOG_FILE = os.path.join(self.tmp, "log")
        with open(fresh.STATE_FILE, "w") as f:
            f.write("{{{")
        self.assertEqual(fresh.state(), {"offset": -1})
        self.assertTrue(os.path.exists(fresh.STATE_FILE + ".bad"))
        self.assertFalse(os.path.exists(fresh.STATE_FILE))

    def test_missing_state_is_simply_empty(self):
        fresh = load()
        fresh.STATE_FILE = os.path.join(self.tmp, "nothing.json")
        self.assertEqual(fresh.state(), {})

    def test_missing_transcript_is_survivable(self):
        self.assertEqual(self.m.parse("/nowhere/at/all.jsonl"), [])
        self.assertEqual(self.m.tab_name("/nowhere/at/all.jsonl"), "")

    def test_bad_reset_time_does_not_crash(self):
        self.assertEqual(self.m.left("not a date"), "?")
        self.assertEqual(self.m.left(None), "?")


class Security(Base):

    def tap(self, who, chat, data, message_id=1):
        return {"id": "1", "from": {"id": who}, "data": data,
                "message": {"message_id": message_id, "chat": {"id": chat}}}

    def test_a_stranger_cannot_approve(self):
        self.assertTrue(self.m.from_owner(self.tap(1, 1, "p:allow:x")))
        self.assertFalse(self.m.from_owner(self.tap(999, 999, "p:allow:x")))

    def test_owner_is_recognised_by_either_side(self):
        self.assertTrue(self.m.from_owner(self.tap(999, 1, "p:allow:x")))

    def test_no_owner_means_nobody_is_trusted(self):
        self.state["chat_id"] = ""
        self.m.CHAT_ID = ""
        self.assertFalse(self.m.from_owner(self.tap(1, 1, "p:allow:x")))

    def test_secrets_are_redacted_before_sending(self):
        for leak in ("export KEY=sk-ant-api03-AbCdEfGh12345678901234",
                     "psql postgres://admin:hunter2@db/app",
                     "token: abcdef123456",
                     "bot 8123456789:AAF-abcdefghijklmnopqrstuvwxyz012345"):
            self.assertIn("[redacted]", self.m.redact(leak), leak)

    def test_ordinary_commands_survive_redaction(self):
        for safe in ("git push origin main", "npm test", "ls -la ~/Desktop"):
            self.assertEqual(self.m.redact(safe), safe)

    def test_every_ask_gets_its_own_tag(self):
        self.state["prefs"] = {"events": {"approve": True}}
        self.m.APPROVE_WAIT = 0
        tags = set()
        for _ in range(5):
            self.calls.clear()
            self.m.on_permission_request(
                {"tool_name": "Bash", "transcript_path": "",
                 "tool_input": {"command": "ls"}, "tool_use_id": ""})
            kb = self.calls[0][1]["reply_markup"]["inline_keyboard"][0]
            tags.add(kb[0]["callback_data"].split(":")[2])
        self.assertEqual(len(tags), 5, "тег должен быть свой у каждого запроса")

    def test_junk_callbacks_are_dropped(self):
        self.m.apply_choice("e:not-an-event")
        self.assertEqual(self.state["prefs"].get("events", {}), {})
        self.m.apply_choice("v:nonsense")
        self.assertNotEqual(self.state.get("view"), "nonsense")


class EndToEnd(unittest.TestCase):
    """drives the hook via stdin, only the network is faked"""

    def setUp(self):
        self.m = load()
        self.tmp = tempfile.mkdtemp()
        self.m.DATA = self.tmp
        self.m.STATE_FILE = os.path.join(self.tmp, "state.json")
        self.m.STATE_LOCK = os.path.join(self.tmp, "state.lock")
        self.m.LOG_FILE = os.path.join(self.tmp, "log")
        self.m.SPEND_CACHE = os.path.join(self.tmp, "spend.json")
        self.m.POLL_LOCK = os.path.join(self.tmp, "poll.lock")
        self.m.LIMITS_CACHE = os.path.join(self.tmp, "limits.json")
        self.m.PROJECTS_ROOT = os.path.join(self.tmp, "projects")
        self.m.BOT_TOKEN = "test-token"
        self.m.CHAT_ID = "1"
        self.m.USE_API = False
        self.posted = []
        self.updates = []

        def fake_api(method, payload):
            self.posted.append((method, payload))
            return {"result": {"message_id": len(self.posted)}}

        self.m.api = fake_api
        self.m.limits = lambda *a: {}
        self.m.window_cost = lambda *a: 0.0

        def fake_urlopen(url, **kw):
            target = getattr(url, "full_url", url)
            if hasattr(url, "data") and url.data:
                fields = urllib.parse.parse_qs(url.data.decode())
                self.posted.append(("sendMessage" if "sendMessage" in target
                                    else target.rsplit("/", 1)[-1],
                                    {k: v[0] for k, v in fields.items()}))
                return io.BytesIO(json.dumps(
                    {"ok": True, "result": {"message_id": len(self.posted)}}).encode())
            return io.BytesIO(json.dumps({"ok": True, "result": self.updates}).encode())

        self.m.urllib.request.urlopen = fake_urlopen

    def transcript(self, minutes_ago, tokens=50000, error=False):
        began = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        rows = [
            {"type": "ai-title", "aiTitle": "a real tab"},
            {"type": "user", "timestamp": began,
             "message": {"content": "please do the long thing for me"}},
            {"type": "assistant", "requestId": "r1", "timestamp": began,
             "message": {"model": "claude-opus-5",
                         "usage": {"input_tokens": 10, "output_tokens": tokens,
                                   "cache_creation_input_tokens": 0,
                                   "cache_read_input_tokens": 0}}},
        ]
        if error:
            rows.append({"type": "user", "isApiErrorMessage": True, "timestamp": began,
                         "message": {"content": [{"type": "text",
                                                  "text": "API Error: 529 overloaded"}]}})
        path = os.path.join(self.tmp, "t.jsonl")
        with open(path, "w") as f:
            for row in rows:
                f.write(json.dumps(row) + "\n")
        return path

    def fire(self, payload):
        saved_stdin, saved_env = sys.stdin, os.environ.get("CC_TG_BG")
        os.environ["CC_TG_BG"] = "1"          # stay in-process, do not detach
        sys.stdin = io.StringIO(json.dumps(payload))
        try:
            self.m.run()
        finally:
            sys.stdin = saved_stdin
            if saved_env is None:
                os.environ.pop("CC_TG_BG", None)

    def sent_texts(self):
        return [p[1].get("text", "") for p in self.posted
                if isinstance(p[1], dict) and p[1].get("text")]

    def test_a_long_task_is_announced_with_its_cost(self):
        self.fire({"hook_event_name": "Stop", "session_id": "s",
                   "transcript_path": self.transcript(45),
                   "last_assistant_message": "all done"})
        texts = [t for t in self.sent_texts() if t]
        self.assertTrue(texts, "о длинной задаче обязано прийти сообщение")
        self.assertIn("TASK DONE", texts[-1])
        self.assertIn("$", texts[-1], "в сообщении должна быть цена")
        self.assertIn("a real tab", texts[-1])

    def test_a_short_task_says_nothing(self):
        self.fire({"hook_event_name": "Stop", "session_id": "s",
                   "transcript_path": self.transcript(2),
                   "last_assistant_message": "quick one"})
        self.assertEqual([t for t in self.sent_texts() if t], [])

    def test_a_failure_speaks_however_short_it_was(self):
        self.fire({"hook_event_name": "Stop", "session_id": "s",
                   "transcript_path": self.transcript(1, error=True),
                   "last_assistant_message": ""})
        texts = [t for t in self.sent_texts() if t]
        self.assertTrue(texts, "об упавшей задаче сообщают всегда")
        self.assertIn("TASK FAILED", texts[-1])
        self.assertIn("529", texts[-1], "нужен текст ошибки, а не пустое соболезнование")

    def test_the_threshold_from_the_phone_is_obeyed(self):
        self.m.save_state({"chat_id": "1", "prefs": {"min_seconds": 60}})
        self.fire({"hook_event_name": "Stop", "session_id": "s",
                   "transcript_path": self.transcript(5),
                   "last_assistant_message": "five minutes of work"})
        self.assertTrue([t for t in self.sent_texts() if t],
                        "порог 1 минута — пятиминутная задача должна пройти")

    def test_a_menu_tap_from_the_owner_is_applied(self):
        self.m.save_state({"chat_id": "1", "menu_id": 7})
        self.updates = [{"update_id": 1, "callback_query": {
            "id": "c1", "data": "q:300", "from": {"id": 1},
            "message": {"message_id": 7, "chat": {"id": 1}}}}]
        self.m.poll()
        self.assertEqual(self.m.state()["prefs"]["min_seconds"], 300)

    def test_a_tap_from_a_stranger_changes_nothing(self):
        self.m.save_state({"chat_id": "1"})
        self.updates = [{"update_id": 1, "callback_query": {
            "id": "c1", "data": "q:300", "from": {"id": 999},
            "message": {"message_id": 7, "chat": {"id": 999}}}}]
        self.m.poll()
        self.assertEqual(self.m.state().get("prefs", {}).get("min_seconds"), None)

    def test_the_first_hello_captures_the_chat_and_greets(self):
        self.m.CHAT_ID = ""
        self.updates = [{"update_id": 1, "message": {
            "text": "hi", "chat": {"id": 4242},
            "from": {"id": 4242, "language_code": "ru"}}}]
        self.m.poll()
        self.assertEqual(self.m.state()["chat_id"], "4242")
        self.assertEqual(self.m.state()["tg_lang"], "ru")
        self.assertTrue(any("Подключено" in t or "Connected" in t
                            for t in self.sent_texts()))

    def test_a_stop_with_a_missing_transcript_stays_quiet_and_alive(self):
        self.fire({"hook_event_name": "Stop", "session_id": "s",
                   "transcript_path": "/nowhere.jsonl", "last_assistant_message": ""})
        self.assertEqual([t for t in self.sent_texts() if t], [])

    def test_a_crash_is_written_down_rather_than_swallowed(self):
        def boom():
            raise RuntimeError("boom")
        self.m.run = boom
        self.m.main()          # must not raise
        with open(self.m.LOG_FILE) as f:
            written = f.read()
        self.assertIn("crashed", written)
        self.assertIn("boom", written)

    def test_an_over_long_message_is_trimmed_not_dropped(self):
        self.assertEqual(len(self.m.clamp("x" * 9000)), self.m.TELEGRAM_MAX + 1)
        self.assertEqual(self.m.clamp("short"), "short")


if __name__ == "__main__":
    unittest.main(verbosity=2)
