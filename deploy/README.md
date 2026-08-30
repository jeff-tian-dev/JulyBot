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
./deploy/install-service-all.sh             # restart both services
```

`install-service-all.sh` restarts the bot and the website together. Use
`./deploy/install-service.sh` or `./deploy/install-service-web.sh` alone if only one side
changed.

`.env` is **not** in git (it holds secrets) — when a release adds new environment variables,
copy the new keys from `.env.example` into this machine's `.env` by hand **before** restarting.

⚠️ **A missing required variable stops the bot too, not just the website.** Both processes
load the same [config/settings.py](../config/settings.py), which raises at import time if
anything in `REQUIRED_VARS` is unset — so e.g. deploying the Stripe release without adding
`STRIPE_SECRET_KEY` takes the Discord bot offline as well. The error names the missing
variable; check `logs/julybot.stderr.log` and `logs/julybot-web.stderr.log`.

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

The domain is **seventhmonthlegends.fyi**. Publishing it is a one-time networking setup,
handled outside the app — uvicorn stays bound to `127.0.0.1:8001` and a reverse proxy in
front of it terminates TLS. Keeping uvicorn on loopback is deliberate: the app itself is
never directly exposed to the internet, only the proxy is.

**1. Router: forward ports 80 and 443 to the Mac Studio.**
Port 443 serves the site; port 80 is needed for Let's Encrypt's HTTP challenge and to
redirect plain-HTTP visitors. Give this machine a static LAN IP (DHCP reservation) first,
or the forward will silently break when its local address changes.

**2. DNS: point the domain at the home public IP.**
The domain is registered at **Porkbun**, and DNS is managed there. In the Porkbun panel
(Domain Management → DNS), add an `A` record with an empty/`@` host pointing at the
connection's public IP — find it with `curl -s ifconfig.me`. Add a second `A` record for
`www` if that hostname should work too.

**3. Handle the dynamic IP.**
Residential connections usually get a public IP that changes without warning. When it
does, the `A` record goes stale, the site drops offline, and **Stripe webhooks silently
stop being delivered** — purchases would be charged but never recorded. Options:

- Ask the ISP for a static IP (often a small monthly add-on) — simplest, most reliable.
- Run a dynamic-DNS updater on this Mac against **Porkbun's DNS API**. Enable API access
  for the domain in the Porkbun panel (it's off by default — Domain Management → toggle
  *API Access*), create an API key + secret under Account → API Access, then run a
  Porkbun-compatible ddns client on a schedule (e.g. `ddclient`, or a small cron job
  hitting `https://api.porkbun.com/api/json/v3/dns/editByNameType/...`). Store the API
  credentials in this repo's `.env` if a custom script is written — never commit them.
- Move DNS to Cloudflare and use its dynamic-DNS integration instead.

Whichever is chosen, verify externally after any suspected IP change — `curl -sI
https://seventhmonthlegends.fyi` from off-network, or Stripe's webhook delivery log.

**4. Install Caddy and put it in front of uvicorn.**

```bash
brew install caddy
```

`/opt/homebrew/etc/Caddyfile`:

```
seventhmonthlegends.fyi {
    reverse_proxy 127.0.0.1:8001
}
```

```bash
sudo brew services start caddy
```

Caddy provisions and renews the Let's Encrypt certificate automatically and redirects
HTTP to HTTPS. It needs ports 80/443 reachable at first start, so do this after the
router forward is in place.

**5. Point the app at the real URL.**
Set `WEB_BASE_URL=https://seventhmonthlegends.fyi` in `.env` and restart
(`./deploy/install-service-web.sh`). This builds the Stripe Checkout success/cancel
redirect URLs, so a stale value sends buyers somewhere broken after paying.

**Checking the UI before Stripe is live.** The pricing page renders straight from
[web/tiers.py](../web/tiers.py) and makes no Stripe API calls, so the site can be loaded
and reviewed end-to-end with placeholder Stripe keys still in `.env` — only the
Subscribe button needs working credentials. Worth confirming the tier cards, the prices,
`/static/style.css` (proxy misconfigurations tend to break static paths first), and the
`/success` and `/cancel` pages by visiting them directly.

## Environment

Copy from `.env.example`. Key values for this machine:

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | Supabase **Session pooler** string + `?sslmode=require` |
| `BASE_IMAGE_DIR` | `/Users/jefftian/JulyBot/data/bases` |

The database is hosted on Supabase — there is no local Postgres to start or stop. If you can't reach it, check the project isn't paused (free-tier projects pause after inactivity) and that the connection string is the **Session pooler** (IPv4) variant.
