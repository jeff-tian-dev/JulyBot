# JulyBot — Clash of Clans Discord Bot

A Discord bot for Clash of Clans clans, built around four independent modules:

- **Base finder** — ingests YouTube VODs from watched channels, extracts attack-loading-screen base layouts via OpenCV, and lets users find similar bases by uploading a screenshot.
- **Legend tracker** — polls the official Clash of Clans API on a schedule, stores daily snapshots of every linked player's legend league stats, and computes day-over-day diffs.
- **Account linker** — verifies a Discord user owns a given CoC account using the in-game API token flow, and stores the link.
- **Ping automator** — APScheduler jobs that drive the polls and ingestion, plus role-based notification hooks.

The Discord layer (`disnake` Cogs) is a thin shim. Each module is a plain Python package, callable and testable without a running bot.

---

## Tech stack

| Concern         | Choice                                          |
| --------------- | ----------------------------------------------- |
| Language        | Python 3.11+                                    |
| Discord         | `disnake` (not `discord.py`)                    |
| Database        | PostgreSQL with `pgvector` extension            |
| DB driver       | Raw `asyncpg` — no ORM                          |
| HTTP            | `aiohttp` (single shared session per module)    |
| YouTube         | `yt-dlp` (stream URLs, never full downloads)    |
| Image / CV      | `opencv-python`, `Pillow`, `numpy`, `imagehash` |
| Scheduling      | `APScheduler` (`AsyncIOScheduler`)              |
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
|   `-- ping_automator/
|       `-- scheduler.py      # APScheduler jobs + ping hook
|-- discord_bot/
|   |-- bot.py                # create_bot() — InteractionBot factory
|   `-- commands/             # one Cog per module (account, legend, base_finder, ping)
|-- tests/
|   |-- conftest.py           # stubs env vars before project imports
|   |-- test_account_linker.py
|   |-- test_legend_tracker.py
|   `-- test_base_finder.py
|-- scripts/
|   `-- init_db.py            # standalone DB initializer
|-- data/bases/               # generated base images (gitignored except .gitkeep)
|-- main.py                   # entry point — pool + scheduler + bot
|-- requirements.txt
|-- .env.example
|-- CLAUDE.md                 # project conventions for Claude Code
`-- README.md                 # this file
```

---

## Setup

### 1. Prerequisites

- Python 3.11 or newer (tested on 3.13)
- PostgreSQL 14+ with the `pgvector` extension installed:
  ```sql
  CREATE EXTENSION IF NOT EXISTS vector;
  ```
  The init script runs this for you, but the DB user needs `CREATE` privileges on the database.
- A Discord bot application (token + guild ID)
- A Clash of Clans developer API token from https://developer.clashofclans.com

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
```

### 3. Configure environment

Copy the template and fill in your secrets:

```bash
cp .env.example .env
```

| Variable                        | Required | Default                                | Notes                                              |
| ------------------------------- | -------- | -------------------------------------- | -------------------------------------------------- |
| `DISCORD_TOKEN`                 | yes      | —                                      | Bot token from the Discord developer portal        |
| `DISCORD_GUILD_ID`              | no       | `0`                                    | Test guild for instant slash-command sync          |
| `COC_API_TOKEN`                 | yes      | —                                      | From developer.clashofclans.com (IP-locked)        |
| `COC_API_BASE_URL`              | no       | `https://api.clashofclans.com/v1`      |                                                    |
| `DATABASE_URL`                  | yes      | —                                      | `postgresql://user:pass@host:port/db`              |
| `BASE_IMAGE_DIR`                | no       | `./data/bases`                         | Where extracted base PNGs are written              |
| `BASE_CACHE_SIZE`               | no       | `750`                                  | Sliding-window cap; older rows are evicted         |
| `YOUTUBE_CHANNEL_IDS`           | no       | empty                                  | Comma-separated list, e.g. `UCabc,UCxyz`           |
| `LEGEND_POLL_INTERVAL_MINUTES`  | no       | `60`                                   | Legend snapshot cadence                            |
| `CACHE_REFRESH_INTERVAL_HOURS`  | no       | `24`                                   | Base-finder ingestion cadence                      |

Missing any required variable raises a clear `ValueError` at startup.

### 4. Initialize the database

```bash
python scripts/init_db.py
```

This creates the `vector` extension, all four tables, and seeds `watched_channels` with any IDs from `YOUTUBE_CHANNEL_IDS`.

### 5. Run the bot

```bash
python main.py
```

Startup sequence: load settings -> open asyncpg pool -> ensure tables -> start APScheduler -> connect Discord. Ctrl-C triggers an ordered shutdown of all three.

---

## Database schema

| Table              | Purpose                                                                   |
| ------------------ | ------------------------------------------------------------------------- |
| `users`            | Discord ID <-> CoC tag links, with `verified` flag                        |
| `legend_snapshots` | One row per `(coc_tag, snapshot_date)`; trophies + attack/defense counters |
| `base_cache`       | Extracted base images: path, pHash, source, town hall, `vector(512)` embedding |
| `watched_channels` | YouTube channel IDs the base finder pulls from                            |

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

All currently respond with `[<module>] This command is not yet implemented.` — wire them up in [discord_bot/commands/](discord_bot/commands/) once the underlying module is exercised.

---

## Status

Skeleton complete. Module logic is implemented end-to-end except:

- `modules/base_finder/detector.py` — CV thresholds are placeholders, marked `NOTE FOR CV ENGINEER`. Tune against real VOD frames.
- `modules/base_finder/normalizer.py` — UI crop fractions are approximate. Verify against 1080p / 1440p captures.
- All Discord Cogs respond with placeholder text; replace with real handlers as features come online.

See [CLAUDE.md](CLAUDE.md) for project conventions and the change log.
