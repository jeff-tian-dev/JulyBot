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

## Run the web service (Stripe subscription site)

The subscription website (`web/`) is a **separate process** from the bot — its own launchd
service, its own log files, started/stopped independently. Installing, restarting, or
removing the bot's service has no effect on the website, and vice versa.

**Foreground** (good for debugging):

```bash
./deploy/start-web.sh
```

**Background** (auto-start on login, restart on crash):

```bash
./deploy/install-service-web.sh
```

Logs go to `logs/julybot-web.stdout.log` and `logs/julybot-web.stderr.log`.

Stop the background service:

```bash
./deploy/stop-web.sh
```

Remove the launchd agent entirely:

```bash
./deploy/uninstall-service-web.sh
```

### Restarting both services at once

After a code deploy that touches both the bot and the website, `./deploy/install-service-all.sh`
runs `install-service.sh` and `install-service-web.sh` in one command — a convenience wrapper
only. The two remain independent launchd services underneath (`com.julybot` and
`com.julybot.web`); this just saves typing both commands. Matching `./deploy/stop-all.sh` and
`./deploy/uninstall-service-all.sh` wrappers also exist. Use the individual `*-web.sh` / plain
`*.sh` scripts when you only need to restart one side (e.g. a website-only change).

### Stripe webhook setup

Once the site is publicly reachable (see below), create a webhook endpoint in the
[Stripe Dashboard](https://dashboard.stripe.com/webhooks) pointing at:

```
https://<your-domain>/webhook/stripe
```

Select at least `checkout.session.completed` — that's the only event type this app currently
handles (Checkout runs in one-time-payment mode, not a recurring Stripe subscription, so
`customer.subscription.*` events never apply here; see the 2026-08-30 CLAUDE.md entry). Every
other event Stripe sends is safely acknowledged and ignored. Copy the resulting signing secret
into `STRIPE_WEBHOOK_SECRET`.

For local testing without a public domain, use the [Stripe CLI](https://stripe.com/docs/stripe-cli):

```bash
stripe listen --forward-to localhost:8001/webhook/stripe
```

### Making the site publicly reachable

This is required for Stripe to deliver real webhooks and isn't handled by the app itself:

1. Buy a domain and point its DNS at this Mac Studio's public IP (or use dynamic DNS if
   the ISP-assigned IP isn't static).
2. Forward port 443 (and 80, if used for a Let's Encrypt HTTP challenge) through the router
   to this machine.
3. Terminate TLS in front of uvicorn — e.g. [Caddy](https://caddyserver.com/) reverse-proxying
   to `127.0.0.1:8001`, which auto-provisions and renews a Let's Encrypt certificate.
4. Set `WEB_BASE_URL` in `.env` to the real `https://<your-domain>` — it's used to build the
   Stripe Checkout success/cancel redirect URLs, so it must match what buyers actually reach.

## Environment

Copy from `.env.example`. Key values for this machine:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | Supabase **Session pooler** string + `?sslmode=require` |
| `BASE_IMAGE_DIR` | `/Users/jefftian/JulyBot/data/bases` |

The database is hosted on Supabase — there is no local Postgres to start or stop. If you can't reach it, check the project isn't paused (free-tier projects pause after inactivity) and that the connection string is the **Session pooler** (IPv4) variant.
