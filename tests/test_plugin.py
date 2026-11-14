"""Digest of the owner's open tasks (telegram.digest) and turning on handoff silence (telegram.focus)."""
import json
import os
import re
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

from core import executors, reducer, spool  # noqa: E402

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
    """The owner's task dN: set by their message mN, the head of the chain whose root is mN."""
    n = did[1:]
    return {"id": did, "session": session, "words": words, "state": state, "for": f"m{n}", "for_session": None,
            "root": f"m{n}", "created": NOW - 7200, "since": NOW - 7200, **kw}


def side(did, session, root, state="working", **kw):
    """A sub-task of the owner's task: the hub handed his same message to one more session (hub task new --for mN)."""
    return {"id": did, "session": session, "words": f"Words of {root}", "state": state, "for": root, "for_session": None,
            "by": "hub", "root": root, "created": NOW - 3600, "since": NOW - 3600, **kw}


def sub(did, session, parent, root, state="working", **kw):
    """An executor's task: a session raised it on its own (hub task run), no title and no words of the owner."""
    return {"id": did, "session": session, "context": "Brief with `code` and /some/path/file.py", "state": state,
            "for": None, "for_session": "dev", "parent": parent, "root": root, "created": NOW - 3600,
            "since": NOW - 3600, **kw}


def by(name):
    """A fact a session wrote (hub task new, hub task go and the like from its turn)."""
    return {"channel": "session", "owner": False, "sender": name}


def world(sessions, tasks, messages=None):
    st = {"sessions": {s["name"]: s for s in sessions}, "delegations": {d["id"]: d for d in tasks},
          "messages": {}, "owner": {"deferred": []}, "schedule": {}, "runs": {}}
    for t in tasks:   # the owner's message the chain hangs on
        if t.get("root"):
            st["messages"].setdefault(t["root"], {"id": t["root"], "from_owner": True, "state": "done", "session": "hub", "text": t.get("words") or ""})
    st["messages"].update(messages or {})
    return st


def d(ts=NOW):
    return main.when(ts, {})


def visible(row):
    """What the reader counts: no list dash and no bold marks."""
    return row[2:].replace("**", "")


def rows(text):
    return [r for r in text.splitlines() if r.startswith("- ")]


