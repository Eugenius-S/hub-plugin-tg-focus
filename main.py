"""Процесс плагина «Только важное»: включает у бота Telegram тишину передач (telegram.focus) и раз в N минут шлёт
владельцу одну сводку его открытых задач (telegram.digest: бот стирает прежнюю). Открытых нет — прежняя стирается,
новая не уходит. Сводка — только этого хаба: задачи другой машины здесь не видны. Ядро не правит: читает журнал."""
import json
import os
import sys
import time

from core import catalog, delegations, minihub, spool, tail, views

EXT = "tg-focus"
KEY = "tasks"            # ключ сводки у бота: новая с тем же ключом стирает прежнюю
TICK = 5                 # с: как часто читать журнал
DEFAULT_MINUTES = 30
OPEN = ("working", "waiting-owner")


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
    """Задача владельца: поставил он (mN), не сессия, не подзадача исполнителя."""
    return bool(d.get("for")) and not d.get("for_session") and not d.get("parent") and d.get("state") in OPEN


def title(d):
    text = (d.get("words") or d.get("context") or "").strip()
    first = text.splitlines()[0] if text else d["id"]
    first = first.removeprefix("🎤 голос:").strip() or d["id"]   # так бот помечает голосовое владельца
    return first if len(first) <= 60 else first[:59].rstrip() + "…"


def status(st, d, now):
    """(порядок, слова): ждёт владельца — первым, потом застрявшие, потом идущие."""
    sess = st["sessions"].get(d["session"])
    if sess is None or sess.get("state") == "removed":
        return 1, "сессии нет, решает хаб"
    if d["state"] == "waiting-owner" or views.session_status(sess) == "waiting":
        return 0, "ждёт тебя"
    since = delegations.stuck(st, d)
    if since is not None:
        mins = max(1, round((now - since) / 60))
        who = "решает хаб" if d.get("timed_out") or (d.get("nudges") or 0) >= len(delegations.NUDGES) else "ядро напоминает"
        return 1, f"стоит {mins} мин, {who}"
    s = views.session_status(sess)
    if s == "failed":
        return 1, "сессия упала, решает хаб"
    if minihub.waiting_on(st, d):
        return 2, "работают исполнители"
    if s == "asleep":
        return 2, "сессия спит"
    return 2, "работает" if s == "working" else "в очереди"


def digest(st, now):
    """Текст сводки или "" (открытых нет)."""
    rows = []
    for d in st.get("delegations", {}).values():
        if mine(d):
            order, what = status(st, d, now)
            rows.append((order, d.get("created") or 0, f"• {title(d)} — {d['session']}: {what}"))
    if not rows:
        return ""
    rows.sort()
    return f"**Открытые задачи: {len(rows)}**\n" + "\n".join(r[2] for r in rows)


class Plugin:
    def __init__(self, state_dir, clock=time.time):
        self.dir, self.clock = state_dir, clock
        self.tail = tail.Tail(os.path.join(state_dir, "journal.jsonl"))
        self.mine = os.path.join(state_dir, EXT, "state.json")
        try:   # последняя сводка ("" — стёрта) и когда ушла — переживают перезапуск хаба: он не шлёт сводку заново
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
        """Тишина передач включается один раз, при первой встрече: выключил владелец (ключ есть) — не трогать."""
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
            if self.sent != "":   # было что-то (или не знаем) — стереть один раз
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
        print(f"{EXT}: не включила тишину передач: {e}", file=sys.stderr, flush=True)
    while True:
        try:
            p.tick()
        except Exception as e:  # noqa: BLE001 — процесс плагина живёт дальше
            print(f"{EXT}: {e}", file=sys.stderr, flush=True)
        time.sleep(TICK)


if __name__ == "__main__":
    main()
