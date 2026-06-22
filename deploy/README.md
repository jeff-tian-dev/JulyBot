# Mac Studio deployment

JulyBot runs locally on this Mac Studio at `/Users/jefftian/JulyBot`.

## Prerequisites

- **Python 3.11+** — `python3` on PATH (Homebrew or system)
- **Supabase project** — the hosted Postgres database (pgvector enabled). Grab the **Session pooler** connection string from Project Settings → Database.
- **Discord bot token** and **CoC API token** — CoC token must be IP-whitelisted to this machine's public IP

## First-time setup

```bash
cd /Users/jefftian/JulyBot
chmod +x deploy/*.sh
./deploy/setup.sh
```

Edit `.env` with your secrets, then initialize the database:

```bash
.venv/bin/python scripts/init_db.py
```

## Run the bot

**Foreground** (good for debugging):

```bash
./deploy/start.sh
```

**Background** (auto-start on login, restart on crash):

```bash
./deploy/install-service.sh
```

Logs go to `logs/julybot.stdout.log` and `logs/julybot.stderr.log`.

Stop the background service:

```bash
./deploy/stop.sh
```

Remove the launchd agent entirely:

```bash
./deploy/uninstall-service.sh
```

## Environment

Copy from `.env.example`. Key values for this machine:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | Supabase **Session pooler** string + `?sslmode=require` |
| `BASE_IMAGE_DIR` | `/Users/jefftian/JulyBot/data/bases` |

The database is hosted on Supabase — there is no local Postgres to start or stop. If you can't reach it, check the project isn't paused (free-tier projects pause after inactivity) and that the connection string is the **Session pooler** (IPv4) variant.