class Digest(unittest.TestCase):
    def setUp(self):
        patch = say("en")
        patch.start()
        self.addCleanup(patch.stop)

    def chain(self):
        """One owner task with two executors under it: one writes, one asks the owner."""
        return world([
            session("dev", "idle"),
            session("ex1", gist={"text": "Grouping: tests first"}, recap={"text": "Writes the grouping"}, delegation="d11"),
            session("ex2", "idle", pending=[{"id": "q1"}], gist={"text": "Emoji: asks which one"})],
            [task("d1", "dev", "Digest by tasks", title="Digest by tasks"),
             side("d11", "ex1", "m1"), side("d12", "ex2", "m1")])

    def test_grouped_by_the_owners_task_not_by_status(self):
        text = main.digest(self.chain(), NOW)
        self.assertEqual(text.splitlines(), [
            "**📋 Open tasks · 1**", "",
            "**Digest by tasks**",
            "- ⏭ **Emoji**: asks which one → waiting for your word",
            "- 🔄 **Grouping**: tests first"])
        for old in ("Waiting for you", "In progress", "Stalled"):
            self.assertNotIn(old, text)

    def test_a_sub_task_without_a_gist_is_named_by_its_stage(self):
        st = world([session("dev", "idle"), session("ex", recap={"text": "Adds the option to the bot and checks delivery"},
                                                    delegation="d11")],
                   [task("d1", "dev", "Notify me", title="Notify me"), side("d11", "ex", "m1")])
        self.assertEqual(rows(main.digest(st, NOW)), ["- 🔄 **Adds the option** to the bot and checks delivery"])

    def test_a_task_without_sub_tasks_is_one_row(self):
        st = world([session("dev", recap={"text": "Fixing the bot"}, delegation="d1")], [task("d1", "dev", "Fix the bot")])
        self.assertEqual(main.digest(st, NOW).splitlines(), [
            "**📋 Open tasks · 1**", "", "- 🔄 **Fix the bot**: Fixing the bot"])

    def test_every_status_has_its_own_emoji(self):
        st = world([session("dev", "idle"), session("ask", "idle", pending=[{"id": "q"}]), session("w", delegation="d2"),
                    session("busy-one", delegation="d99"), session("zzz", "asleep"), session("dead", "removed"),
                    session("bad", "failed"), session("front", "idle", kind="hub")], [
            task("d1", "ask", "Asked"), task("d2", "w", "Working"), task("d3", "busy-one", "Taken"),
            task("d4", "dev", "Waiting for nothing"), task("d9", "front", "Nobody took it"), task("d5", "zzz", "Sleeping"),
            task("d6", "dead", "Orphan"), task("d7", "bad", "Crashed"),
            task("d8", "dev", "Replied for the owner", state="waiting-owner", reply="Need from you: pick one.")])
        st["sessions"]["busy-one"]["delegation"] = "d99"
        st["delegations"]["d99"] = task("d99", "busy-one", "Other work", title="Other work", **{"for": None})
        with mock.patch.object(main, "TASKS", 20):
            by = {re.search(r"\*\*(.+?)\*\*", r).group(1): r[2:] for r in rows(main.digest(st, NOW))}
        self.assertTrue(by["Asked"].startswith("⏭ "), by)
        self.assertTrue(by["Replied for the owner"].startswith("⏭ "), by)
        self.assertIn("pick one", by["Replied for the owner"])
        self.assertTrue(by["Taken"].startswith("⏭ "), by)
        self.assertIn("Other work", by["Taken"])
        self.assertTrue(by["Working"].startswith("🔄 "), by)
        self.assertTrue(by["Waiting for nothing"].startswith("⏸ "), by)
        self.assertIn("the core is nudging · since", by["Waiting for nothing"])
        self.assertTrue(by["Nobody took it"].startswith("⏸ "), by)
        self.assertIn("no result yet", by["Nobody took it"])
        self.assertTrue(by["Sleeping"].startswith("⏸ "), by)
        self.assertIn("session asleep", by["Sleeping"])
        self.assertTrue(by["Orphan"].startswith("⚠️ "), by)
        self.assertIn("no session", by["Orphan"])
        self.assertTrue(by["Crashed"].startswith("⚠️ "), by)

    def test_stuck_shows_the_reason_and_the_date(self):
        st = world([session("ops", "idle")], [task("d1", "ops", "Stuck", nudges=1, nudged_at=NOW - 600)])
        self.assertEqual(rows(main.digest(st, NOW)), [f"- ⏸ **Stuck**: the core is nudging · since {d(NOW - 600)}"])
        st["delegations"]["d1"].update(nudges=3, timed_out=True)
        self.assertIn("the hub decides", rows(main.digest(st, NOW))[0])

    def test_chains_with_a_blocker_come_first(self):
        st = world([session("a"), session("ex", "idle", pending=[{"id": "q"}], gist={"text": "Emoji: pick"})],
                   [task("d1", "a", "Going", title="Going"), task("d2", "a", "Has a waiting sub-task",
                    title="Has a waiting sub-task"), side("d21", "ex", "m2")])
        st["sessions"]["a"]["delegation"] = "d1"
        text = main.digest(st, NOW)
        self.assertEqual(text.splitlines()[2:6], ["**Has a waiting sub-task**", "- ⏭ **Emoji**: pick → waiting for your word", "", "- 🔄 **Going**: in progress"])

    def test_only_open_work_is_shown(self):
        st = world([session("dev"), session("ex1"), session("ex2"), session("ex3"), session("ex4")], [
            task("d1", "dev", "Open task"),
            side("d11", "ex1", "m1", state="replied"), side("d12", "ex2", "m1", state="done"),
            side("d13", "ex3", "m1", state="stopped"), side("d14", "ex4", "m1"),
            task("d2", "dev", "Replied task", state="replied"), task("d3", "dev", "Closed", state="done"),
            task("d4", "dev", "Stopped", state="stopped")])
        text = main.digest(st, NOW)
        self.assertEqual(text.count("\n- "), 1)          # d14 only: the others are not open
        self.assertTrue(text.startswith("**📋 Open tasks · 1**"))
        for gone in ("Replied task", "Closed", "Stopped"):
            self.assertNotIn(gone, text)

    def test_not_the_owners_chain(self):
        st = world([session("dev"), session("ops")], [
            task("d1", "dev", "Mine"),
            task("d2", "ops", "Set by a session", **{"for": None, "for_session": "dev", "root": "m500"}),
            sub("d3", "ops", None, "m9")], messages={"m500": {"id": "m500", "from_owner": False, "state": "done", "text": "x"},
                                                      "m9": {"id": "m9", "from_owner": False, "state": "done", "text": "y"}})
        st["delegations"]["d3"].pop("parent")
        text = main.digest(st, NOW)
        self.assertNotIn("Set by a session", text)
        self.assertEqual(text.splitlines()[0], "**📋 Open tasks · 1**")

    def test_a_sub_task_below_a_sub_task_is_listed_under_the_task(self):
        st = world([session("dev", "idle"), session("ex1", delegation="d11", gist={"text": "Middle: one"}),
                    session("ex2", delegation="d12", gist={"text": "Deep: two"}),
                    session("ex3", delegation="d13", gist={"text": "Executor: three"})], [
            task("d1", "dev", "Top task"), side("d11", "ex1", "m1"), side("d12", "ex2", "m1", parent="d11"),
            sub("d13", "ex3", "d12", "m1")])
        self.assertEqual(len(rows(main.digest(st, NOW))), 2)
        self.assertNotIn("Executor", main.digest(st, NOW))

    def test_the_head_without_its_own_words_takes_the_owners_message(self):
        st = world([session("dev"), session("ex", gist={"text": "Covers: downloading"}, delegation="d5")], [
            side("d5", "ex", "m7", words=None)], messages={"m7": {"id": "m7", "from_owner": True, "state": "done",
                                                            "text": "[Image #1] 🎤 голос: animated covers are still missing"}})
        self.assertEqual(rows(main.digest(st, NOW)), ["- 🔄 **Animated covers are still** missing: Covers: downloading"])
        self.assertNotIn("Image", main.digest(st, NOW))
        self.assertNotIn("🎤", main.digest(st, NOW))

    def test_four_sub_tasks_then_and_n_more(self):
        sessions = [session("dev", "idle")] + [session(f"ex{i}", delegation=f"d1{i}", gist={"text": f"Part {i}: do"})
                                                for i in range(1, 8)]
        tasks = [task("d1", "dev", "Big task")] + [side(f"d1{i}", f"ex{i}", "m1", created=NOW - 100 + i)
                                                    for i in range(1, 8)]
        text = main.digest(world(sessions, tasks), NOW)
        self.assertEqual(len(rows(text)), 5)
        self.assertEqual(rows(text)[-1], "- and 3 more")

    def test_eight_tasks_then_and_n_more_tasks(self):
        tasks = [task(f"d{i}", "dev", f"Task number {i}: do it", created=NOW - 100 + i) for i in range(1, 12)]
        text = main.digest(world([session("dev")], tasks), NOW)
        self.assertEqual(text.splitlines()[0], "**📋 Open tasks · 11**")
        self.assertEqual(len(rows(text)), 9)
        self.assertEqual(rows(text)[-1], "- and 3 more tasks")

    def test_the_heading_of_a_task_is_all_bold(self):
        st = self.chain()
        st["delegations"]["d1"]["title"] = "Motion Gallery: refs as tags"
        self.assertEqual(main.digest(st, NOW).splitlines()[2], "**Motion Gallery: refs as tags**")

    def test_a_row_is_up_to_70_characters(self):
        long = "word " * 40
        st = world([session("dev", "idle"), session("ex", delegation="d11", gist={"text": "Name: " + long},
                                                    recap={"text": long})], [
            task("d1", "dev", "Head: " + long), side("d11", "ex", "m1"),
            task("d2", "dev", "Alone: " + long)])
        text = main.digest(st, NOW)
        self.assertGreaterEqual(len(rows(text)), 2)
        for r in rows(text):
            self.assertLessEqual(len(visible(r)), main.ROW, r)
        for line in text.splitlines():
            self.assertLessEqual(len(line.replace("**", "")), main.ROW + 3, line)

    def test_no_session_names_ids_paths_or_code(self):
        st = world([session("secret-session", "idle"), session("secret-exec", gist=None, recap=None, delegation="d11")],
                   [task("d1", "secret-session", "Fix the bot [hub:m42] see d12345 and **more**"),
                    side("d11", "secret-exec", "m1", words=None, context="Brief with `code` and /some/path/file.py")])
        text = main.digest(st, NOW)
        self.assertIn("**Sub-task**", text)       # at work, no gist and no words: a plain label, not the brief
        for bad in ("secret-session", "secret-exec", "d12345", "m42", "hub:", "**more", "/some/path", "`", "file.py", "d11"):
            self.assertNotIn(bad, text)

    def test_voice_mark_and_nothing_open(self):
        st = world([session("dev", "idle", kind="hub")], [task("d1", "dev", main.VOICE_MARK + " Make a digest")])
        self.assertEqual(rows(main.digest(st, NOW)), ["- ⏸ **Make a digest**: no result yet"])
        self.assertEqual(main.digest(world([], []), NOW), "")
        self.assertEqual(main.digest(world([session("dev")], [task("d1", "dev", "x", state="done")]), NOW), "")


