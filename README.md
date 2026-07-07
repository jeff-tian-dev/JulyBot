# JulyBot — Clash of Clans Discord Bot

A Discord bot for Clash of Clans clans, built around seven independent modules:

- **Account linker** — verifies a Discord user owns a given CoC account using the in-game API token flow, and stores the link. Supports multiple accounts (alts) per Discord user.
- **Legend tracker** — polls the official Clash of Clans API on a schedule, stores daily snapshots of every linked player's legend league stats, and computes day-over-day diffs.
- **Base finder** — ingests YouTube VODs from watched channels, extracts attack-loading-screen base layouts via OpenCV, and lets users find similar bases by uploading a screenshot.
- **Ping automator** — APScheduler jobs that drive the polls and ingestion, plus role-based notification hooks.
- **X monitor** — polls watched X accounts via `tweety-ns` (cookie auth) and posts new posts as Discord embeds.
- **YouTube feed tracker** — polls YouTube RSS feeds via `feedparser` and posts when a watched channel uploads a new video.
- **Moderation** — admin-only `/kick`, `/ban`, `/unban` slash commands with pre-flight validation, public taunt messages, and an audit log embed to a mod-log channel.

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
|   `-- moderation/
|       |-- actions.py        # kick / ban / unban via disnake
|       |-- validation.py     # pre-flight target checks + ModerationError
|       |-- messages.py       # public taunt quips
|       `-- logging.py        # mod-log channel embed
|-- discord_bot/
|   |-- bot.py                # create_bot() — InteractionBot factory
|   `-- commands/             # one Cog per module (account, x, youtube, moderation, + stub legend/base_finder/ping)
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
|   `-- install-service.sh    # launchd agent — auto-start on login
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
| `YOUTUBE_FEED_POLL_INTERVAL_MINUTES` | no  | `10`                                   | YouTube RSS poll cadence                           |
| `YOUTUBE_PING_ROLE_ID`          | no       | `1508359179440750602`                  | Role mention on new YouTube videos (0 = no ping)   |
| `YOUTUBE_PING_COOLDOWN_HOURS`   | no       | `3`                                    | After a ping, new YouTube videos within this window post silently |
| `MOD_LOG_CHANNEL_ID`              | no       | `1514111681222148219`                  | Channel for kick/ban/unban mod logs                |

Missing any required variable raises a clear `ValueError` at startup.

### 4. Initialize the database

```bash
.venv/bin/python scripts/init_db.py
```

This enables the `vector` extension, creates all tables on your Supabase instance, and seeds `watched_channels` with any IDs from `YOUTUBE_CHANNEL_IDS`.

### 5. Run the bot

```bash
./deploy/start.sh                  # foreground
./deploy/install-service.sh        # background via launchd (auto-start on login)
```

Startup sequence: load settings -> open asyncpg pool -> ensure tables -> start APScheduler -> connect Discord. Ctrl-C (or `./deploy/stop.sh`) triggers an ordered shutdown.

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

See [database/models.py](database/models.py) for the exact DDL.

---

## Tests

```bash
python -m pytest tests/ -v
```

Tests mock `asyncpg.Pool` and patch `aiohttp` calls — no Postgres or network access required. The DB-touching `_FakePoolAcquireCtx` helper in each test file shows the pattern for mocking pool acquisition.

---

## Slash commands

Only four Cogs are loaded today (see `COG_MODULES` in [discord_bot/bot.py](discord_bot/bot.py)): **account, x, youtube, moderation**. The legend, base_finder, and ping Cogs exist but are still stubs and are commented out of the load list.

| Command                          | Module             | State        |
| -------------------------------- | ------------------ | ------------ |
| `/link <coc_tag> <token>`        | account_linker     | live         |
| `/unlink <coc_tag>`              | account_linker     | live         |
| `/accounts`                      | account_linker     | live         |
| `/whois <discord_user>`          | account_linker     | live         |
| `/xsetchannel <channel>`         | x_monitor (admin)  | live         |
| `/xtoggle`                       | x_monitor (admin)  | live         |
| `/xadd <username>`               | x_monitor (admin)  | live         |
| `/xremove <username>`            | x_monitor (admin)  | live         |
| `/xlist`                         | x_monitor (admin)  | live         |
| `/ytsetchannel <channel>`        | youtube_feed (admin) | live       |
| `/yttoggle`                      | youtube_feed (admin) | live       |
| `/ytadd <channel_id>`            | youtube_feed (admin) | live       |
| `/ytremove <channel_id>`         | youtube_feed (admin) | live       |
| `/ytlist`                        | youtube_feed (admin) | live       |
| `/kick <member> [reason]`        | moderation (admin) | live         |
| `/ban <member> [reason]`         | moderation (admin) | live         |
| `/unban <user_id> [reason]`      | moderation (admin) | live         |
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

- **Wired and live:** account linker, X monitor, YouTube feed tracker, and moderation Cogs delegate to their module functions.
- **Stubs, not loaded:** the legend, base_finder, and ping Cogs still return placeholder text and are commented out of `COG_MODULES` in [discord_bot/bot.py](discord_bot/bot.py). Their underlying module functions and scheduler jobs are implemented — only the Cog replies are stubbed.
- The legend, base-finder, and YouTube scheduler jobs run unconditionally; the X poll job is registered only when `X_COOKIES` is set.
- `modules/base_finder/detector.py` — CV thresholds are placeholders, marked `NOTE FOR CV ENGINEER`. Tune against real VOD frames.
- `modules/base_finder/normalizer.py` — UI crop fractions are approximate. Verify against 1080p / 1440p captures.

See [CLAUDE.md](CLAUDE.md) for project conventions and the change log.
