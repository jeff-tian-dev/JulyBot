# JulyBot — Clash of Clans Discord Bot

A Discord bot for Clash of Clans clans, built around nine independent modules:

- **Account linker** — verifies a Discord user owns a given CoC account using the in-game API token flow, and stores the link. Supports multiple accounts (alts) per Discord user.
- **Legend tracker** — polls the official Clash of Clans API on a schedule, stores daily snapshots of every linked player's legend league stats, and computes day-over-day diffs.
- **Base finder** — ingests YouTube VODs from watched channels, extracts attack-loading-screen base layouts via OpenCV, and lets users find similar bases by uploading a screenshot.
- **Ping automator** — APScheduler jobs that drive the polls and ingestion, plus role-based notification hooks.
- **X monitor** — polls watched X accounts via `tweety-ns` (cookie auth) and posts new posts as Discord embeds.
- **YouTube feed tracker** — polls YouTube RSS feeds via `feedparser` and posts when a watched channel uploads a new video.
- **Moderation** — admin-only `/kick`, `/ban`, `/unban` slash commands with pre-flight validation, public taunt messages, and an audit log embed to a mod-log channel.
- **Roster** — admin-managed named groups of players (by Discord user or raw CoC tag). Optionally *watch* a roster to get alerts when a member leaves or rejoins the server's clan family (set per server) and to track how long each member has been out.
- **Ranked tracker** — looks up a player's current Ranked Battles weekly tournament group (`/group`) and renders a refreshable dashboard of defenses received this week, with no stored history — everything is fetched live from the CoC API on each refresh.
- **Subscriptions** — `/subscribe <member>` runs a purchase through a ticket: one message carries a link button per product the server set up (e.g. "July Sub" → the website), where the buyer pays through Stripe, then advances to confirmed once an admin picks the buyer's payment out of Stripe. Terms are accepted at checkout, so there is no separate agreement step. The dropdown lists only payments made through **this server's own** Stripe Payment Links, so servers sharing one Stripe account never see each other's buyers. Confirmed purchases are recorded in `subscribers`; recurring ones are re-checked against Stripe on a schedule so cancellations and failed renewals show up on their own (a one-time payment has no status that can change, so it is not re-polled). Granting Discord access is still manual.
- **Products** — each server's `/subscribe` buttons, managed with `/product add <label> <link>|remove|list`. Adding one scans its page for Stripe Payment Links and claims them for the server (the first server to add a link owns it); that's what scopes Confirm's list and records which product a confirmed payment was for.
- **Roll call** — every day at the legend reset (05:00 UTC), lists the members of watched rosters who weren't in one of the server's clans at that moment, with the clan they were in instead. Posts to its own channel (`/setup rollcall`) and pings each listed Discord user.
- **On / testing / off** — `/toggle feature:<Roll call|Watchdog|X|YouTube> mode:<On|Testing|Off>`. Testing posts the same messages to the server's test channel (`/setup testchannel`) with every ping replaced by a plain name, so nobody is notified. Off posts nothing, but tracking keeps running.
- **Setup and help** — `/setup status|modlog|watchdog|rollcall|testchannel|clan add|clan remove|pingrole` configures a server (everything is per server); `/help` lists the commands the viewer can use. The bot posts a welcome pointing at both when it joins a server.

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
|   |-- guild_config/
|   |   |-- storage.py        # per-server channels, ping roles, clan family (/setup)
|   |   `-- setup.py          # clan validation against the CoC API
|   |-- ping_automator/
|   |   `-- scheduler.py      # APScheduler jobs + ping hook
|   |-- moderation/
|   |   |-- actions.py        # kick / ban / unban via disnake
|   |   |-- validation.py     # pre-flight target checks + ModerationError
|   |   |-- messages.py       # public taunt quips
|   |   `-- logging.py        # mod-log channel embed
|   |-- roster/
|   |   |-- storage.py        # roster CRUD, membership, clan-membership state
|   |   |-- watcher.py        # watchdog poller: leave/rejoin alerts + absence
|   |   `-- rollcall.py       # daily roll call: who missed the legend reset
|   |-- ranked_tracker/
|   |   |-- poller.py         # CoC API client for /players and /leaguegroup
|   |   |-- group.py          # group resolution, defense histogram, embed rendering
|   |   |-- extrapolate.py    # per-member 30-attack pace extrapolation (/groupextrapolate)
|   |   `-- tracking.py       # /trackingon|off|list -- DM alerts on status change
|   |-- subscriptions/
|   |   |-- products.py      # per-server /subscribe buttons (label + page)
|   |   |-- payment_links.py # which server owns which Stripe Payment Link; Confirm's list
|   |   |-- stripe_api.py    # the only module importing `stripe`; read-only lookups
|   |   |-- refresh.py       # scheduled re-check of stored subscriptions
|   |   `-- storage.py       # subscribers CRUD
|   `-- agreements/
|       |-- document.py      # the website's terms text, stored with manual purchases
|       |-- storage.py       # agreements CRUD: pending -> confirmed / voided
|       `-- validation.py    # status embed, lookup embed, receipt document
|-- discord_bot/
|   |-- bot.py                # create_bot() — InteractionBot factory
|   `-- commands/             # one Cog per module (account, x, youtube, moderation, roster, post, base_post, agreement, ranked, subscribe, purchase, product, setup, help, + stub legend/base_finder/ping)
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
| `DISCORD_GUILD_ID`              | no       | `0`                                    | The home server: legacy values below are copied into its `/setup` settings once. Commands are global regardless |
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
| `X_PING_ROLE_ID`                | no       | `0`                                    | **Legacy** — per server via `/setup pingrole`; copied into the home server once (`TWITTER_PING_ROLE_ID` still accepted) |
| `X_PING_COOLDOWN_HOURS`         | no       | `3`                                    | After a ping, new X posts within this window post silently |
| `X_MAX_CONSECUTIVE_FAILURES`    | no       | `3`                                    | Consecutive connection failures before the poller alerts once and pauses (resumes on restart) |
| `X_ALERT_CHANNEL_ID`            | no       | `0`                                    | Channel for the "X monitoring stopped" alert (0 = fall back to `MOD_LOG_CHANNEL_ID`) |
| `X_WEBSITE_ACCOUNTS`            | no       | `yun2k411`                             | Comma-separated X accounts whose posts are saved to `website_x_posts` for seventhmonth.com; never posted to Discord |
| `YOUTUBE_FEED_POLL_INTERVAL_MINUTES` | no  | `10`                                   | YouTube RSS poll cadence                           |
| `YOUTUBE_PING_ROLE_ID`          | no       | `0`                                    | **Legacy** — per server via `/setup pingrole`; copied into the home server once |
| `YOUTUBE_PING_COOLDOWN_HOURS`   | no       | `3`                                    | After a ping, new YouTube videos within this window post silently |
| `MOD_LOG_CHANNEL_ID`              | no       | `0`                                    | **Legacy** — per server via `/setup modlog`; copied into the home server once. Still the fallback for `X_ALERT_CHANNEL_ID` |
| `COC_FAMILY_CLAN_TAGS`           | no       | empty                                  | **Legacy** — clan families are per server via `/setup clan add`. Comma-separated, **without `#`**; copied into the home server once |
| `COC_CLAN_TAG`                   | no       | empty                                  | **Legacy** — merged into the family above for the one-time copy |
| `CLAN_WATCH_CHANNEL_ID`          | no       | `0`                                    | **Legacy** — per server via `/setup watchdog`; copied into the home server once |
| `CLAN_WATCH_POLL_INTERVAL_MINUTES` | no     | `10`                                   | How often watched rosters are checked against the clan family |
| `RANKED_TRACKING_POLL_INTERVAL_MINUTES` | no | `20`                              | How often tracked players' likely-to-be-hit status is checked for `/trackingon` DM alerts |
| `STRIPE_SECRET_KEY`              | no       | empty                                   | Secret key used read-only to match product pages' Payment Links, list a server's checkouts when confirming a payment, and refresh subscriber status. Empty disables all three; the bot still runs |
| `SUBSCRIBER_REFRESH_INTERVAL_MINUTES` | no  | `60`                                    | How often stored subscriptions are re-checked against Stripe |

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

