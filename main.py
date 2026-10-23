"""Process of the "Only the important" plugin: turns on handoff silence in the Telegram bot (telegram.focus) and every N
minutes sends the owner one digest of their open tasks (telegram.digest: the bot erases the previous one). Nothing
open - the previous digest is erased and no new one is sent. The digest covers this hub only: tasks of another
machine are not visible here. It does not touch the core: it reads the journal.

The digest is grouped by the owner's tasks, not by statuses: a task is the chain of work that grew from one message
of the owner (the delegations with the same root). The task is a bold heading, under it its sub-tasks, one row each:
"- <status emoji> **Name**: gist -> stage", up to ~70 characters, no session names, ids and paths, at most four
sub-tasks under a task. A task without sub-tasks is one row. The status and the blocker come from the core state only
(the same emoji as the core's own day digest); what the core does not know is not written.

The digest text goes to Telegram in the hub language (core.i18n.current(): hub.json language -> owner language ->
en), resolved at the moment of sending; any language without a table here falls back to English."""
import datetime
import json
import os
import re
import sys
import time

from core import catalog, delegations, minihub, spool, tail, views

EXT = "tg-focus"
KEY = "tasks"            # digest key at the bot: a new one with the same key erases the previous one
TICK = 5                 # s: how often to read the journal
DEFAULT_MINUTES = 30
OPEN = ("working", "waiting-owner")
NAME_WORDS = 4           # the task's name in a row: up to four words, bold
ROW = 70                 # a row as a whole: up to ~70 characters, on a phone no longer than two lines
SUBS = 4                 # sub-tasks under a task; the rest - "and N more"
TASKS = 8                # tasks in the digest; the rest - "and N more tasks"
# Status emoji and their order (blockers first) are the core's DAY_ICON / DAY_ORDER of the owner digest.
ICON = {"owner": "⏭", "busy": "⏭", "subtasks": "🔄", "working": "🔄", "idle": "⏸", "stopped": "⚠️"}
ORDER = ("owner", "busy", "stopped", "working", "subtasks", "idle")
# "Need from you: ..." in a session's result: the protocol phrase of the core (English or Russian), spelled by code points
NEED = re.compile(r"(?im)^\W*(?:" + "".join(map(chr, (1085, 1091, 1078, 1085, 1086, 32, 1086, 1090, 32, 1090, 1077, 1073, 1103)))
                  + r"|need from you)\s*:\s*(\S.*)$")
# The bot marks the owner's voice messages with this prefix (a protocol marker of the core, not UI text): a
# microphone emoji and the Russian word for "voice" plus a colon, spelled by code points to keep this file English.
VOICE_MARK = chr(0x1F3A4) + " " + "".join(map(chr, (0x433, 0x43E, 0x43B, 0x43E, 0x441))) + ":"

