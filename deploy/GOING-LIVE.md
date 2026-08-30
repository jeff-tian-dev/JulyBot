# Going live: publishing the subscription site

**Handoff document.** Everything in this file happens on the Mac Studio and in web
dashboards — no code changes are required. The application side is finished, tested, and
merged; what remains is networking and Stripe account setup.

Work through it top to bottom. Each phase has a check that must pass before the next one
is worth attempting.

---

## What already works

- The subscription site (`web/`) runs on the Mac Studio as its own launchd service
  (`com.julybot.web`), independent of the Discord bot (`com.julybot`).
- Checkout, the Stripe webhook, and the Supabase writes have all been verified end to end
  in Stripe **test mode**, driven through the Stripe CLI.
- Two tiers are configured: **L2/L3 at $20** and **L1 at $30**, each a **one-time**
  purchase the buyer repeats each month — not an auto-renewing subscription.

## What is not done

- The site is only reachable from the Mac itself (`http://127.0.0.1:8001`).
- The domain **seventhmonthlegends.fyi** (registered at Porkbun) points nowhere yet.
- Stripe is still in test mode. No real payment can be taken.

---

## Phase 1 — Make the site reachable at the domain

The site currently listens only on the Mac's own loopback address. Three things have to
line up for the outside world to reach it: the **router** must forward incoming web
traffic to the Mac, **DNS** must resolve the domain to the home's public IP address, and
something must serve **HTTPS**, which browsers and Stripe both require.

uvicorn deliberately stays bound to `127.0.0.1:8001` throughout. A reverse proxy (Caddy)
sits in front and handles TLS, so the application is never directly exposed to the
internet — only the proxy is. Do not change `WEB_HOST` to `0.0.0.0`.

### 1.1 Collect the two IP addresses

On the Mac:

```bash
curl -s ifconfig.me        # public IP — what the internet sees. DNS points here.
ipconfig getifaddr en0     # local IP on the home network, e.g. 192.168.1.42.
                           # Try en1 if that prints nothing (en0 is usually Ethernet, en1 wifi).
```

These are different addresses used for different steps. Keep both.

### 1.2 Reserve the Mac's local IP

In the router's admin page, find DHCP reservations (sometimes "static lease" or "address
reservation") and pin the Mac to the local IP from above.

Skipping this is the most common cause of the site mysteriously dying weeks later: the
router hands the Mac a different local address after a reboot, and the port forward set up
in the next step keeps pointing at the old one.

### 1.3 Forward ports 80 and 443 to the Mac

Also in the router admin page, under "Port Forwarding" or "Virtual Server". Two rules,
both pointing at the Mac's reserved local IP:

| External port | Internal port | Protocol |
| ------------- | ------------- | -------- |
| 80            | 80            | TCP      |
| 443           | 443           | TCP      |

Port 443 carries the actual site. Port 80 is needed for Let's Encrypt's certificate
challenge and to redirect anyone typing plain `http://`.

> **If the ISP blocks these ports** — some residential providers block inbound 80/443
> outright — no amount of configuration will make this work. The workaround is a
> **Cloudflare Tunnel**, which makes an *outbound* connection from the Mac and needs no
> port forwarding or DNS pointing at the home IP at all. If Phase 1 fails its check below
> and the router config looks correct, this is the likely reason.

### 1.4 Point the domain at the public IP

DNS for **seventhmonthlegends.fyi** is managed at **Porkbun** (Domain Management → DNS).
Add:

| Type | Host        | Answer / Value            |
| ---- | ----------- | ------------------------- |
| A    | *(blank)*   | the public IP from 1.1     |
| A    | `www`       | the public IP from 1.1     |

The blank host means the bare domain. The `www` record is optional but avoids confusing
anyone who types it.

Delete any placeholder records Porkbun created at registration (parking pages, etc.) that
also target the bare domain, or they will conflict.

DNS usually propagates within minutes. Check from any machine:

```bash
dig seventhmonthlegends.fyi +short     # should print the public IP
```

### 1.5 Install Caddy for HTTPS

Caddy is a web server that obtains and renews a free Let's Encrypt certificate
automatically, then forwards requests to the application.

```bash
brew install caddy
```

Write `/opt/homebrew/etc/Caddyfile` — the entire file is:

```
seventhmonthlegends.fyi {
    reverse_proxy 127.0.0.1:8001
}
```

Then:

```bash
sudo brew services start caddy
```

`sudo` is required because ports below 1024 are privileged. Caddy must be able to reach
ports 80/443 from the internet when it first starts, so do this **after** 1.3 and 1.4 —
otherwise certificate issuance fails and it will keep retrying.

If it does not come up, check its log:

```bash
brew services info caddy
tail -50 /opt/homebrew/var/log/caddy.log
```

### 1.6 Set the public URL and restart

In the Mac's `.env`:

```
WEB_BASE_URL=https://seventhmonthlegends.fyi
```

```bash
./deploy/install-service-web.sh
```

This value builds the URLs Stripe returns buyers to after paying. Leaving it as
`localhost` sends every real customer to a dead page after checkout.

### ✅ Phase 1 check

Open **https://seventhmonthlegends.fyi** in a browser — **from a phone on cellular data,
not from the home wifi.** Many routers cannot loop a request back to a machine on their
own network ("NAT loopback"), so testing from inside the house can fail while the site is
perfectly fine for everyone else.

