# tg-focus - only the important in Telegram

An [Agents Hub](https://github.com/flagman/agents-hub-core) plugin: the Telegram bot sends the owner results and questions, not every handoff of work between sessions, and every N minutes one digest of their open tasks.

- **Handoff silence.** A session handed work on (a task for another session, a message to it, `--fyi`) - the bot holds its interim result back. A result for the task or a reply to your message arrived - the held one is dropped. The chain ended without a result - the held one goes out as one message. A handoff is recognised by core facts, not by text. It is turned on at the plugin's first start (`telegram.focus` in `hub.json`); if you turned it off, the plugin leaves it alone.
- **Task digest.** Every 30 minutes (`telegram.digest_minutes`, 5-1440) - one message grouped by your tasks, not by statuses. A task is the chain of work that grew from one message of yours: a bold heading (the task title - `hub task new --title` - else your words), under it its open sub-tasks, one row each: `- <emoji> **Name**: gist -> stage`. Emoji are the status: ⏭ waits for your word (or the session is busy with another task), 🔄 in progress (or sub-tasks are going), ⏸ stands without a result (stalled rows say since when), ⚠️ no session or it crashed. Blockers go first. A row is up to ~70 characters, no session names, ids, paths or code; at most four sub-tasks under a task, then `and N more`; a task without sub-tasks is one row; at most eight tasks, then `and N more tasks`. Status and blocker come from the hub's state only - what the hub does not know is not written. A new digest erases the previous one; nothing open - the previous one is erased and no new one comes. Only tasks you set; tasks sessions give each other are not included. Switch: `telegram.digest`. The digest covers this hub only: tasks of another hub on another machine are not visible in it.
- **Settings** - "Settings -> Only the important".

Needs a hub with the `telegram.focus` bot option and the `telegram.digest {key, text}` fact.

## Language

Everything the plugin shows is in two languages, English and Russian: the digest text that goes to Telegram follows the hub language (`hub.json` `language`, then the owner's language, then English), and the settings block follows the page language (`hub.lang`). Any other language falls back to English. The manifest description is English, the Russian text is under `i18n.ru` in `hub-plugin.json`.

## Tests

```
PYTHONPATH=<hub code folder> HUB_STATE_DIR=<temp dir> HUB_CONFIG=<temp file> python3 -m unittest discover -s tests
```

Icon — [3D Fluency](https://icons8.com/icons/3d-fluency) by Icons8.