class Noise(unittest.TestCase):
    """What a session raised on its own is its work, not the owner's: no "Sub-task: no result yet" rows in his digest."""

    def setUp(self):
        patch = say("en")
        patch.start()
        self.addCleanup(patch.stop)

    def test_an_executor_under_an_open_task_is_not_a_row(self):
        st = world([session("dev", "idle"), session("ex", delegation="d11", gist={"text": "Grouping: tests first"})],
                   [task("d1", "dev", "Digest by tasks"), sub("d11", "ex", "d1", "m1")])
        self.assertEqual(rows(main.digest(st, NOW)), ["- 🔄 **Digest by tasks**: sub-tasks in progress"])

    def test_executors_alone_do_not_keep_a_closed_task(self):
        st = world([session("dev", "idle"), session("ex", "idle"), session("busy-one", delegation="d99")], [
            task("d1", "dev", "Resume the comments", state="done"), sub("d11", "ex", "d1", "m1"),
            sub("d12", "busy-one", "d1", "m1", via_schedule=True)])
        self.assertEqual(main.digest(st, NOW), "")

    def test_an_executor_waiting_for_the_owner_keeps_the_task_and_is_a_row(self):
        st = world([session("dev", "idle"), session("ex", "idle", pending=[{"id": "q"}], gist={"text": "Emoji: pick"})],
                   [task("d1", "dev", "Digest by tasks", state="done"), sub("d11", "ex", "d1", "m1")])
        self.assertEqual(rows(main.digest(st, NOW)), ["- ⏭ **Emoji**: pick → waiting for your word"])
        st["delegations"]["d1"]["state"] = "working"
        self.assertEqual(main.digest(st, NOW).splitlines()[2:], ["**Digest by tasks**", "- ⏭ **Emoji**: pick → waiting for your word"])

    def test_a_sub_task_with_no_name_and_a_service_status_is_never_a_row(self):
        for name, extra in (("idle", {}), ("busy", {"delegation": "d99"})):
            with self.subTest(name):
                st = world([session("dev", "idle"), session("ex", "idle" if name == "idle" else "busy", **extra)],
                           [task("d1", "dev", "Head task", state="done"), side("d11", "ex", "m1", words=None)])
                st["delegations"]["d99"] = task("d99", "ex", "", **{"for": None, "for_session": "x"})
                self.assertEqual(main.digest(st, NOW), "")
                st["delegations"]["d1"]["state"] = "working"   # the task itself is open: one row with its own status
                self.assertEqual(rows(main.digest(st, NOW)), ["- 🔄 **Head task**: sub-tasks in progress"])
                for bad in ("Sub-task", "no result yet", "busy"):
                    self.assertNotIn(bad, main.digest(st, NOW))


