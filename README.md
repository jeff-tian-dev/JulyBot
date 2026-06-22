# JulyBot — Clash of Clans Discord Bot

A Discord bot for Clash of Clans clans, built around five independent modules:

- **Base finder** — ingests YouTube VODs from watched channels, extracts attack-loading-screen base layouts via OpenCV, and lets users find similar bases by uploading a screenshot.
- **Legend tracker** — polls the official Clash of Clans API on a schedule, stores daily snapshots of every linked player's legend league stats, and computes day-over-day diffs.
- **Account linker** — verifies a Discord user owns a given CoC account using the in-game API token flow, and stores the link.
- **Ping automator** — APScheduler jobs that drive the polls and ingestion, plus role-based notification hooks.
- **Twitter monitor** — polls watched X/Twitter accounts via `tweety-ns` (cookie auth) and posts new tweets as Discord embeds.
- **YouTube feed tracker** — polls YouTube RSS feeds via `feedparser` and posts when a watched channel uploads a new video.

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
| Twitter/X       | `tweety-ns` (cookie auth, no API key)           |
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
|   |-- twitter_monitor/
|   |   |-- client.py         # tweety-ns session wrapper
|   |   |-- storage.py        # watch list + seen tweet dedup
|   |   |-- poller.py         # poll accounts, post embeds
|   |   `-- embeds.py         # Discord embed builder
|   |-- youtube_feed/
|   |   |-- fetcher.py        # RSS fetch via feedparser
|   |   |-- storage.py        # watch list + last_seen_video_id
|   |   |-- poller.py         # poll channels, post embeds
|   |   `-- embeds.py         # Discord embed builder
|   `-- ping_automator/
|       `-- scheduler.py      # APScheduler jobs + ping hook
|-- discord_bot/
|   |-- bot.py                # create_bot() — InteractionBot factory
|   `-- commands/             # one Cog per module (account, legend, base_finder, ping, twitter)
|-- tests/
|   |-- conftest.py           # stubs env vars before project imports
|   |-- test_account_linker.py
|   |-- test_legend_tracker.py
|   |-- test_base_finder.py
|   |-- test_twitter_monitor.py
|   `-- test_youtube_feed.py
|   `-- test_youtube_feed.py
|-- scripts/
|   |-- init_db.py            # standalone DB initializer (create tables + seed channels)
|   `-- ...                   # base_finder dev/validation tools (scan_video, run_local_pipeline, etc.)
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
| `COC_API_BASE_URL`              | no       | `https://api.clashofclans.com/v1`      |                                                    |
| `DATABASE_URL`                  | yes      | —                                      | Supabase **Session pooler** string + `?sslmode=require` (see `.env.example`) |
| `BASE_IMAGE_DIR`                | no       | `/Users/jefftian/JulyBot/data/bases`   | Where extracted base PNGs are written              |
| `BASE_CACHE_SIZE`               | no       | `750`                                  | Sliding-window cap; older rows are evicted         |
| `YOUTUBE_CHANNEL_IDS`           | no       | empty                                  | Comma-separated list, e.g. `UCabc,UCxyz`           |
| `LEGEND_POLL_INTERVAL_MINUTES`  | no       | `60`                                   | Legend snapshot cadence                            |
| `CACHE_REFRESH_INTERVAL_HOURS`  | no       | `24`                                   | Base-finder ingestion cadence                      |
| `TWITTER_COOKIES`               | no       | empty                                  | Semicolon-delimited browser cookies; empty disables Twitter monitor |
| `TWITTER_SESSION_NAME`          | no       | `julybot_twitter`                      | tweety session file basename under `data/twitter/` |
| `TWITTER_POLL_INTERVAL_MINUTES` | no       | `10`                                   | X account poll cadence                             |
| `TWITTER_PING_ROLE_ID`          | no       | `0`                                    | Role mention on new tweets (0 = no ping)           |
| `YOUTUBE_FEED_POLL_INTERVAL_MINUTES` | no  | `10`                                   | YouTube RSS poll cadence                           |
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
| `guild_settings`   | Per-guild ping, Twitter, and YouTube channel settings                     |
| `twitter_watched_accounts` | X accounts watched per guild, with `last_seen_tweet_id`           |
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

## Slash commands (currently stubs)

| Command                          | Module             |
| -------------------------------- | ------------------ |
| `/link <coc_tag> <token>`        | account_linker     |
| `/unlink`                        | account_linker     |
| `/whois <discord_user>`          | account_linker     |
| `/legend`                        | legend_tracker     |
| `/legend_history <days>`         | legend_tracker     |
| `/leaderboard`                   | legend_tracker     |
| `/findbase <image>`              | base_finder        |
| `/addchannel <youtube_url>`      | base_finder        |
| `/cachestats`                    | base_finder        |
| `/setpingchannel <channel>`      | ping_automator     |
| `/togglepings`                   | ping_automator     |
| `/settwitterchannel <channel>`   | twitter_monitor (admin only) |
| `/toggletwitter`                 | twitter_monitor (admin only) |
| `/twitter_add <username>`        | twitter_monitor (admin only) |
| `/twitter_remove <username>`     | twitter_monitor (admin only) |
| `/twitter_list`                  | twitter_monitor (admin only) |
| `/setyoutubechannel <channel>`   | youtube_feed (admin only)    |
| `/toggleyoutube`                 | youtube_feed (admin only)    |
| `/youtube_add <channel_id>`      | youtube_feed (admin only)    |
| `/youtube_remove <channel_id>`   | youtube_feed (admin only)    |
| `/youtube_list`                  | youtube_feed (admin only)    |

Most commands still respond with placeholder text; replace with real handlers as features come online.

---

## Status

Skeleton complete. Module logic is implemented end-to-end except:

- `modules/base_finder/detector.py` — CV thresholds are placeholders, marked `NOTE FOR CV ENGINEER`. Tune against real VOD frames.
- `modules/base_finder/normalizer.py` — UI crop fractions are approximate. Verify against 1080p / 1440p captures.
- All Discord Cogs respond with placeholder text; replace with real handlers as features come online.

See [CLAUDE.md](CLAUDE.md) for project conventions and the change log.
