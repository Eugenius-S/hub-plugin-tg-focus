"""Digest of the owner's open tasks (telegram.digest) and turning on handoff silence (telegram.focus)."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="tg-focus-test-")
os.environ["HUB_STATE_DIR"] = TMP                          # unconditionally: tests never touch the live hub
os.environ["HUB_CONFIG"] = os.path.join(TMP, "hub.json")

from core import spool  # noqa: E402

import main  # noqa: E402

NOW = 1_800_000_000.0


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def say(lang):
    """Pin the hub language the plugin sees (it resolves it at the moment of sending)."""
    return mock.patch.object(main, "_lang", lambda: lang)


def session(name, state="busy", **kw):
    return {"name": name, "state": state, "since": NOW - 3600, "kind": "agent", **kw}


def task(did, session, words, state="working", **kw):
    return {"id": did, "session": session, "words": words, "state": state, "for": "m1", "for_session": None,
            "created": NOW - 7200, "since": NOW - 7200, **kw}


def world(sessions, tasks):
    return {"sessions": {s["name"]: s for s in sessions}, "delegations": {d["id"]: d for d in tasks},
            "messages": {}, "owner": {"deferred": []}, "schedule": {}, "runs": {}}


class Digest(unittest.TestCase):
    def setUp(self):
        patch = say("en")
        patch.start()
        self.addCleanup(patch.stop)

    def test_only_his_open_tasks(self):
        st = world([session("dev"), session("ops")], [
            task("d1", "dev", "Fix the bot"),
            task("d2", "dev", "Executor sub-task", parent="d1"),
            task("d3", "ops", "Set by a session", **{"for": None, "for_session": "dev"}),
            task("d4", "ops", "From the hub", **{"for": None}),
            task("d5", "ops", "Already replied", state="replied"),
            task("d6", "ops", "Closed", state="done")])
        text = main.digest(st, NOW)
        self.assertEqual(text, "**Open tasks: 1**\n• Fix the bot — dev: working")

    def test_waiting_for_him_goes_first_then_stuck(self):
        st = world([session("dev"), session("ops", "idle"), session("ask", "idle", pending=[{"id": "q1"}])], [
            task("d1", "dev", "Going"),
            task("d2", "ops", "Stuck", nudges=1, nudged_at=NOW - 600),
            task("d3", "ask", "Asked")])
        lines = main.digest(st, NOW).splitlines()
        self.assertEqual(lines[1:], ["• Asked — ask: waiting for you",
                                     "• Stuck — ops: stuck for 10 min, the core is nudging",
                                     "• Going — dev: working"])

    def test_stuck_after_the_last_nudge_is_the_hubs(self):
        st = world([session("ops", "idle")], [task("d2", "ops", "Stuck", nudges=3, timed_out=True)])
        self.assertIn("the hub decides", main.digest(st, NOW))

    def test_a_long_task_is_cut_to_one_line(self):
        st = world([session("dev")], [task("d1", "dev", "Very " * 30 + "\nsecond line")])
        line = main.digest(st, NOW).splitlines()[1]
        self.assertNotIn("second", line)
        self.assertLess(len(line), 90)

    def test_voice_mark_and_helpers(self):
        st = world([session("dev", "idle")], [task("d1", "dev", main.VOICE_MARK + " Make a digest"),
                                              task("d2", "x", "Sub-task", parent="d1", minihub=True,
                                                   **{"for": None, "for_session": "dev"})])
        st["sessions"]["x"] = session("x")
        self.assertEqual(main.digest(st, NOW).splitlines()[1], "• Make a digest — dev: executors are working")

    def test_nothing_open_is_empty(self):
        self.assertEqual(main.digest(world([], []), NOW), "")


class Language(unittest.TestCase):
    """Texts go to Telegram in the hub language: English by default, Russian when the hub speaks Russian."""

    def st(self):
        return world([session("dev"), session("ops", "idle")], [
            task("d1", "dev", "Fix the bot"),
            task("d2", "ops", "Stuck", nudges=1, nudged_at=NOW - 600)])

    def test_english_is_the_default(self):
        with mock.patch("core.i18n.current", side_effect=RuntimeError("no hub language")):
            self.assertEqual(main._lang(), "en")
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**Open tasks: 2**")

    def test_the_language_is_the_hubs(self):
        with mock.patch("core.i18n.current", return_value="ru"):
            self.assertEqual(main._lang(), "ru")
            self.assertEqual(main.digest(self.st(), NOW).splitlines(), [
                "**Открытые задачи: 2**", "• Stuck — ops: стоит 10 мин, ядро напоминает", "• Fix the bot — dev: работает"])

    def test_an_unknown_language_falls_back_to_english(self):
        with say("de"):
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**Open tasks: 2**")
        with say("ru-RU"):   # a regional variant of a known language
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**Открытые задачи: 2**")

    def test_both_tables_have_the_same_keys_and_placeholders(self):
        import re
        self.assertEqual(set(main.STR["en"]), set(main.STR["ru"]))
        for k, v in main.STR["en"].items():
            self.assertEqual(set(re.findall(r"\{(\w+)\}", v)), set(re.findall(r"\{(\w+)\}", main.STR["ru"][k])), k)


class Tail:
    def __init__(self, st):
        self.reader = type("R", (), {"state": st})()

    def poll(self):
        return False


class Process(unittest.TestCase):
    def setUp(self):
        patch = say("en")
        patch.start()
        self.addCleanup(patch.stop)
        shutil.rmtree(os.path.join(TMP, "spool"), ignore_errors=True)
        shutil.rmtree(os.path.join(TMP, main.EXT), ignore_errors=True)
        self.write({})
        self.now = NOW
        self.st = world([session("dev")], [task("d1", "dev", "Fix the bot")])

    def write(self, cfg):
        with open(os.environ["HUB_CONFIG"], "w", encoding="utf-8") as f:
            json.dump(cfg, f)

    def plugin(self):
        p = main.Plugin(TMP, clock=lambda: self.now)
        p.tail = Tail(self.st)
        return p

    def sent(self):
        out = []
        for name in sorted(os.listdir(os.path.join(TMP, "spool")) if os.path.isdir(os.path.join(TMP, "spool")) else []):
            with open(os.path.join(TMP, "spool", name), encoding="utf-8") as f:
                ev = json.load(f)
            out.append((ev["type"], ev["data"]["text"], ev["src"]))
        return out

    def test_a_digest_every_n_minutes(self):
        self.write({"telegram": {"digest_minutes": 30}})
        p = self.plugin()
        p.tick()
        self.now += 29 * 60
        p.tick()
        self.now += 2 * 60
        p.tick()
        self.assertEqual([t for _, t, _ in self.sent()], [main.digest(self.st, NOW), main.digest(self.st, NOW + 31 * 60)])
        self.assertEqual({(k, s) for k, _, s in self.sent()}, {("telegram.digest", "ext:tg-focus")})

    def test_the_digest_goes_out_in_the_language_of_the_moment(self):
        self.write({"telegram": {"digest_minutes": 5}})
        p = self.plugin()
        p.tick()
        with say("ru"):
            self.now += 6 * 60
            p.tick()
        texts = [t for _, t, _ in self.sent()]
        self.assertEqual(texts, ["**Open tasks: 1**\n• Fix the bot — dev: working",
                                 "**Открытые задачи: 1**\n• Fix the bot — dev: работает"])

    def test_the_last_digest_is_wiped_once_when_nothing_is_open(self):
        p = self.plugin()
        p.tick()
        self.st["delegations"]["d1"]["state"] = "done"
        p.tick()
        p.tick()
        self.assertEqual([t for _, t, _ in self.sent()][1:], [""])

    def test_switched_off_wipes_and_stays_quiet(self):
        p = self.plugin()
        p.tick()
        self.write({"telegram": {"digest": False}})
        self.now += 3600
        p.tick()
        p.tick()
        self.assertEqual([t for _, t, _ in self.sent()][1:], [""])

    def test_a_restart_does_not_send_it_again(self):
        self.plugin().tick()
        self.now += 60
        self.plugin().tick()
        self.assertEqual(len(self.sent()), 1)

    def test_focus_is_switched_on_once(self):
        p = self.plugin()
        self.assertTrue(p.focus_on())
        with open(os.environ["HUB_CONFIG"], encoding="utf-8") as f:
            self.assertIs(json.load(f)["telegram"]["focus"], True)
        self.write({"telegram": {"focus": False, "brief": True}})
        self.assertFalse(p.focus_on())
        with open(os.environ["HUB_CONFIG"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["telegram"], {"focus": False, "brief": True})


if __name__ == "__main__":
    unittest.main()
