"""Сводка открытых задач владельца (telegram.digest) и включение тишины передач (telegram.focus)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

TMP = tempfile.mkdtemp(prefix="tg-focus-test-")
os.environ["HUB_STATE_DIR"] = TMP                          # безусловно: живой хаб тестам не трогать
os.environ["HUB_CONFIG"] = os.path.join(TMP, "hub.json")

from core import spool  # noqa: E402

import main  # noqa: E402

NOW = 1_800_000_000.0


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def session(name, state="busy", **kw):
    return {"name": name, "state": state, "since": NOW - 3600, "kind": "agent", **kw}


def task(did, session, words, state="working", **kw):
    return {"id": did, "session": session, "words": words, "state": state, "for": "m1", "for_session": None,
            "created": NOW - 7200, "since": NOW - 7200, **kw}


def world(sessions, tasks):
    return {"sessions": {s["name"]: s for s in sessions}, "delegations": {d["id"]: d for d in tasks},
            "messages": {}, "owner": {"deferred": []}, "schedule": {}, "runs": {}}


class Digest(unittest.TestCase):
    def test_only_his_open_tasks(self):
        st = world([session("dev"), session("ops")], [
            task("d1", "dev", "Починить бота"),
            task("d2", "dev", "Подзадача исполнителя", parent="d1"),
            task("d3", "ops", "Поставила сессия", **{"for": None, "for_session": "dev"}),
            task("d4", "ops", "От хаба", **{"for": None}),
            task("d5", "ops", "Уже ответила", state="replied"),
            task("d6", "ops", "Закрыта", state="done")])
        text = main.digest(st, NOW)
        self.assertEqual(text, "**Открытые задачи: 1**\n• Починить бота — dev: работает")

    def test_waiting_for_him_goes_first_then_stuck(self):
        st = world([session("dev"), session("ops", "idle"), session("ask", "idle", pending=[{"id": "q1"}])], [
            task("d1", "dev", "Идёт"),
            task("d2", "ops", "Стоит", nudges=1, nudged_at=NOW - 600),
            task("d3", "ask", "Спросила")])
        lines = main.digest(st, NOW).splitlines()
        self.assertEqual(lines[1:], ["• Спросила — ask: ждёт тебя",
                                     "• Стоит — ops: стоит 10 мин, ядро напоминает",
                                     "• Идёт — dev: работает"])

    def test_stuck_after_the_last_nudge_is_the_hubs(self):
        st = world([session("ops", "idle")], [task("d2", "ops", "Стоит", nudges=3, timed_out=True)])
        self.assertIn("решает хаб", main.digest(st, NOW))

    def test_a_long_task_is_cut_to_one_line(self):
        st = world([session("dev")], [task("d1", "dev", "Очень " * 30 + "\nвторая строка")])
        line = main.digest(st, NOW).splitlines()[1]
        self.assertNotIn("вторая", line)
        self.assertLess(len(line), 90)

    def test_voice_mark_and_helpers(self):
        st = world([session("dev", "idle")], [task("d1", "dev", "🎤 голос: Сделай сводку"),
                                              task("d2", "x", "Подзадача", parent="d1", minihub=True,
                                                   **{"for": None, "for_session": "dev"})])
        st["sessions"]["x"] = session("x")
        self.assertEqual(main.digest(st, NOW).splitlines()[1], "• Сделай сводку — dev: работают исполнители")

    def test_nothing_open_is_empty(self):
        self.assertEqual(main.digest(world([], []), NOW), "")


class Tail:
    def __init__(self, st):
        self.reader = type("R", (), {"state": st})()

    def poll(self):
        return False


class Process(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(os.path.join(TMP, "spool"), ignore_errors=True)
        shutil.rmtree(os.path.join(TMP, main.EXT), ignore_errors=True)
        self.write({})
        self.now = NOW
        self.st = world([session("dev")], [task("d1", "dev", "Починить бота")])

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
