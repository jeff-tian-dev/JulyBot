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

## Deploying a code update

Code reaches this machine via `git pull`. The full update sequence:

```bash
cd /Users/jefftian/JulyBot
git pull
chmod +x deploy/*.sh                        # only if the release added new scripts
.venv/bin/pip install -r requirements.txt   # only if dependencies changed
.venv/bin/python scripts/init_db.py         # only if the schema changed; idempotent, safe to re-run
./deploy/install-service.sh                 # restart the bot
```

`.env` is **not** in git (it holds secrets) — when a release adds new environment variables,
copy the new keys from `.env.example` into this machine's `.env` by hand **before** restarting.
[config/settings.py](../config/settings.py) raises at import time if a variable in
`REQUIRED_VARS` is unset, naming the one that's missing; check `logs/julybot.stderr.log`.

## Automatic deploys on push

`./deploy/install-autoupdate.sh` installs a launchd agent (`com.julybot.autoupdate`) that
checks GitHub every 60 seconds. When `main` has a new commit it runs the tests against that
commit, pulls it, installs dependencies if `requirements.txt` changed, and restarts the bot.
If the bot doesn't log in afterwards, it rolls back to the previous commit.

```bash
./deploy/install-autoupdate.sh     # turn on
./deploy/uninstall-autoupdate.sh   # turn off (the bot keeps running)
tail -f logs/auto-update.log       # what it did, and why it skipped anything
```

It **won't deploy** while this checkout has uncommitted edits to tracked files, or local commits
that aren't on GitHub. It logs a `SKIP` line once, then picks the push up as soon as the edits
are committed or stashed. New `.env` keys and changes to the bot's plist template are still
manual steps (it logs a `NOTE` when it sees either).

## Multi-server update (2026-11)

The bot now works in any server it's added to, with each server's data kept apart. Nothing
manual is needed to deploy it — `create_tables` migrates on startup — but there are a few
steps in Discord afterwards.

**What startup does once:**

- Copies the old single-server `.env` values (`MOD_LOG_CHANNEL_ID`, `CLAN_WATCH_CHANNEL_ID`,
  `X_PING_ROLE_ID`/`TWITTER_PING_ROLE_ID`, `YOUTUBE_PING_ROLE_ID`, `COC_FAMILY_CLAN_TAGS`,
  `COC_CLAN_TAG`) into the home server's (`DISCORD_GUILD_ID`) `/setup` settings. After that
  they're ignored; change them with `/setup`.
- Re-keys `clan_membership` and `seen_tweets` per server, assigning existing rows to the home
  server.
- Registers every command globally and removes the home server's old server-only copies, so
  nothing shows twice. New global commands can take a little while to appear.

**Then, in the home server:**

1. `/setup status` — check the copied channels, roles and clans look right.
2. `/product list` — any products from the old `/product sell` era are now `/subscribe`
   buttons. Remove the ones that shouldn't be.
3. `/product add label:July Sub link:https://seventhmonth.com/sub/` and
   `/product add label:CWL packs link:https://seventhmonth.com/cwl-pack/`.

**In the Underground server**, right away (the first server to add a Stripe link owns it):

1. Invite the bot, then `/setup status`.
2. `/product add label:UG Sub link:https://seventhmonth.com/ug-sub/`.

Each `/product add` reply says how many Stripe checkouts it found on the page and whether any
belong to another server. Confirm Payment only lists payments made through the server's own
links, so July buyers never appear in Underground's list and vice versa.

`STRIPE_SECRET_KEY` stays optional and is *not* in `REQUIRED_VARS`: without it the bot runs,
`/product add` still adds the button, and Confirm Payment says Stripe isn't configured.
`SUBSCRIBE_URL` and `STRIPE_PAYMENT_LINK_*` are no longer read; leftover values in `.env` are
harmless.

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
