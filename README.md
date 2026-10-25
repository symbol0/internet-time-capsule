# Internet Time Capsule

A Telegram bot that keeps a personal archive. Save a note or a link, seal it until a date, or let old notes come back on their own. Nothing here uses a language model.

Text and links are stored. Telegram file bytes are not downloaded, so a photo or document is never described as saved.

## Run locally

Python 3.12, 3.13, or 3.14. Python 3.15 cannot install aiogram yet.

```bash
py -3.14 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\python bot.py
```

Put the BotFather token in `BOT_TOKEN` (or `bot_token`). A token missing the colon between the bot id and the secret is repaired on startup. Set `ADMIN_ID` to your Telegram user id if this archive should stay private. Leave it empty and every account gets a separate vault.

```bash
pytest
```

The database, exports, and backups live in `data/` locally. That directory is not committed.

## Commands

`/start` `/save` `/today` `/vault` `/search` `/seal` `/tags` `/stats` `/export` `/import` `/settings` `/delete`

Forward a message to save its text. `/save buy milk` saves immediately and asks when to bring it back: tomorrow, 30 days, 1 year, a specific date, or keep it available.

`/today` opens sealed notes whose date has arrived, then the oldest eligible rediscoveries, up to the daily limit. The same note is not sent twice inside 90 days. A sealed note is never picked early just because it is old.

When a note comes back, mark it started, still interesting, useful, or not relevant. `/stats` keeps those counts.

`/export` sends a JSON backup. `/import` restores that file and replaces the current vault. Daily SQLite snapshots are written under `data/backups/`.

## Time

Timestamps are stored in UTC. Display, the delivery clock, and "today" use the timezone from `/settings`.

Open notes become eligible for rediscovery after 30 days. The oldest ones go first. Sealed notes wait for their unlock time, then arrive as a scheduled opening, separate from rediscovery.

## FadeHost

Polling does not need a web address, so the free bot stays awake. Do not attach a sleeping web address unless you move to webhooks on a paid plan.

The app directory is replaced on every deploy. The bot stores its SQLite database under `/data/timecapsule` when `/data` is writable. Set `DATA_DIR` only if you want a different folder.

Environment:

- `BOT_TOKEN` or `TELEGRAM_BOT_TOKEN`
- `ADMIN_ID`
- start command: `python bot.py`

A paid backup of `/data` is the off-site copy. The JSON export is the copy you can keep yourself.
