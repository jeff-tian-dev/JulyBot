# JulyBot — Clash of Clans Discord Bot

A Discord bot for Clash of Clans clans, built around nine independent modules:

- **Account linker** — verifies a Discord user owns a given CoC account using the in-game API token flow, and stores the link. Supports multiple accounts (alts) per Discord user.
- **Legend tracker** — polls the official Clash of Clans API on a schedule, stores daily snapshots of every linked player's legend league stats, and computes day-over-day diffs.
- **Base finder** — ingests YouTube VODs from watched channels, extracts attack-loading-screen base layouts via OpenCV, and lets users find similar bases by uploading a screenshot.
- **Ping automator** — APScheduler jobs that drive the polls and ingestion, plus role-based notification hooks.
- **X monitor** — polls watched X accounts via `tweety-ns` (cookie auth) and posts new posts as Discord embeds.
- **YouTube feed tracker** — polls YouTube RSS feeds via `feedparser` and posts when a watched channel uploads a new video.
- **Moderation** — admin-only `/kick`, `/ban`, `/unban` slash commands with pre-flight validation, public taunt messages, and an audit log embed to a mod-log channel.
- **Roster** — admin-managed named groups of players (by Discord user or raw CoC tag). Optionally *watch* a roster to get alerts when a member leaves or rejoins the clan family (`COC_FAMILY_CLAN_TAGS`) and to track how long each member has been out.
- **Ranked tracker** — looks up a player's current Ranked Battles weekly tournament group (`/group`) and renders a refreshable dashboard of defenses received this week, with no stored history — everything is fetched live from the CoC API on each refresh.
- **Subscriptions** — a small FastAPI website (`web/`) offering paid monthly access tiers via Stripe Checkout, purchased one time and repurchased each month by the buyer (not auto-renewing). Runs as its own process alongside the bot; handles checkout + webhook plumbing only (no Discord role granting yet).

The Discord layer (`disnake` Cogs) is a thin shim. Each module is a plain Python package, callable and testable without a running bot.

---

## Tech stack

| Concern         | Choice                                          |
| --------------- | ----------------------------------------------- |
| Language        | Python 3.11+                                    |
| Discord         | `disnake` (not `discord.py`)                    |
| Database        | Supabase (hosted PostgreSQL + `pgvector`)       |
| DB driver       | Raw `asyncpg` — no ORM                          |
| HTTP            | `aiohttp` (single shared session per module)    |
| YouTube         | `yt-dlp` (stream URLs, never full downloads)    |
| Image / CV      | `opencv-python`, `Pillow`, `numpy`, `imagehash` |
| Scheduling      | `APScheduler` (`AsyncIOScheduler`)              |
| X               | `tweety-ns` (cookie auth, no API key)           |
| YouTube feeds   | `feedparser` (RSS, no API key)                  |
| Config          | `python-dotenv` -> `config/settings.py`         |
| Tests           | `pytest`, `pytest-asyncio`                      |

---

## Project structure

