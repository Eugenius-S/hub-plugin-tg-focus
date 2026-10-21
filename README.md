# tg-focus - only the important in Telegram

An [Agents Hub](https://github.com/flagman/agents-hub-core) plugin: the Telegram bot sends the owner results and questions, not every handoff of work between sessions, and every N minutes one digest of their open tasks.

- **Handoff silence.** A session handed work on (a task for another session, a message to it, `--fyi`) - the bot holds its interim result back. A result for the task or a reply to your message arrived - the held one is dropped. The chain ended without a result - the held one goes out as one message. A handoff is recognised by core facts, not by text. It is turned on at the plugin's first start (`telegram.focus` in `hub.json`); if you turned it off, the plugin leaves it alone.
- **Task digest.** Every 30 minutes (`telegram.digest_minutes`, 5-1440) - one message: your open tasks, each with what the task is, who is doing it and its state: waiting for you, stuck for N min (the core nudges or the hub decides), working, executors are working, session asleep. A new digest erases the previous one; nothing open - the previous one is erased and no new one comes. Only tasks you set; executors' sub-tasks and tasks sessions give each other are not included. Switch: `telegram.digest`. The digest covers this hub only: tasks of another hub on another machine are not visible in it.
- **Settings** - "Settings -> Only the important".

Needs a hub with the `telegram.focus` bot option and the `telegram.digest {key, text}` fact.

## Language

Everything the plugin shows is in two languages, English and Russian: the digest text that goes to Telegram follows the hub language (`hub.json` `language`, then the owner's language, then English), and the settings block follows the page language (`hub.lang`). Any other language falls back to English. The manifest description is English, the Russian text is under `i18n.ru` in `hub-plugin.json`.

## Tests

```
PYTHONPATH=<hub code folder> HUB_STATE_DIR=<temp dir> HUB_CONFIG=<temp file> python3 -m unittest discover -s tests
```

Icon — [3D Fluency](https://icons8.com/icons/3d-fluency) by Icons8.