Expected:

- Both tier cards render, showing **L2/L3 $20/month** and **L1 $30/month**
- Styling is applied — if the page is unstyled text, `/static/style.css` is not being
  served, which usually means a proxy misconfiguration
- The optional "Discord username" field appears on each card
- `https://seventhmonthlegends.fyi/success` and `/cancel` both load
- The padlock icon shows, with no certificate warning

The **Subscribe** buttons are expected to fail at this stage — they still point at test
mode. That is Phase 3.

---

## Phase 2 — Handle the changing IP address

Residential internet connections are normally handed a public IP that changes without
notice. When it changes, the DNS record from 1.4 is stale, and:

- the site goes offline, and
- **Stripe webhooks stop being delivered** — customers are charged, but nothing is
  recorded in the database and no access is granted.

That second failure is silent, which makes it worth solving properly rather than fixing
reactively. Pick one:

**Option A — request a static IP from the ISP.** Usually a small monthly add-on. Nothing
further to maintain, and the most reliable choice.

**Option B — run a dynamic DNS updater against Porkbun's API.** In the Porkbun panel,
enable *API Access* for the domain (it is off by default) and create an API key and secret
under Account → API Access. Then run a Porkbun-compatible updater (`ddclient`, or a small
scheduled script calling `https://api.porkbun.com/api/json/v3/dns/editByNameType/...`) so
the A record follows the IP. Keep the credentials in `.env`; never commit them.

**Option C — move DNS to Cloudflare** and use its dynamic DNS integration, or a Cloudflare
Tunnel, which sidesteps both the IP problem and port forwarding entirely.

### ✅ Phase 2 check

After a router reboot (which often forces a new IP), the site is still reachable from
cellular data without anyone touching DNS by hand.

---

## Phase 3 — Switch Stripe to live mode

Do not start this until Phase 1 passes. Stripe's account review asks for a working
website, and live webhooks need the public HTTPS URL to exist.

### 3.1 Activate the Stripe account

In the Stripe Dashboard, complete the **Business details** / activation form: business
information, identity verification, and the bank account that payouts are deposited into.
Use `https://seventhmonthlegends.fyi` as the business website.

Approval is not always instant, so it is worth starting early — this is the step with
unpredictable turnaround.

### 3.2 Recreate the products in live mode

Test mode and live mode have **completely separate** products, prices, and API keys.
Nothing created during testing carries over.

With the dashboard toggled to **live mode**, create two products:

| Product | Price | Type                              |
| ------- | ----- | --------------------------------- |
| L2/L3   | $20   | **One-time** — *not* recurring    |
| L1      | $30   | **One-time** — *not* recurring    |

**One-time is essential.** The application calls Stripe in `mode="payment"`, and Stripe
rejects a recurring price in that mode — checkout would fail outright. This mirrors how
access actually works: members repurchase each month rather than being billed
automatically.

Copy each **price ID** (starts with `price_`, found in the pricing section of the product
page). Do not copy the product ID, which starts with `prod_` and will not work.

### 3.3 Create the live webhook endpoint

Dashboard (live mode) → Developers → Webhooks → Add endpoint:

- **URL:** `https://seventhmonthlegends.fyi/webhook/stripe`
- **Event:** `checkout.session.completed`

That single event is all the application handles. Everything else Stripe sends is
acknowledged and ignored, so subscribing to more only adds noise.

Copy the endpoint's **signing secret** (starts with `whsec_`).

### 3.4 Update `.env` and restart

On the Mac, replace all four values with their live equivalents:

```
STRIPE_SECRET_KEY=sk_live_...          # live secret key, from Developers → API keys
STRIPE_WEBHOOK_SECRET=whsec_...        # from 3.3
STRIPE_PRICE_ID_L2_L3=price_...        # live one-time price from 3.2
STRIPE_PRICE_ID_L1=price_...           # live one-time price from 3.2
```

```bash
./deploy/install-service-all.sh
```

> `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET` are **required** settings, and the
> Discord bot loads the same configuration file. If either is missing or empty, **the bot
> will not start either** — not just the website. The error message names the missing
> variable; see `logs/julybot.stderr.log` and `logs/julybot-web.stderr.log`.

### ✅ Phase 3 check

Buy one subscription with a real card. This charges real money — refund it from the
Stripe Dashboard afterwards.

Confirm:

- Checkout completes and the browser lands on the success page
- The payment appears in the Stripe Dashboard (live mode)
- Stripe's webhook log shows `checkout.session.completed` delivered with a **200**
- A row appears in the Supabase `subscriptions` table with the right tier and email, and
  `status` = `paid`

If the first three pass but no row appears, the webhook is not reaching the Mac — check
the URL in 3.3 and that the site is still publicly reachable.

---

## Still to build (not part of this handoff)

**Discord access is not granted automatically.** The site records who paid; nothing
currently reads that record and assigns a Discord role. Until that is built, granting
access after a purchase remains a manual step, exactly as it is today.

The database has a `discord_id` column reserved for this, and checkout collects an
optional (unverified) Discord username so purchases can be matched to people in the
meantime. There is also no expiry tracking yet — each purchase is recorded with a
timestamp, but nothing computes or enforces when access should lapse.

See the `2026-08-29` and `2026-08-30` entries in [CLAUDE.md](../CLAUDE.md) for the
reasoning behind these decisions.
