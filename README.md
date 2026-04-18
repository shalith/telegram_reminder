# Telegram Reminder MVP — Phase 6.5 Self-Learning

This version adds self-learning from confirmations and corrections:
- learns risky message patterns from cancelled/edited confirmations
- learns successful phrasing from confirmed reminders
- lowers confidence for phrases that were corrected before
- raises confidence for similar phrases that were confirmed before
- applies learned time-pattern rewrites before interpretation

No new environment variables are required for this patch. New SQLite tables are created automatically on startup.

# Telegram Reminder Bot — Phase 6.1 Conversation Maturity + Phase 6.2 Learning Loop

This project is a local-machine Telegram reminder assistant with:

- one-time reminders
- recurring reminders (daily, weekdays, weekly by day)
- wake-up alerts that require acknowledgement
- repeat-until-ack logic with Snooze
- agentic intent understanding for create/list/update/delete flows
- daily agenda summaries
- deadline reminder chains
- user preferences for snooze and wake-up retry behavior
- missed-reminder summaries
- optional Groq-powered extraction with safe fallback to rule-based parsing
- SQLite persistence so reminders survive restarts
- Telegram polling, so you can run it on your local machine without a public URL
- structured JSON logs
- local health endpoint
- CLI backup/export utilities
- systemd and Docker deployment files


This phase adds:

- better task-first reminder extraction such as `Remind me to go for Sony headset repair today morning 9am`
- stronger follow-up slot merging so replies like `Today morning 9` complete the draft reminder
- time phrase normalization for inputs like `today morning 9` and `18th apr morning 8`
- feedback capture for successes, failures, and follow-up gaps
- example memory so successful real conversations can be reused as prompt examples
- automatic eval-case candidate logging for failed interactions
- learned time-pattern storage for normalized phrases

## What Phase 5 adds

### 1. Structured logs

The app now writes:

- JSON logs to stdout when `JSON_LOGS=true`
- rotating file logs to `./logs/reminder_bot.log`

Useful events include:

- app start / stop
- scheduler start / restore / shutdown
- reminder sent / retried / missed
- daily agenda scheduled / sent
- bot startup

### 2. Health endpoint

A tiny HTTP server runs locally so you can check runtime health.

Default:

- `http://127.0.0.1:8088/health`
- `http://127.0.0.1:8088/metrics`

Example:

```bash
curl http://127.0.0.1:8088/health
curl http://127.0.0.1:8088/metrics
```

The health payload includes:

- database check
- scheduler running state
- scheduler job count
- bot-started flag
- runtime counters

### 3. Backup and export utilities

Create a DB backup:

```bash
python -m app.admin_cli backup-db
```

Export reminders as JSON:

```bash
python -m app.admin_cli export-reminders --format json --output ./exports/reminders.json
```

Export reminders as CSV:

```bash
python -m app.admin_cli export-reminders --format csv --output ./exports/reminders.csv
```

Export preferences:

```bash
python -m app.admin_cli export-preferences --output ./exports/preferences.json
```

### 4. Deployment quality

This phase adds:

- `deployment/systemd/reminder-bot.service`
- `deployment/docker/Dockerfile`
- `deployment/docker/docker-compose.yml`
- `scripts/install_systemd_service.sh`

These let you run the bot automatically on reboot or inside Docker.

### 5. Crash-safe recovery

The reminder engine still restores pending reminder jobs and daily agenda jobs from SQLite on startup.
That means:

- reboot the machine
- start the app again
- pending reminders are restored from DB

## Commands

- `/start`
- `/help`
- `/list`
- `/today`
- `/prefs`
- `/delete <reminder_id>`

## Project structure