### 6. Deploying a code update

Code reaches the Mac Studio via `git pull`:

```bash
cd /Users/jefftian/JulyBot
git pull
chmod +x deploy/*.sh                        # only if the release added new scripts
.venv/bin/pip install -r requirements.txt   # only if dependencies changed
.venv/bin/python scripts/init_db.py         # only if the schema changed; idempotent
./deploy/install-service.sh                 # restart the bot
```

`.env` is not in git — when a release adds new environment variables, copy the new keys from `.env.example` into the Mac Studio's `.env` before restarting, or startup fails with a `ValueError` naming the missing variable. See [deploy/README.md](deploy/README.md#deploying-a-code-update) for details.

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
| `website_x_posts`  | Post IDs from `X_WEBSITE_ACCOUNTS`, saved for seventhmonth.com and never posted to Discord. The site reads it with a read-only login |
| `youtube_watched_channels` | YouTube channels watched per guild, with `last_seen_video_id`     |
| `rosters`          | Named player groups per guild (`watched` flag opts into watchdog)       |
| `roster_members`   | Roster membership — one row per Discord user **or** raw CoC tag            |
| `clan_membership`  | Per-tag clan in/out state + accumulated absence, kept by the watchdog poller |
| `coc_player_cache` | Short-TTL cache of live CoC player name + current clan, shared across rosters |
| `ranked_tracking`  | `/trackingon` subscriptions: Discord user + CoC tag pairs, with the last-seen likely-to-be-hit status |
| `agreements`       | The purchase record `/subscribe` and `/agreement record` write: buyer, plus `confirmed_at` / `voided_at`. Non-Stripe purchases also carry `payment_method` / `payer_name` / `amount_cents` and the buyer's in-Discord signature |
| `products`         | Admin-managed one-time products for `/product`: name, Stripe Payment Link, optional description. Soft-deleted (`removed_at`) so past purchases keep their product |
| `subscribers`      | One row per purchase period (a resub adds a row, so it's a history): who, and either the Stripe subscription/payment or — for a purchase paid outside Stripe — the method and amount |

See [database/models.py](database/models.py) for the exact DDL.

---

## Tests

```bash
python -m pytest tests/ -v
```

Tests mock `asyncpg.Pool` and patch `aiohttp` calls — no Postgres or network access required. The DB-touching `_FakePoolAcquireCtx` helper in each test file shows the pattern for mocking pool acquisition.

---

## Slash commands

The Cogs listed in `COG_MODULES` in [discord_bot/bot.py](discord_bot/bot.py) are loaded today: **x, youtube, moderation, account, roster, post, base_post, agreement, ranked, subscribe, purchases**. The legend, base_finder, and ping Cogs exist but are still stubs and are commented out of the load list.



| Command                          | Module             | State        |
| -------------------------------- | ------------------ | ------------ |
| `/link <coc_tag> <token>`        | account_linker     | live         |
| `/unlink <coc_tag>`              | account_linker     | live         |
| `/accounts`                      | account_linker     | live         |
| `/whois <discord_user>`          | account_linker     | live         |
| `/dumpaccounts`                  | account_linker (admin) | live     |
| `/x setchannel <channel>`        | x_monitor (admin)  | live         |
| `/x add <username>`              | x_monitor (admin)  | live         |
| `/x remove <username>`           | x_monitor (admin)  | live         |
| `/x list`                        | x_monitor (admin)  | live         |
| `/yt setchannel <channel>`       | youtube_feed (admin) | live       |
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
| `/purgeword <word> [member] [channel]` | moderation (admin) | live |
| `/purgecancel` | moderation (admin) | live |
| `/post <image> <channel> [text] [ping_role]` | announce (admin) | live |
| `/group <player_tag>`            | ranked_tracker     | live         |
| `/groupextrapolate <player_tag>` | ranked_tracker     | live         |
| `/trackingon <player_tag>`       | ranked_tracker     | live         |
| `/trackingoff <player_tag>`      | ranked_tracker     | live         |
| `/trackinglist`                  | ranked_tracker     | live         |
| `/subscribe <member>`            | subscriptions (admin) | live      |
| `/purchases list [member]`       | subscriptions (admin) | live      |
| `/purchases relink <id>`         | subscriptions (admin) | live      |
| `/purchases archive`             | subscriptions (admin) | live      |
| `/product add <label> <link>`    | subscriptions (admin) | live      |
| `/product remove <label>` / `/product list` | subscriptions (admin) | live |
| `/setup status\|modlog\|watchdog\|rollcall\|testchannel\|pingrole` | guild_config (admin) | live |
| `/toggle <feature> <mode>`       | guild_config (admin) | live      |
| `/setup clan add\|remove <tag>`  | guild_config (admin) | live      |
| `/help`                          | help               | live         |
| `/agreement record <member> <method> <payer_name> <amount>` | agreements (admin) | live |
| `/agreement lookup <member>`     | agreements (admin) | live         |
| `/agreement receipt <id>`        | agreements (admin) | live         |
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
- **Subscriptions:** `/subscribe <member>` posts a purchase status message in the ticket — Stripe Payment Link buttons, then admin confirmation — recording each step in `agreements`. Buyers accept the Terms during Stripe Checkout, so that consent record lives in Stripe rather than in this database. Confirming requires the admin to pick the buyer's subscription from Stripe, which is then tracked in `subscribers` and refreshed on a schedule. The bot reads Stripe over its API rather than receiving webhooks, so status is as fresh as the last poll and which subscription belongs to which Discord user remains a human match (recorded in `subscribers.linked_by`). Granting Discord roles off an active subscription is **not** built yet. See the 2026-08-30 CLAUDE.md entries.
- **Stubs, not loaded:** the legend, base_finder, and ping Cogs still return placeholder text and are commented out of `COG_MODULES` in [discord_bot/bot.py](discord_bot/bot.py). Their underlying module functions and scheduler jobs are implemented — only the Cog replies are stubbed.
- The legend, base-finder, and YouTube scheduler jobs run unconditionally; the X poll job is registered only when `X_COOKIES` is set. The watchdog and roll call jobs always run and skip any server with no clans set up (or, for roll call, with it toggled off, its default).
- `modules/base_finder/detector.py` — CV thresholds are placeholders, marked `NOTE FOR CV ENGINEER`. Tune against real VOD frames.
- `modules/base_finder/normalizer.py` — UI crop fractions are approximate. Verify against 1080p / 1440p captures.

See [CLAUDE.md](CLAUDE.md) for project conventions and the change log.