class Replay(unittest.TestCase):
    """The owner's Telegram on 14.11: "Open tasks / Resume the auto-comments / Sub-task: no result yet" (then "busy with
    another task"). The hub gave his message to a direction; the direction raised a comment-batch executor on its own,
    the hub raised it out of the queue with his word (hub task go <executor> --for mN). The executor's task inherits his
    message as its chain root, yet it is the direction's work. The events go through the core's own reducer."""

    def setUp(self):
        patch = say("en")
        patch.start()
        self.addCleanup(patch.stop)
        self.st, self.seq = reducer.empty(), 0
        cli = {"channel": "cli", "owner": True}
        self.ev("session.requested", "hub-core", cwd="/h", session_id="s-hub", kind="hub", **cli)
        self.ev("session.started", "hub-core")
        self.ev("session.requested", "smm-platform", cwd="/smm", session_id="s-smm", role="agent", about="smm", **cli)
        self.ev("session.started", "smm-platform")
        self.m = f"m{self.ev('message.received', src='telegram', text='Resume the auto-comments in Matchbox', channel='telegram', owner=True)}"
        self.ev("turn.started", "hub-core", src="hook", marker=self.m, prompt_head="x")
        self.d = f"d{self.ev('delegation.created', session='smm-platform', context='Resume them if paused', **{'for': self.m}, for_session=None, **by('hub-core'))}"
        self.ev("turn.result", "hub-core", src="hook", text="passed")

    def ev(self, type, entity=None, src=None, **data):
        self.seq += 1
        e = {"seq": self.seq, "ts": NOW + self.seq, "type": type, "entity": entity, "data": data,
             **({"src": src} if src else {})}
        self.st, _, why = reducer.reduce(self.st, e)
        self.assertIsNone(why, (type, data))
        return self.seq

    def raise_executor(self, name):
        """The direction asks for an executor in its turn on his task, the queue holds it, the hub raises it with his word."""
        self.ev("task.run_requested", name, task="Comment batch to the writer", **by("smm-platform"))
        self.ev("task.run_held", name, module="lean", reason="memory is tight", channel="core", owner=False)
        self.ev("task.run_go", name, **{"for": self.m}, **by("hub-core"))
        run = self.st["runs"][name]
        for type, entity, data in executors.events_for(self.st, name, run, cwd=f"/smm/.claude/worktrees/{name}",
                                                       worktree=None):
            self.ev(type, entity, **data)
        self.ev("session.started", name)
        mine = [d for d in self.st["delegations"].values() if d["session"] == name]
        self.assertEqual([d["root"] for d in mine], [self.m])   # the chain does grow from his message
        return mine[0]

    def test_an_executor_the_hub_raised_with_his_word_is_not_his_task(self):
        self.ev("turn.started", "smm-platform", src="hook", marker=f"m{self.d[1:]}", prompt_head="x")
        ex = self.raise_executor("cmt-batch-802")
        self.ev("turn.result", "smm-platform", src="hook", text="They were on already.")
        self.ev("delegation.done", self.d, **by("hub-core"))
        self.assertIn(ex["state"], main.OPEN)
        self.assertEqual(main.digest(self.st, NOW), "")                          # "Sub-task: no result yet"
        self.st["sessions"]["cmt-batch-802"].update(state="busy", delegation="d999")   # busy with another task
        self.st["delegations"]["d999"] = task("d999", "cmt-batch-802", "", **{"for": None, "for_session": "x"})
        self.assertEqual(main.digest(self.st, NOW), "")                          # "Sub-task: busy with another task"

    def test_the_owners_task_the_hub_gave_a_direction_stays(self):
        self.assertEqual(rows(main.digest(self.st, NOW)), ["- ⏸ **Resume the auto-comments** in Matchbox: no result yet"])
        self.ev("turn.started", "smm-platform", src="hook", marker=f"m{self.d[1:]}", prompt_head="x")
        self.raise_executor("cmt-batch-803")
        text = main.digest(self.st, NOW)
        self.assertEqual(text.splitlines()[0], "**📋 Open tasks · 1**")
        self.assertIn("Resume the auto-comments", text)
        self.assertEqual(len(rows(text)), 1)                                    # the executor is not a row of its own
        self.assertNotIn("Sub-task", text)


