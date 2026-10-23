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


def d(ts=NOW):
    return main.when(ts, {})


def visible(row):
    """What the reader counts: no list dash and no bold marks."""
    return row[2:].replace("**", "")


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
        self.assertEqual(main.digest(st, NOW), "**🛠 In progress · 1**\n- **Fix the bot**")

    def test_sections_in_the_designers_order(self):
        st = world([session("dev"), session("ops", "idle"), session("ask", "idle", pending=[{"id": "q1"}])], [
            task("d1", "dev", "Going"),
            task("d2", "ops", "Stuck", nudges=1, nudged_at=NOW - 600),
            task("d3", "ask", "Asked")])
        self.assertEqual(main.digest(st, NOW), "\n".join([
            "**⚡ Waiting for you · 1**", "- **Asked**", "",
            "**🛠 In progress · 1**", "- **Going**", "",
            "**⏳ Stalled · 1**", f"- **Stuck**: the core is nudging · since {d(NOW - 600)}"]))

    def test_an_empty_section_is_not_shown(self):
        st = world([session("ask", "idle", pending=[{"id": "q1"}])], [task("d3", "ask", "Asked")])
        self.assertEqual(main.digest(st, NOW), "**⚡ Waiting for you · 1**\n- **Asked**")

    def test_every_stalled_row_has_a_date(self):
        st = world([session("ops", "idle"), session("dead", "removed")], [
            task("d2", "ops", "Stuck", nudges=3, timed_out=True), task("d3", "dead", "Orphan"),
            task("d4", "nobody", "No session")])
        rows = [r for r in main.digest(st, NOW).splitlines() if r.startswith("- ")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r.endswith(f"since {d(NOW - 7200)}") or r.endswith(f"since {d(NOW - 3600)}") or
                            r.endswith(f"since {d(NOW)}") for r in rows), rows)
        self.assertIn("the hub decides", rows[0])

    def test_no_session_names_ids_or_paths(self):
        st = world([session("secret-session")], [
            task("d1", "secret-session", "Fix the bot [hub:m42] see d12345 and **more**")])
        text = main.digest(st, NOW)
        for bad in ("secret-session", "d12345", "m42", "hub:", "**more"):
            self.assertNotIn(bad, text)

    def test_the_name_is_the_task_title_up_to_four_words(self):
        st = world([session("dev")], [
            task("d1", "dev", "Raw speech words that go on and on", title="Motion Gallery: icon size"),
            task("d2", "dev", "Make two modes of the plugin and then test it all", created=NOW - 8000)])
        rows = main.digest(st, NOW).splitlines()[1:]
        self.assertEqual(rows, ["- **Make two modes** of the plugin and then test it all", "- **Motion Gallery**: icon size"])

    def test_a_row_is_up_to_70_characters_and_four_rows_a_section(self):
        tasks = [task(f"d{i}", "dev", f"Name {i}: " + "word " * 40, created=NOW - 100 + i) for i in range(1, 8)]
        text = main.digest(world([session("dev")], tasks), NOW)
        rows = [r for r in text.splitlines() if r.startswith("- ")]
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[-1], "- and 3 more")
        self.assertTrue(all(len(visible(r)) <= main.ROW for r in rows[:-1]), rows)
        self.assertTrue(rows[0].startswith("- **Name 1**: word"))
        self.assertTrue(rows[0].endswith("…"))
        self.assertEqual(text.splitlines()[0], "**🛠 In progress · 7**")

    def test_a_long_task_is_cut_to_one_line(self):
        st = world([session("dev")], [task("d1", "dev", "Very " * 30 + "\nsecond line")])
        row = main.digest(st, NOW).splitlines()[1]
        self.assertNotIn("second", row)
        self.assertNotIn("\n", row)
        self.assertLessEqual(len(visible(row)), main.ROW)

    def test_voice_mark_and_helpers(self):
        st = world([session("dev", "idle")], [task("d1", "dev", main.VOICE_MARK + " Make a digest"),
                                              task("d2", "x", "Sub-task", parent="d1", minihub=True,
                                                   **{"for": None, "for_session": "dev"})])
        st["sessions"]["x"] = session("x")
        self.assertEqual(main.digest(st, NOW).splitlines()[1], "- **Make a digest**: executors are working")

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
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**🛠 In progress · 1**")

    def test_the_language_is_the_hubs(self):
        with mock.patch("core.i18n.current", return_value="ru"):
            self.assertEqual(main._lang(), "ru")
            self.assertEqual(main.digest(self.st(), NOW).splitlines(), [
                "**🛠 В работе · 1**", "- **Fix the bot**", "",
                "**⏳ Без движения · 1**", f"- **Stuck**: ядро напоминает · с {d(NOW - 600)}"])

    def test_an_unknown_language_falls_back_to_english(self):
        with say("de"):
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**🛠 In progress · 1**")
        with say("ru-RU"):   # a regional variant of a known language
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**🛠 В работе · 1**")

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
        self.assertEqual(texts, ["**🛠 In progress · 1**\n- **Fix the bot**", "**🛠 В работе · 1**\n- **Fix the bot**"])

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