```
JulyBot/
|-- config/
|   `-- settings.py           # central env-var loader; the only place os.getenv lives
|-- database/
|   |-- connection.py         # asyncpg pool singleton
|   `-- models.py             # CREATE TABLE statements + create_tables / drop_tables
|-- modules/
|   |-- account_linker/
|   |   `-- linker.py         # link / unlink / lookup; calls CoC verifyToken
|   |-- legend_tracker/
|   |   |-- poller.py         # CoC API client (shared aiohttp session)
|   |   `-- snapshots.py      # daily snapshots: save / fetch / diff
|   |-- base_finder/
|   |   |-- pipeline.py       # YouTube -> frames -> normalize -> store
|   |   |-- detector.py       # loading-screen detection (CV stub)
|   |   |-- normalizer.py     # crop UI, resize, pHash
|   |   `-- matcher.py        # find_matching_bases / is_duplicate
|   |-- x_monitor/
|   |   |-- client.py         # tweety-ns session wrapper
|   |   |-- storage.py        # watch list + seen tweet dedup
|   |   |-- poller.py         # poll accounts, post embeds
|   |   |-- embeds.py         # Discord embed builder
|   |   `-- tweety_patch.py   # runtime patch for tweety's X transaction-id parsing
|   |-- youtube_feed/
|   |   |-- fetcher.py        # RSS fetch via feedparser
|   |   |-- storage.py        # watch list + last_seen_video_id
|   |   |-- poller.py         # poll channels, post embeds
|   |   `-- embeds.py         # Discord embed builder
|   |-- ping_automator/
|   |   `-- scheduler.py      # APScheduler jobs + ping hook
|   |-- moderation/
|   |   |-- actions.py        # kick / ban / unban via disnake
|   |   |-- validation.py     # pre-flight target checks + ModerationError
|   |   |-- messages.py       # public taunt quips
|   |   `-- logging.py        # mod-log channel embed
|   |-- roster/
|   |   |-- storage.py        # roster CRUD, membership, clan-membership state
|   |   `-- watcher.py        # clan-watch poller: leave/rejoin alerts + absence
|   |-- ranked_tracker/
|   |   |-- poller.py         # CoC API client for /players and /leaguegroup
|   |   |-- group.py          # group resolution, defense histogram, embed rendering
|   |   |-- extrapolate.py    # per-member 30-attack pace extrapolation (/groupextrapolate)
|   |   `-- tracking.py       # /trackingon|off|list -- DM alerts on status change
|   `-- subscriptions/
|       `-- storage.py        # subscriptions table CRUD (pool-first, no Stripe/FastAPI imports)
|-- web/                      # subscription website -- separate process from the bot
|   |-- app.py                # FastAPI app factory + lifespan (DB pool open/close)
|   |-- main.py                # uvicorn entrypoint -- python web/main.py
|   |-- tiers.py                # tier display config (name/price/description) + Price IDs from settings
|   |-- stripe_client.py       # only module that imports `stripe`; wraps calls in asyncio.to_thread
|   |-- routes/                # pages.py (pricing/success/cancel), checkout.py, webhook.py
|   `-- templates/              # Jinja2 templates, no JS framework
|-- discord_bot/
|   |-- bot.py                # create_bot() — InteractionBot factory
|   `-- commands/             # one Cog per module (account, x, youtube, moderation, roster, post, base_post, agreement, ranked, + stub legend/base_finder/ping)
|-- tests/
|   |-- conftest.py           # stubs env vars before project imports
|   |-- test_account_linker.py
|   |-- test_legend_tracker.py
|   |-- test_base_finder.py
|   |-- test_x_monitor.py
|   |-- test_youtube_feed.py
|   `-- test_moderation.py
|-- scripts/
|   |-- init_db.py            # standalone DB initializer (create tables + seed channels)
|   `-- ...                   # base_finder dev/validation tools (scan_video, benchmark_matcher, etc.)
|-- deploy/
|   |-- setup.sh              # one-time Mac Studio setup (venv + .env)
|   |-- start.sh              # run bot in foreground
|   |-- stop.sh               # stop the launchd background service
|   |-- install-service.sh    # launchd agent — auto-start on login
|   `-- uninstall-service.sh  # remove the launchd agent
|-- data/bases/               # generated base images (gitignored except .gitkeep)
|-- main.py                   # entry point — pool + scheduler + bot
|-- requirements.txt
|-- .env.example
|-- CLAUDE.md                 # project conventions for Claude Code
`-- README.md                 # this file
```

---

## Setup (Mac Studio)

This bot is intended to run locally on the Mac Studio at `/Users/jefftian/JulyBot`. See [deploy/README.md](deploy/README.md) for full deployment details.

### 1. Prerequisites

- **Python 3.11+**
- A **Supabase** project (free tier is fine) — the hosted Postgres database
- A Discord bot application (token + guild ID)
- A Clash of Clans developer API token from https://developer.clashofclans.com — whitelist this machine's public IP

### 2. First-time setup

```bash
cd /Users/jefftian/JulyBot
chmod +x deploy/*.sh
./deploy/setup.sh
```

This creates a `.venv`, installs dependencies, and copies `.env.example` → `.env` if needed. The database lives on Supabase, so there's nothing to run locally.

### 3. Configure environment

Edit `.env` with your secrets (or copy the template manually):

```bash
cp .env.example .env   # only if setup.sh didn't already create it
```

| Variable                        | Required | Default                                | Notes                                              |
| ------------------------------- | -------- | -------------------------------------- | -------------------------------------------------- |
| `DISCORD_TOKEN`                 | yes      | —                                      | Bot token from the Discord developer portal        |
| `DISCORD_GUILD_ID`              | no       | `0`                                    | Test guild for instant slash-command sync          |
| `COC_API_TOKEN`                 | yes      | —                                      | From developer.clashofclans.com (IP-locked)        |
| `COC_API_BASE_URL`              | no       | `https://api.clashofclans.com/v1`      | Set to `https://cocproxy.royaleapi.dev/v1` to route via RoyaleAPI's proxy (whitelist their static IP `45.79.218.79` instead of your changing IP) |
| `DATABASE_URL`                  | yes      | —                                      | Supabase **Session pooler** string + `?sslmode=require` (see `.env.example`) |
| `BASE_IMAGE_DIR`                | no       | `./data/bases`                         | Where extracted base PNGs are written (`.env.example` sets the absolute Mac Studio path) |
| `BASE_CACHE_SIZE`               | no       | `750`                                  | Sliding-window cap; older rows are evicted         |
| `YOUTUBE_CHANNEL_IDS`           | no       | empty                                  | Comma-separated list, e.g. `UCabc,UCxyz`           |
| `LEGEND_POLL_INTERVAL_MINUTES`  | no       | `60`                                   | Legend snapshot cadence                            |
| `CACHE_REFRESH_INTERVAL_HOURS`  | no       | `24`                                   | Base-finder ingestion cadence                      |
| `X_COOKIES`                     | no       | empty                                  | Semicolon-delimited browser cookies; empty disables X monitor (`TWITTER_COOKIES` still accepted) |
| `X_SESSION_NAME`                | no       | `julybot_x`                            | tweety session file basename under `data/x/` (`TWITTER_SESSION_NAME` still accepted) |
| `X_POLL_INTERVAL_MINUTES`       | no       | `10`                                   | X account poll cadence (`TWITTER_POLL_INTERVAL_MINUTES` still accepted) |
| `X_PING_ROLE_ID`                | no       | `0`                                    | Role mention on new posts (0 = no ping; `TWITTER_PING_ROLE_ID` still accepted) |
| `X_PING_COOLDOWN_HOURS`         | no       | `3`                                    | After a ping, new X posts within this window post silently |
| `X_MAX_CONSECUTIVE_FAILURES`    | no       | `3`                                    | Consecutive connection failures before the poller alerts once and pauses (resumes on restart) |
| `X_ALERT_CHANNEL_ID`            | no       | `0`                                    | Channel for the "X monitoring stopped" alert (0 = fall back to `MOD_LOG_CHANNEL_ID`) |
| `YOUTUBE_FEED_POLL_INTERVAL_MINUTES` | no  | `10`                                   | YouTube RSS poll cadence                           |
| `YOUTUBE_PING_ROLE_ID`          | no       | `1508359179440750602`                  | Role mention on new YouTube videos (0 = no ping)   |
| `YOUTUBE_PING_COOLDOWN_HOURS`   | no       | `3`                                    | After a ping, new YouTube videos within this window post silently |
| `MOD_LOG_CHANNEL_ID`              | no       | `1514111681222148219`                  | Channel for kick/ban/unban mod logs                |
| `COC_FAMILY_CLAN_TAGS`           | no       | empty                                  | Comma-separated family clan tags **without `#`** (it's a comment char in `.env`). A roster member is "in" if in ANY of these clans. Empty disables the clan-watch job |
| `COC_CLAN_TAG`                   | no       | empty                                  | Optional single main-clan tag, merged into the family above (back-compat) |
| `CLAN_WATCH_CHANNEL_ID`          | no       | `1528897151625592993`                  | Channel that clan-watch leave/rejoin alerts post to |
| `CLAN_WATCH_POLL_INTERVAL_MINUTES` | no     | `10`                                   | How often watched rosters are checked against the clan family |
| `RANKED_TRACKING_POLL_INTERVAL_MINUTES` | no | `20`                              | How often tracked players' likely-to-be-hit status is checked for `/trackingon` DM alerts |
| `STRIPE_SECRET_KEY`              | yes      | —                                       | Secret key from the Stripe Dashboard (test or live mode)   |
| `STRIPE_WEBHOOK_SECRET`          | yes      | —                                       | Signing secret for the `/webhook/stripe` endpoint (Stripe Dashboard or `stripe listen`) |
| `STRIPE_PUBLISHABLE_KEY`         | no       | empty                                   | Unused by redirect-based Checkout; kept for future client-side Stripe.js use |
| `STRIPE_PRICE_ID_L2_L3`          | no       | empty                                   | Stripe Price ID for the L2/L3 tier ($20/month)              |
| `STRIPE_PRICE_ID_L1`             | no       | empty                                   | Stripe Price ID for the L1 tier ($30/month, highest)        |
| `WEB_HOST`                       | no       | `127.0.0.1`                             | Bind address for the subscription website's uvicorn server  |
| `WEB_PORT`                       | no       | `8001`                                  | Bind port for the subscription website                      |
| `WEB_BASE_URL`                   | no       | `http://localhost:8001`                 | Public base URL used to build Stripe Checkout success/cancel redirect URLs — set to the real `https://<domain>` once deployed |

Missing any required variable raises a clear `ValueError` at startup.

### 4. Initialize the database

```bash
.venv/bin/python scripts/init_db.py
```

This enables the `vector` extension, creates all tables on your Supabase instance, and seeds `watched_channels` with any IDs from `YOUTUBE_CHANNEL_IDS`.

### 5. Start / stop the bot

The bot runs from the repo root on the Mac Studio. Foreground and background are mutually exclusive — stop the service before running in the foreground, or two bots will double-post.

| Action | Command |
| ------ | ------- |
| **Start (foreground)** — logs to terminal, `Ctrl-C` to stop | `./deploy/start.sh` |
| **Start (background)** — launchd, auto-start on login, restart on crash | `./deploy/install-service.sh` |
| **Stop the background service** | `./deploy/stop.sh` |
| **Restart after a code change** | `./deploy/install-service.sh` (re-run; safe) |
| **Remove the launchd agent** | `./deploy/uninstall-service.sh` |

Background logs: `logs/julybot.stdout.log` and `logs/julybot.stderr.log`.

Startup sequence: load settings -> open asyncpg pool -> ensure tables -> seed unseeded YouTube channels -> start APScheduler -> connect Discord. Ctrl-C (foreground) or `./deploy/stop.sh` (background) triggers an ordered shutdown.

The subscription website is a **separate process** with its own start/stop scripts (`./deploy/start-web.sh`, `./deploy/install-service-web.sh`, `./deploy/stop-web.sh`) and its own launchd service — see [deploy/README.md](deploy/README.md#run-the-web-service-stripe-subscription-site). To restart the bot and the website together after a deploy, `./deploy/install-service-all.sh` runs both install scripts in one command (still two independent launchd services underneath); matching `stop-all.sh`/`uninstall-service-all.sh` wrappers also exist.

The site is not yet publicly reachable and Stripe is still in test mode — **[deploy/GOING-LIVE.md](deploy/GOING-LIVE.md)** is the step-by-step handoff for domain setup (seventhmonthlegends.fyi, via Porkbun DNS + Caddy) and switching Stripe to live.

### 6. Deploying a code update

Code reaches the Mac Studio via `git pull`:

```bash
cd /Users/jefftian/JulyBot
git pull
chmod +x deploy/*.sh                        # only if the release added new scripts
.venv/bin/pip install -r requirements.txt   # only if dependencies changed
.venv/bin/python scripts/init_db.py         # only if the schema changed; idempotent
./deploy/install-service-all.sh             # restart both services
```

`.env` is not in git — when a release adds new environment variables, copy the new keys from `.env.example` into the Mac Studio's `.env` before restarting. Because both processes share [config/settings.py](config/settings.py), a missing **required** variable stops the **bot** as well as the website. See [deploy/README.md](deploy/README.md#deploying-a-code-update) for details.

---

## Database schema

| Table              | Purpose                                                                   |
| ------------------ | ------------------------------------------------------------------------- |
| `users`            | Discord ID <-> CoC tag links, with `verified` flag                        |
| `legend_snapshots` | One row per `(coc_tag, snapshot_date)`; trophies + attack/defense counters |
| `base_cache`       | Extracted base images: path, pHash, source, town hall, `vector(512)` embedding |
| `watched_channels` | YouTube channel IDs the base finder pulls from                            |
| `guild_settings`   | Per-guild ping, X, and YouTube channel settings                     |
| `twitter_watched_accounts` | X accounts watched per guild, with `last_seen_tweet_id` (legacy table name) |
| `seen_tweets`      | Posted tweet IDs for deduplication across restarts                        |
| `youtube_watched_channels` | YouTube channels watched per guild, with `last_seen_video_id`     |
| `rosters`          | Named player groups per guild (`watched` flag opts into clan-watch)       |
| `roster_members`   | Roster membership — one row per Discord user **or** raw CoC tag            |
| `clan_membership`  | Per-tag clan in/out state + accumulated absence, kept by the clan-watch poller |
| `coc_player_cache` | Short-TTL cache of live CoC player name + current clan, shared across rosters |
| `ranked_tracking`  | `/trackingon` subscriptions: Discord user + CoC tag pairs, with the last-seen likely-to-be-hit status |
| `subscriptions`    | Stripe one-time purchases bought through the `web/` checkout site (repurchased monthly, not auto-renewing): one row per purchase, customer ID, tier, email, status |

See [database/models.py](database/models.py) for the exact DDL.

---

## Tests

```bash
python -m pytest tests/ -v
```

Tests mock `asyncpg.Pool` and patch `aiohttp` calls — no Postgres or network access required. The DB-touching `_FakePoolAcquireCtx` helper in each test file shows the pattern for mocking pool acquisition.

---

## Slash commands

The Cogs listed in `COG_MODULES` in [discord_bot/bot.py](discord_bot/bot.py) are loaded today: **x, youtube, moderation, account, roster, post, base_post, agreement, ranked**. The legend, base_finder, and ping Cogs exist but are still stubs and are commented out of the load list.

The **subscriptions** feature has no Discord command surface — it's a separate website (`web/`, see below) reachable outside of Discord, not a slash command.

| Command                          | Module             | State        |
| -------------------------------- | ------------------ | ------------ |
| `/link <coc_tag> <token>`        | account_linker     | live         |
| `/unlink <coc_tag>`              | account_linker     | live         |
| `/accounts`                      | account_linker     | live         |
| `/whois <discord_user>`          | account_linker     | live         |
| `/dumpaccounts`                  | account_linker (admin) | live     |
| `/x setchannel <channel>`        | x_monitor (admin)  | live         |
| `/x toggle`                      | x_monitor (admin)  | live         |
| `/x add <username>`              | x_monitor (admin)  | live         |
| `/x remove <username>`           | x_monitor (admin)  | live         |
| `/x list`                        | x_monitor (admin)  | live         |
| `/yt setchannel <channel>`       | youtube_feed (admin) | live       |
| `/yt toggle`                     | youtube_feed (admin) | live       |
| `/yt add <channel_id>`           | youtube_feed (admin) | live       |
| `/yt remove <channel_id>`        | youtube_feed (admin) | live       |
| `/yt list`                       | youtube_feed (admin) | live       |
| `/roster create\|delete\|rename\|list` | roster (admin) | live      |
| `/roster view <name>`            | roster (admin)     | live         |
| `/roster watch\|unwatch <name>`  | roster (admin)     | live         |
| `/roster add\|remove\|move <user>` | roster (admin)   | live         |
| `/roster addtag\|removetag\|movetag <tag>` | roster (admin) | live   |
| `/kick <member> [reason]`        | moderation (admin) | live         |
| `/ban <member> [reason]`         | moderation (admin) | live         |
| `/unban <user_id> [reason]`      | moderation (admin) | live         |
| `/purgeword <member> <word>`     | moderation (admin) | live         |
| `/post <image> <channel> [text] [ping_role]` | announce (admin) | live |
| `/group <player_tag>`            | ranked_tracker     | live         |
| `/groupextrapolate <player_tag>` | ranked_tracker     | live         |
| `/trackingon <player_tag>`       | ranked_tracker     | live         |
| `/trackingoff <player_tag>`      | ranked_tracker     | live         |
| `/trackinglist`                  | ranked_tracker     | live         |
| `/legend`                        | legend_tracker     | stub, not loaded |
| `/legend_history <days>`         | legend_tracker     | stub, not loaded |
| `/leaderboard`                   | legend_tracker     | stub, not loaded |
| `/findbase <image>`              | base_finder        | stub, not loaded |
| `/addchannel <youtube_url>`      | base_finder        | stub, not loaded |
| `/cachestats`                    | base_finder        | stub, not loaded |
| `/setpingchannel <channel>`      | ping_automator     | stub, not loaded |
| `/togglepings`                   | ping_automator     | stub, not loaded |

---

## Status

Module logic is implemented end-to-end across all packages. The wiring gap is on the Discord side:

- **Wired and live:** account linker, X monitor, YouTube feed tracker, moderation, roster, announce (`/post`, `/postbase`), agreement, and ranked tracker (`/group`) Cogs delegate to their module functions.
- **Subscriptions website (`web/`):** checkout + Stripe webhook plumbing is implemented and tested (one-time monthly purchase, not a recurring Stripe subscription — see the 2026-08-30 CLAUDE.md entry). Discord role granting off a purchase is explicitly **not** built yet — `subscriptions.discord_id` is a reserved, unpopulated column for that future pass. There's also no expiry-date tracking yet; `subscriptions` is currently just a purchase log.
- **Stubs, not loaded:** the legend, base_finder, and ping Cogs still return placeholder text and are commented out of `COG_MODULES` in [discord_bot/bot.py](discord_bot/bot.py). Their underlying module functions and scheduler jobs are implemented — only the Cog replies are stubbed.
- The legend, base-finder, and YouTube scheduler jobs run unconditionally; the X poll job is registered only when `X_COOKIES` is set, and the clan-watch job only when a clan tag (`COC_FAMILY_CLAN_TAGS` or `COC_CLAN_TAG`) is set.
- `modules/base_finder/detector.py` — CV thresholds are placeholders, marked `NOTE FOR CV ENGINEER`. Tune against real VOD frames.
- `modules/base_finder/normalizer.py` — UI crop fractions are approximate. Verify against 1080p / 1440p captures.

See [CLAUDE.md](CLAUDE.md) for project conventions and the change log.