class Language(unittest.TestCase):
    """Texts go to Telegram in the hub language: English by default, Russian when the hub speaks Russian."""

    def st(self):
        return world([session("dev", "idle"), session("ex", "idle", pending=[{"id": "q"}], gist={"text": "Emoji: pick"})],
                     [task("d1", "dev", "Fix the bot", title="Fix the bot"), side("d11", "ex", "m1")])

    def test_english_is_the_default(self):
        with mock.patch("core.i18n.current", side_effect=RuntimeError("no hub language")):
            self.assertEqual(main._lang(), "en")
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**📋 Open tasks · 1**")

    def test_the_language_is_the_hubs(self):
        with mock.patch("core.i18n.current", return_value="ru"):
            self.assertEqual(main._lang(), "ru")
            self.assertEqual(main.digest(self.st(), NOW).splitlines(), [
                "**📋 Открытые задачи · 1**", "", "**Fix the bot**",
                "- ⏭ **Emoji**: pick → ждёт вашего слова"])

    def test_an_unknown_language_falls_back_to_english(self):
        with say("de"):
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**📋 Open tasks · 1**")
        with say("ru-RU"):   # a regional variant of a known language
            self.assertEqual(main.digest(self.st(), NOW).splitlines()[0], "**📋 Открытые задачи · 1**")

    def test_both_tables_have_the_same_keys_and_placeholders(self):
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
        self.assertEqual(texts[0].splitlines()[0], "**📋 Open tasks · 1**")
        self.assertEqual(texts[1].splitlines()[0], "**📋 Открытые задачи · 1**")

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
