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

## Pending update — `/subscribe` purchase flow (commit `295af9a`, 2026-08-30)

This release is **not yet deployed to this machine.** It needs all four optional steps
above, so run the sequence in full:

```bash
cd /Users/jefftian/JulyBot
git pull
.venv/bin/pip install -r requirements.txt   # `stripe` is a dependency again
# --- edit .env by hand first, see the table below ---
.venv/bin/python scripts/init_db.py         # creates `subscribers`, drops `subscriptions`
./deploy/install-service.sh                 # restart
```

**New `.env` keys** (add by hand — `.env` is not in git; all four are also in `.env.example`):

| Variable | Value | Required? |
|----------|-------|-----------|
| `STRIPE_PAYMENT_LINK_L2_L3` | Payment Link URL for the $20 L2/L3 tier | Tier is hidden if unset |
| `STRIPE_PAYMENT_LINK_L1` | Payment Link URL for the $30 L1 tier | Tier is hidden if unset |
| `STRIPE_SECRET_KEY` | `sk_live_…` (or `sk_test_…` to rehearse) | Optional — see below |
| `SUBSCRIBER_REFRESH_INTERVAL_MINUTES` | `60` | Optional, defaults to 60 |

Both Payment Links must be created in the Stripe Dashboard as **recurring monthly**
subscriptions, and both must be **live-mode** links if `STRIPE_SECRET_KEY` is a live key —
a test link paired with a live key produces subscriptions the confirm step cannot find.

Two things to expect while running this:

- **`init_db.py` drops the `subscriptions` table.** That is intended. It was built for the
  deleted FastAPI webhook, never held a real row, and is replaced by `subscribers`. The
  destructive `DROP TABLE` in the output is not an error.
- **`init_db.py` also clears a stale `payment_method = 'PayPal'`** off agreements written by
  `/subscribe`. The column carried a `DEFAULT 'PayPal'` from when PayPal was the only payment
  option, so Stripe purchases were printing "Payment Method: PayPal" on their receipts. Rows
  from the retired moderator flow keep their value — that one is real. Nothing to do; it just
  explains the `UPDATE` in the log.
- **`STRIPE_SECRET_KEY` is deliberately optional and *not* in `REQUIRED_VARS`.** Leaving it
  blank still starts the bot — Confirm Payment just reports that Stripe isn't configured, and
  the subscriber-status refresh job doesn't register. This is on purpose: a Stripe
  misconfiguration must never take the whole Discord bot down.

**What changed for users:** `/subscribe` is now **admin-only** and takes a member argument
(`/subscribe @buyer`), posting a public status message in the ticket instead of an ephemeral
one. `/agreement send` is gone — the agreement is step 1 of `/subscribe`; `/agreement lookup`
and `/agreement receipt` still work and still render the older moderator-flow rows.

**Verifying it took**, after the restart:

```bash
tail -n 40 logs/julybot.stderr.log     # should show a clean login, no settings error
```

Then in Discord: `/subscribe @someone` → the buyer clicks I Agree → payment link buttons
appear → an admin clicks Confirm Payment and picks the buyer's subscription from the Stripe
dropdown. If the dropdown is empty, the key and the Payment Links are in different modes
(one live, one test).

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
