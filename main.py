"""Process of the "Only the important" plugin: turns on handoff silence in the Telegram bot (telegram.focus) and every N
minutes sends the owner one digest of their open tasks (telegram.digest: the bot erases the previous one). Nothing
open - the previous digest is erased and no new one is sent. The digest covers this hub only: tasks of another
machine are not visible here. It does not touch the core: it reads the journal.

The digest is laid out like the recap: sections "waiting for you" / "in progress" / "stalled" (with a date), one row
per task - "- **Name**: gist" up to ~70 characters, no session names and ids, at most four rows in a section.

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
SECTION = 4              # rows in a section; the rest - "and N more"
# The bot marks the owner's voice messages with this prefix (a protocol marker of the core, not UI text): a
# microphone emoji and the Russian word for "voice" plus a colon, spelled by code points to keep this file English.
VOICE_MARK = chr(0x1F3A4) + " " + "".join(map(chr, (0x433, 0x43E, 0x43B, 0x43E, 0x441))) + ":"

STR = {
    "en": {
        "no_session": "no session",
        "stuck_hub": "the hub decides",
        "stuck_core": "the core is nudging",
        "failed": "session failed",
        "executors": "executors are working",
        "asleep": "session asleep",
        "queued": "queued",
        "wait_head": "**⚡ Waiting for you · {n}**",
        "work_head": "**🛠 In progress · {n}**",
        "stall_head": "**⏳ Stalled · {n}**",
        "since": "since {date}",
        "more": "and {n} more",
    },
    "ru": {
        "no_session": "сессии нет",
        "stuck_hub": "решает хаб",
        "stuck_core": "ядро напоминает",
        "failed": "сессия упала",
        "executors": "работают исполнители",
        "asleep": "сессия спит",
        "queued": "в очереди",
        "wait_head": "**⚡ Ждут тебя · {n}**",
        "work_head": "**🛠 В работе · {n}**",
        "stall_head": "**⏳ Без движения · {n}**",
        "since": "с {date}",
        "more": "и ещё {n}",
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


_MARKS = re.compile(r"\[(?:Image|Video|Audio|File)\s*#\d+\]|\[hub:[^\]]*\]|\b[dmq]\d{3,}\b")   # hub labels and ids
_MARKUP = re.compile(r"[*_`#>]+|\[([^\]]*)\]\([^)]*\)")
_NAME_SPLIT = re.compile(r"\s*(?::|\s·|\s—|\s–)\s+")    # "Name: ...", "Name · ...", "Name — ..."
_SHORT = re.compile(r"[^\W\d_]{1,2}")                    # a preposition or conjunction: does not end a name


def clean(text):
    """One line of plain words: the owner's voice mark, hub labels, ids and markdown are gone."""
    text = _MARKS.sub(" ", str(text or "")).replace(VOICE_MARK, " ")
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


def row(text, extra=""):
    """A row "- **Name**: gist", the whole up to ROW characters: a long gist is cut at the tail, extra (the reason
    and the date) stays."""
    name, rest, sep = split_name(text)
    extra = cut(clean(extra), max(ROW - len(name) - 2, 20))
    room = ROW - len(name) - (len(extra) + 2 if extra else 0) - len(sep)
    rest = cut(rest, max(room, 12)) if rest and room >= 12 else ""
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


def section(lang, head, rows):
    """"**Head · N**", up to SECTION rows, then "- and N more"; nothing in it - no section."""
    if not rows:
        return []
    more = len(rows) - SECTION
    return [tr(lang, head, n=len(rows)), *rows[:SECTION]] + ([f"- {tr(lang, 'more', n=more)}"] if more > 0 else [])


def status(st, d, now, lang):
    """(section, reason, since): section 0 - waits for the owner, 1 - stalled (with a date), 2 - going."""
    sess = st["sessions"].get(d["session"])
    since = d.get("since") or d.get("created") or now
    if sess is None or sess.get("state") == "removed":
        return 1, tr(lang, "no_session"), since
    if d["state"] == "waiting-owner" or views.session_status(sess) == "waiting":
        return 0, "", since
    stuck = delegations.stuck(st, d)
    if stuck is not None:
        hub_decides = d.get("timed_out") or (d.get("nudges") or 0) >= len(delegations.NUDGES)
        return 1, tr(lang, "stuck_hub" if hub_decides else "stuck_core"), stuck
    s = views.session_status(sess)
    if s == "failed":
        return 1, tr(lang, "failed"), since
    if minihub.waiting_on(st, d):
        return 2, tr(lang, "executors"), since
    if s == "asleep":
        return 2, tr(lang, "asleep"), since
    return 2, "" if s == "working" else tr(lang, "queued"), since


def digest(st, now, lang=None):
    """The digest text or "" (nothing open): sections waiting for the owner / in progress / stalled. No session
    names and ids. lang - hub language by default, resolved now."""
    lang = lang or _lang()
    groups = ([], [], [])
    for d in sorted((d for d in st.get("delegations", {}).values() if mine(d)), key=lambda d: d.get("created") or 0):
        sec, why, since = status(st, d, now, lang)
        text = d.get("title") or d.get("words") or d.get("context") or d["id"]   # the title is short, the words are speech
        if sec == 1:
            groups[1].append(row(text, f"{why} · {tr(lang, 'since', date=when(since, st))}"))
        else:   # the section already says "waiting" / "going": the state is only a stand-in for a missing gist
            groups[sec].append(row(text, why if sec == 2 and not split_name(text)[1] else ""))
    parts = (section(lang, "wait_head", groups[0]), section(lang, "work_head", groups[2]),
             section(lang, "stall_head", groups[1]))
    return "\n\n".join("\n".join(p) for p in parts if p)


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