STR = {
    "en": {
        "head": "**📋 Open tasks · {n}**",
        "task": "Task",
        "subtask": "Sub-task",
        "owner": "waiting for your word",
        "busy": "busy: {what}",
        "busy_bare": "busy with another task",
        "working": "in progress",
        "subtasks": "sub-tasks in progress",
        "idle": "no result yet",
        "asleep": "session asleep",
        "no_session": "no session",
        "failed": "session failed",
        "stuck_hub": "the hub decides",
        "stuck_core": "the core is nudging",
        "since": "since {date}",
        "more": "and {n} more",
        "more_tasks": "and {n} more tasks",
    },
    "ru": {
        "head": "**📋 Открытые задачи · {n}**",
        "task": "Задача",
        "subtask": "Подзадача",
        "owner": "ждёт твоего слова",
        "busy": "занят: {what}",
        "busy_bare": "занят другой задачей",
        "working": "в работе",
        "subtasks": "идут подзадачи",
        "idle": "итога пока нет",
        "asleep": "сессия спит",
        "no_session": "сессии нет",
        "failed": "сессия упала",
        "stuck_hub": "решает хаб",
        "stuck_core": "ядро напоминает",
        "since": "с {date}",
        "more": "и ещё {n}",
        "more_tasks": "и ещё задач: {n}",
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


_MARKS = re.compile(r"\[(?:Image|Video|Audio|File)\s*#\d+\]|\[hub:[^\]]*\]|\b[dmq]\d{3,}\b")   # hub labels and ids
_MARKUP = re.compile(r"[*_`#>]+|\[([^\]]*)\]\([^)]*\)")
_CODE = re.compile(r"\s*\([^()`\n]*`[^`\n]*`[^()\n]*\)|\s*`[^`\n]*`")                  # `code` and brackets around it
_PATH = re.compile(r"(?<![\w/.:-])(?:~|[A-Za-z]:)?[\\/](?:[\w.\-]+[\\/])+[\w.\-]*")   # file paths, POSIX and Windows
_NAME_SPLIT = re.compile(r"\s*(?::|\s·|\s—|\s–)\s+")    # "Name: ...", "Name · ...", "Name — ..."
_SHORT = re.compile(r"[^\W\d_]{1,2}")                    # a preposition or conjunction: does not end a name


def clean(text):
    """One line of plain words: the owner's voice mark, hub labels, ids, code, paths and markdown are gone."""
    text = _PATH.sub(" ", _CODE.sub(" ", _MARKS.sub(" ", str(text or "")))).replace(VOICE_MARK, " ")
    text = _MARKUP.sub(lambda m: m.group(1) or "", text)
    return re.sub(r"\s+([,.;:!?)])", r"\1", " ".join(text.split())).strip()


def cut(text, limit):
    """By a word; the ellipsis is inside the limit."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit - 1]
    word = head.rsplit(" ", 1)[0] if " " in head and text[limit - 1] != " " else head
    while " " in word and _SHORT.fullmatch(word.rsplit(" ", 1)[1]):   # no dangling preposition before the ellipsis
        word = word.rsplit(" ", 1)[0]
    return word.rstrip(" ,;:—–-") + "…"


def split_name(text):
    """(name, rest, separator): the name is what stands before a ":" within the first NAME_WORDS words, the rest is
    the gist (": "); no mark - the first words, the rest goes on as a phrase (" ")."""
    text = clean(text)
    mark = _NAME_SPLIT.search(text)
    if mark and mark.start() and len(text[:mark.start()].split()) <= NAME_WORDS:
        name, rest, sep = text[:mark.start()], text[mark.end():], ": "
    else:
        words = text.split()
        n = NAME_WORDS
        while n > 1 and len(words) > n - 1 and _SHORT.fullmatch(words[n - 1]):
            n -= 1                      # a preposition does not hold the name: it goes into the rest
        name, rest, sep = " ".join(words[:n]), " ".join(words[n:]), " "
    name = name.strip(" .,;:")
    return name[:1].upper() + name[1:], rest.strip(), sep


def lower(text):
    """The gist after the colon starts lower-case if its first word is an ordinary one, not a name."""
    words = text.split(" ", 2)
    if len(words[0]) > 1 and words[0][0].isupper() and words[0][1:].islower() and \
            (len(words) < 2 or not words[1][:1].isupper()):
        return text[0].lower() + text[1:]
    return text


def row(text, extra="", room=ROW):
    """A row "- **Name**: gist -> extra", the whole up to room characters: a long gist is cut at the tail, extra (the
    stage, the reason and the date) stays."""
    name, rest, sep = split_name(text)
    extra = cut(clean(extra), max(room - len(name) - 2, 20))
    left = room - len(name) - (len(extra) + 2 if extra else 0) - len(sep)
    rest = cut(rest, max(left, 12)) if rest and left >= 12 else ""
    rest = lower(rest) if rest and sep != " " else rest
    rest = "" if not rest.strip(" .,;:…") else rest          # only a stump "…" is left of the gist
    if rest and sep == " ":              # the phrase goes on past the bold: "**Made two modes** and …: reason"
        return f"- **{name}** {rest}" + (f": {extra}" if extra else "")
    return f"- **{name}**" + (f": {rest}" if rest else "") + (f"{' → ' if rest else ': '}{extra}" if extra else "")


def when(ts, st):
    """Date "DD.MM" of a moment in the owner's time zone (the machine's one when it is not known)."""
    try:
        import zoneinfo
        zone = zoneinfo.ZoneInfo((st.get("owner") or {}).get("tz") or "")
    except Exception:   # noqa: BLE001 - no zone or no tz database: the machine's time
        zone = None
    return datetime.datetime.fromtimestamp(ts, zone).strftime("%d.%m")


def need(reply):
    """What the session asks of the owner ("Need from you: ..." in its result), or ""."""
    got = NEED.search(str(reply or ""))
    return clean(got[1]).rstrip(" .") if got else ""


def chains(st):
    """{root: [delegations, oldest first]} of the owner's tasks with open work: a task is the chain that grew from one
    message of the owner (its delegations carry the message id as root; before that field - the head's own "for").
    Whatever a session started on its own is not the owner's task."""
    out = {}
    for d in sorted(st.get("delegations", {}).values(), key=lambda d: d.get("created") or d.get("since") or 0):
        root = d.get("root") or (d.get("for") if not d.get("for_session") else None)
        if root:
            out.setdefault(root, []).append(d)
    return {root: ds for root, ds in out.items() if any(d.get("state") in OPEN for d in ds) and (
        (st["messages"].get(root) or {}).get("from_owner")
        or any(d.get("for") == root and not d.get("for_session") for d in ds))}


def heading(st, d, root, head):
    """(text, own): the line that names the work. The head - its title or the owner's words (the message the chain grew
    from when the head has none). A sub-task - its title, else what its session says it does (own is True: that text
    is already the stage), else the words of the brief. Never an id or a session name."""
    sess = st["sessions"].get(d.get("session")) or {}
    gist, recap = (sess.get("gist") or {}).get("text"), (sess.get("recap") or {}).get("text")
    words = [d.get("words"), (st["messages"].get(root) or {}).get("text")] if head else [d.get("words")]
    for text, own in [(d.get("title"), False), *([] if head else [(gist, True), (recap, True)]), *[(w, False) for w in words],
                      (gist, True), (recap, True)]:
        if clean(text):
            return clean(text), own and sess.get("delegation") == d.get("id")
    return "", False


def status(st, d, kids, lang):
    """(kind, stage) of one task from the core state only: waits for the owner, busy with another task, in progress
    (what its session says), waits for its sub-tasks, stands (since when), no session or crashed."""
    if d["state"] == "waiting-owner":
        return "owner", need(d.get("reply")) or tr(lang, "owner")
    sess = st["sessions"].get(d.get("session"))
    if sess is None or sess.get("state") == "removed":
        return "stopped", tr(lang, "no_session")
    s = views.session_status(sess)
    if s == "waiting":
        return "owner", tr(lang, "owner")
    if s == "failed":
        return "stopped", tr(lang, "failed")
    if s == "working" and sess.get("delegation") == d["id"]:
        say = (sess.get("recap") or {}).get("text") or (sess.get("gist") or {}).get("text")
        return "working", clean(say) or tr(lang, "working")
    if kids or minihub.waiting_on(st, d):
        return "subtasks", tr(lang, "subtasks")
    stuck = delegations.stuck(st, d)
    if stuck is not None:
        hub_decides = d.get("timed_out") or (d.get("nudges") or 0) >= len(delegations.NUDGES)
        why = tr(lang, "stuck_hub" if hub_decides else "stuck_core")
        return "idle", f"{why} · {tr(lang, 'since', date=when(stuck, st))}"
    if s == "working":
        other = (st["delegations"].get(sess.get("delegation") or "")) or {}
        what = split_name(heading(st, other, other.get("root"), True)[0])[0] if other else ""
        return "busy", tr(lang, "busy", what=what) if what else tr(lang, "busy_bare")
    return "idle", tr(lang, "asleep" if s == "asleep" else "idle")


def line(kind, text, stage):
    """A task or sub-task as a row: "- <emoji> **Name**: gist -> stage", the whole up to ROW characters."""
    return f"- {ICON[kind]} " + row(text, stage, ROW - len(ICON[kind]) - 1)[2:]


def group(st, root, ds, lang):
    """One task: (rank, ("lone", row)) for a task without open sub-tasks or (rank, ("chain", lines)); the rank is the
    worst status inside (blockers first), then the age."""
    ids = {d["id"] for d in ds}
    head = next((d for d in ds if d.get("for") == root and not d.get("for_session")), ds[0])
    under = {}
    for d in ds:
        if d is not head:
            up = d.get("parent") if d.get("parent") in ids and d.get("parent") != head["id"] else head["id"]
            under.setdefault(up, []).append(d)

    def kids(d):
        return [k for k in under.get(d["id"], []) if k["state"] in OPEN]

    def below(d):   # everything under the task, flat: a sub-task below a sub-task is listed under the task
        return [x for k in kids(d) for x in (k, *below(k))]

    subs = []
    for k in below(head):
        kind, stage = status(st, k, kids(k), lang)
        text, own = heading(st, k, root, False)
        if kind == "working" and own:    # the name already says what the session does: no second phrase after it
            stage = ""
        subs.append((ORDER.index(kind), k.get("since") or 0, line(kind, text or tr(lang, "subtask"), stage)))
    subs.sort(key=lambda s: s[:2])
    title = heading(st, head, root, True)[0] or tr(lang, "task")
    own = status(st, head, kids(head), lang) if head["state"] in OPEN else None
    ranks = ([ORDER.index(own[0])] if own else []) + [s[0] for s in subs]
    rank = (min(ranks) if ranks else len(ORDER), head.get("created") or 0)
    if not subs:
        return rank, ("lone", line(own[0], title, own[1]) if own else f"- **{split_name(title)[0]}**")
    lines = [f"**{cut(title, ROW)}**"]
    if own and own[0] in ("owner", "stopped"):   # the head's own blocker must not drown under its sub-tasks
        lines.append(f"- {ICON[own[0]]} {cut(own[1], ROW - 2)}")
    lines += [s[2] for s in subs[:SUBS]]
    if len(subs) > SUBS:
        lines.append(f"- {tr(lang, 'more', n=len(subs) - SUBS)}")
    return rank, ("chain", lines)


def digest(st, now, lang=None):
    """The digest text or "" (nothing open): the owner's tasks, blockers first; a task is a bold heading with its open
    sub-tasks under it, a task without sub-tasks is one row. No session names, ids and paths. lang - hub language by
    default, resolved now."""
    lang = lang or _lang()
    found = sorted((group(st, root, ds, lang) for root, ds in chains(st).items()), key=lambda g: g[0])
    if not found:
        return ""
    shown = found[:TASKS]
    parts = ["\n".join(g[1]) for _, g in shown if g[0] == "chain"]
    lone = [g[1] for _, g in shown if g[0] == "lone"]
    if lone:
        parts.append("\n".join(lone))
    if len(found) > TASKS:
        parts.append(f"- {tr(lang, 'more_tasks', n=len(found) - TASKS)}")
    return "\n\n".join([tr(lang, "head", n=len(found)), *parts])


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