```text
app/
  admin_cli.py          # backup/export utilities
  agent.py              # intent extraction layer (Groq + rule fallback)
  agent_schema.py       # structured agent decisions + deadline offsets
  assistant_features.py # deadline offsets, daily agenda helpers, local-day ranges
  config.py             # environment settings
  db.py                 # database setup
  health_server.py      # local health and metrics HTTP server
  logging_setup.py      # JSON + rotating file logging
  main.py               # startup entry point
  models.py             # reminders + conversation state + user preferences
  parser.py             # deterministic schedule parser used by the executor
  recurrence.py         # recurrence calculations and formatting
  runtime.py            # runtime counters for health/metrics
  scheduler.py          # APScheduler delivery + daily agenda scheduling
  service.py            # reminder CRUD, target resolution, preferences, summaries
  telegram_bot.py       # Telegram handlers + executor flow
deployment/
  docker/
  systemd/
scripts/
  install_systemd_service.sh
tests/
  test_agent.py
  test_parser.py
  test_phase4_helpers.py
  test_phase5_ops.py
```

## Local setup

### 1. Create the bot

- Open Telegram
- Search for `@BotFather`
- Create a new bot
- Copy the bot token

### 2. Create and activate a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Then update `.env`:

```env
TELEGRAM_BOT_TOKEN=your_real_token_here
DEFAULT_TIMEZONE=Asia/Singapore
DATABASE_URL=sqlite:///./reminders.db
GROQ_API_KEY=
GROQ_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
LOG_LEVEL=INFO
LOG_DIR=./logs
JSON_LOGS=true
HEALTH_HOST=127.0.0.1
HEALTH_PORT=8088
BACKUP_DIR=./backups
```

### 5. Important note if you already ran Phase 4

Phase 5 does not change the DB schema, so you can continue using your Phase 4 database.
Still, if you want a clean test run, you may remove the DB first:

```bash
rm -f reminders.db
```

### 6. Run the bot

```bash
python -m app.main
```

## Example operations after startup

Check health:

```bash
curl http://127.0.0.1:8088/health
```

Create a backup:

```bash
python -m app.admin_cli backup-db
```

Export reminders:

```bash
python -m app.admin_cli export-reminders --format csv --output ./exports/reminders.csv
```

## systemd auto-start on reboot

From the project root:

```bash
bash scripts/install_systemd_service.sh
```

Then check:

```bash
systemctl status reminder-bot@$(whoami) --no-pager
journalctl -u reminder-bot@$(whoami) -f
```

## Docker run

From the project root:

```bash
cp .env.example .env
# edit .env first
mkdir -p data logs backups
cd deployment/docker
docker compose up --build -d
```

Then:

```bash
mkdir -p ../../data
docker compose logs -f
curl http://127.0.0.1:8088/health
```

## Groq behavior

- If `GROQ_API_KEY` is set, the bot attempts Groq extraction first.
- If Groq fails, returns invalid JSON, or is unavailable, the bot falls back automatically.
- The LLM never writes to the database directly. It only produces a structured intent object.

## Notes

- Health and metrics are local HTTP endpoints only.
- The bot still uses Telegram polling, so your machine must stay on and connected for real-time reminders.
- Backup/export utilities are CLI-based, not Telegram commands.


## Phase 6 notes

- New agent pipeline modules are under `app/ai`, `app/tools`, `app/services`, `app/repositories`, and `app/telemetry`.
- For the easiest local run, delete the old `reminders.db` before starting because Phase 6 adds new tables and columns.
- If `GROQ_API_KEY` is not set, the bot still works using the rule-based fallback interpreter.
- Update/delete disambiguation now uses Telegram inline buttons with `resolve:<ai_run_id>:<reminder_id>` callback payloads.


## Notes
- On Railway, only one bot instance should poll Telegram at a time. This build retries automatically on short-lived polling conflicts during deploy overlap.

## Phase 8 — Personal Memory Intelligence

This package adds memory-based interpretation:
- remembers task/time patterns from successful reminders
- injects personal memory hints into the structured interpreter prompt
- suggests likely follow-up times for known tasks
- boosts confidence for familiar task + time-of-day combinations
- stores task memory in `task_memory_profiles`
