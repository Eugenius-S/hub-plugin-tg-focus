"""Process of the "Only the important" plugin: turns on handoff silence in the Telegram bot (telegram.focus) and every N
minutes sends the owner one digest of their open tasks (telegram.digest: the bot erases the previous one). Nothing
open - the previous digest is erased and no new one is sent. The digest covers this hub only: tasks of another
machine are not visible here. It does not touch the core: it reads the journal.

The digest text goes to Telegram in the hub language (core.i18n.current(): hub.json language -> owner language ->
en), resolved at the moment of sending; any language without a table here falls back to English."""
import json
import os
import sys
import time

from core import catalog, delegations, minihub, spool, tail, views

EXT = "tg-focus"
KEY = "tasks"            # digest key at the bot: a new one with the same key erases the previous one
TICK = 5                 # s: how often to read the journal
DEFAULT_MINUTES = 30
OPEN = ("working", "waiting-owner")
# The bot marks the owner's voice messages with this prefix (a protocol marker of the core, not UI text): a
# microphone emoji and the Russian word for "voice" plus a colon, spelled by code points to keep this file English.
VOICE_MARK = chr(0x1F3A4) + " " + "".join(map(chr, (0x433, 0x43E, 0x43B, 0x43E, 0x441))) + ":"

STR = {
    "en": {
        "no_session": "no session, the hub decides",
        "waiting": "waiting for you",
        "stuck": "stuck for {mins} min, {who}",
        "who_hub": "the hub decides",
        "who_core": "the core is nudging",
        "failed": "session failed, the hub decides",
        "executors": "executors are working",
        "asleep": "session asleep",
        "working": "working",
        "queued": "queued",
        "header": "**Open tasks: {n}**",
    },
    "ru": {
        "no_session": "сессии нет, решает хаб",
        "waiting": "ждёт тебя",
        "stuck": "стоит {mins} мин, {who}",
        "who_hub": "решает хаб",
        "who_core": "ядро напоминает",
        "failed": "сессия упала, решает хаб",
        "executors": "работают исполнители",
        "asleep": "сессия спит",
        "working": "работает",
        "queued": "в очереди",
        "header": "**Открытые задачи: {n}**",
    },
}


def _lang():
    try:
        from core import i18n
        return i18n.current()      # hub language chain: hub.json language -> owner language -> en
    except Exception:
        return "en"


def tr(lang, key, **params):
    """Text of a key in the language (any language without a table - English); {name} placeholders."""
    base = (lang or "en").split("-")[0].lower()
    text = (STR.get(base) or STR["en"]).get(key) or STR["en"][key]
    return text.format(**params) if params else text


def emit(state, type, data):
    spool.put(os.path.join(state, "spool"), type, None, data, src=f"ext:{EXT}")


def settings(state):
    tg = catalog.load_config(state).get("telegram") or {}
    try:
        minutes = max(5, min(1440, int(tg.get("digest_minutes") or DEFAULT_MINUTES)))
    except (TypeError, ValueError):
        minutes = DEFAULT_MINUTES
    return tg.get("digest") is not False, minutes


def mine(d):
    """The owner's task: set by them (mN), not by a session, not a sub-task of an executor."""
    return bool(d.get("for")) and not d.get("for_session") and not d.get("parent") and d.get("state") in OPEN


def title(d):
    text = (d.get("words") or d.get("context") or "").strip()
    first = text.splitlines()[0] if text else d["id"]
    first = first.removeprefix(VOICE_MARK).strip() or d["id"]   # the bot marks the owner's voice message this way
    return first if len(first) <= 60 else first[:59].rstrip() + "…"


def status(st, d, now, lang):
    """(order, words): waiting for the owner - first, then the stuck ones, then the running ones."""
    sess = st["sessions"].get(d["session"])
    if sess is None or sess.get("state") == "removed":
        return 1, tr(lang, "no_session")
    if d["state"] == "waiting-owner" or views.session_status(sess) == "waiting":
        return 0, tr(lang, "waiting")
    since = delegations.stuck(st, d)
    if since is not None:
        mins = max(1, round((now - since) / 60))
        hub_decides = d.get("timed_out") or (d.get("nudges") or 0) >= len(delegations.NUDGES)
        return 1, tr(lang, "stuck", mins=mins, who=tr(lang, "who_hub" if hub_decides else "who_core"))
    s = views.session_status(sess)
    if s == "failed":
        return 1, tr(lang, "failed")
    if minihub.waiting_on(st, d):
        return 2, tr(lang, "executors")
    if s == "asleep":
        return 2, tr(lang, "asleep")
    return 2, tr(lang, "working" if s == "working" else "queued")


def digest(st, now, lang=None):
    """The digest text or "" (nothing open). lang - hub language by default, resolved now."""
    lang = lang or _lang()
    rows = []
    for d in st.get("delegations", {}).values():
        if mine(d):
            order, what = status(st, d, now, lang)
            rows.append((order, d.get("created") or 0, f"• {title(d)} — {d['session']}: {what}"))
    if not rows:
        return ""
    rows.sort()
    return tr(lang, "header", n=len(rows)) + "\n" + "\n".join(r[2] for r in rows)


class Plugin:
    def __init__(self, state_dir, clock=time.time):
        self.dir, self.clock = state_dir, clock
        self.tail = tail.Tail(os.path.join(state_dir, "journal.jsonl"))
        self.mine = os.path.join(state_dir, EXT, "state.json")
        try:   # the last digest ("" - erased) and when it went survive a hub restart: it does not resend the digest
            with open(self.mine, encoding="utf-8") as f:
                saved = json.load(f)
            self.sent, self.at = saved.get("text"), float(saved.get("at") or 0)
        except (OSError, ValueError, AttributeError):
            self.sent, self.at = None, 0

    def save(self, text, now):
        self.sent, self.at = text, now
        os.makedirs(os.path.dirname(self.mine), exist_ok=True)
        with open(self.mine + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"text": text, "at": now}, f, ensure_ascii=False)
        os.replace(self.mine + ".tmp", self.mine)

    def focus_on(self):
        """Handoff silence is turned on once, on first meeting: if the owner turned it off (the key exists) - leave it."""
        path = catalog.config_path(self.dir)
        with catalog.edit_settings(path) as raw:
            tg = raw.setdefault("telegram", {})
            if "focus" in tg:
                return False
            tg["focus"] = True
        return True

    def tick(self):
        self.tail.poll()
        on, minutes = settings(self.dir)
        now = self.clock()
        text = digest(self.tail.reader.state, now) if on else ""
        if not text:
            if self.sent != "":   # there was something (or we do not know) - erase once
                emit(self.dir, "telegram.digest", {"key": KEY, "text": ""})
                self.save("", now)
            return
        if self.sent and now - self.at < minutes * 60:
            return
        emit(self.dir, "telegram.digest", {"key": KEY, "text": text})
        self.save(text, now)


def main():
    state = os.environ.get("HUB_STATE_DIR") or os.path.expanduser("~/.hub-core")
    p = Plugin(state)
    try:
        p.focus_on()
    except (OSError, ValueError) as e:
        print(f"{EXT}: could not turn on handoff silence: {e}", file=sys.stderr, flush=True)
    while True:
        try:
            p.tick()
        except Exception as e:  # noqa: BLE001 - the plugin process lives on
            print(f"{EXT}: {e}", file=sys.stderr, flush=True)
        time.sleep(TICK)


if __name__ == "__main__":
    main()
