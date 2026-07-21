# CLAUDE.md

Project-specific instructions for Claude Code working on this repo. Read top-to-bottom before making changes. Append new conventions here as they emerge — do **not** rewrite history; add new sections with a date.

---

## Project at a glance

A Clash of Clans Discord bot, Python 3.11+. Four modules: `base_finder` (image recognition pipeline over YouTube VODs), `legend_tracker` (CoC API polling + daily snapshots), `account_linker` (Discord ID <-> CoC tag verification), `ping_automator` (APScheduler jobs). Discord layer (`discord_bot/`) is a thin shim — every Cog command must delegate to module functions and format the reply, nothing more.

Storage is PostgreSQL with the `pgvector` extension. Connection is raw `asyncpg` — **do not introduce SQLAlchemy or any other ORM**.

---

## Hard rules (do not violate without asking)

1. **Config is centralized.** Every env var read goes through [config/settings.py](config/settings.py). No `os.getenv` or `load_dotenv` calls anywhere else. If you need a new setting, add it to `Settings`, `.env.example`, and the README env-var table.
2. **Discord stays thin.** Cogs in [discord_bot/commands/](discord_bot/commands/) call module functions and format embeds. They never query the DB, hit external APIs, or do CV work directly.
3. **Modules stay independently testable.** Every async function in `modules/` must be callable with a real or mocked `asyncpg.Pool` — no implicit dependency on a running Discord bot or scheduler.
4. **Discord client is `disnake`, not `discord.py`.** The two are API-incompatible at the Cog layer.
5. **Raw SQL only.** All DDL lives in [database/models.py](database/models.py); ad-hoc queries use `pool.acquire() / conn.fetch / conn.execute`. No query builders.
6. **No hardcoded magic numbers.** Thresholds, intervals, and sizes are either in [config/settings.py](config/settings.py) or named module-level constants at the top of the file (see e.g. [modules/base_finder/detector.py](modules/base_finder/detector.py)).
7. **Logging via `logging.getLogger(__name__)`.** No `print()` outside [scripts/init_db.py](scripts/init_db.py) (which is a CLI entrypoint).
8. **Never commit `.env`** — only `.env.example`. The `.gitignore` already excludes it; don't weaken that.

---

## CV stubs (intentionally incomplete)

[modules/base_finder/detector.py](modules/base_finder/detector.py) and [modules/base_finder/normalizer.py](modules/base_finder/normalizer.py) ship with placeholder thresholds. They are flagged `NOTE FOR CV ENGINEER` in the source. Do **not** "polish" the thresholds yourself — they need to be tuned against real CoC VODs by the CV engineer. Structure and interfaces are stable; just don't randomly tweak constants.

---

## Testing

- Test runner: `pytest` + `pytest-asyncio` (strict mode).
- [tests/conftest.py](tests/conftest.py) sets dummy env vars before any project import. **This is required** because `config.settings` raises on missing required vars at import time. If you add a new required env var to `Settings`, add a matching `os.environ.setdefault` line to `conftest.py`.
- DB is mocked via `MagicMock` + `AsyncMock` and a small `_FakePoolAcquireCtx` helper. See [tests/test_account_linker.py](tests/test_account_linker.py) for the pattern; reuse it.
- HTTP calls are mocked with `unittest.mock.patch.object(module, "fn", AsyncMock(...))`. Never make real CoC API or YouTube calls in tests.
- Run: `python -m pytest tests/ -v`. As of last run: 9 passed in ~10s.

---

## Adding a new feature

Default workflow:

1. Add the data model (table or column) to [database/models.py](database/models.py) as a `CREATE TABLE IF NOT EXISTS`. Bump nothing else — `init_db.py` is idempotent.
2. Implement the business logic as plain async functions in the relevant `modules/<x>/` package. Keep functions narrow and pool-arg-first: `async def thing(pool, ...)`.
3. Add unit tests with mocked pool/HTTP under `tests/`.
4. Only after the module logic lands cleanly, wire a Cog command in `discord_bot/commands/`. The Cog should be 5-15 lines per command: parse args, call module function, format reply.
5. If the work is recurring, register a job in [modules/ping_automator/scheduler.py](modules/ping_automator/scheduler.py).

---

## Things that bit us / known gotchas

- **`config.settings` raises at import time.** Tests rely on `conftest.py` to stub vars. Don't move the `_load()` call inside a function — other modules use the eager singleton.
- **`signal.SIGTERM` isn't supported on Windows.** [main.py](main.py) wraps `loop.add_signal_handler` in a `try/except NotImplementedError`. If you refactor shutdown, preserve that fallback.
- **`pgvector` requires a Postgres extension.** `init_db.py` runs `CREATE EXTENSION IF NOT EXISTS vector;` at the start of `create_tables`. On Supabase the `postgres` role has this privilege, so it just works on first run.
- **The DB is Supabase (hosted), not local.** `DATABASE_URL` is a Supabase **Session pooler** string with `?sslmode=require`. asyncpg + Supabase's pooler can't use prepared-statement caching, so [database/connection.py](database/connection.py) creates the pool with `statement_cache_size=0` and strips that param from the DSN — **don't remove either.** `scripts/init_db.py` reuses that same `get_pool()`; don't make it open its own raw pool.
- **`data/` is gitignored except for `data/bases/.gitkeep`.** Don't add tracked files under `data/` — generated images live there.
- **APScheduler `create_scheduler(pool)` takes the pool.** The original spec showed a no-arg signature, but jobs need the pool, so it's passed at construction. If you ever need other long-lived deps in jobs, do the same — don't reach for globals.

---

## Update protocol

When you discover a non-obvious convention, an architectural decision, or a workaround future-Claude should know about:

- Append it under a dated heading at the bottom of this file (e.g. `## 2026-04-28 — initial skeleton`).
- Keep entries short. If a rule needs a paragraph, it probably belongs in code or a docstring instead.
- Don't delete prior entries unless they're factually wrong; mark them superseded.

---

## 2026-04-28 — initial skeleton

- Repo bootstrapped with the four-module skeleton, full Discord stub layer, and 9 passing pytest cases.
- `requirements.txt` versions confirmed installable on Python 3.13 / Windows: disnake 2.12, asyncpg 0.31, opencv-python 4.13, yt-dlp 2026.3, imagehash 4.3, APScheduler 3.11, pgvector 0.4.
- Decision: `tests/conftest.py` was added (not in the original spec) because eager-loading settings broke test collection. Documented above.
- Decision: `.gitignore` un-ignores `data/bases/.gitkeep` specifically. The original spec listed `data/` as ignored *and* asked for a tracked `.gitkeep` — resolved by being more specific.

## 2026-06-18 — cleanup pass (foundation tidy-up)

- Removed the abandoned `twitter_stalker` experiment entirely: the module, its Discord cog, helper scripts, `requirements-twitter.txt`, tests, and the `.agents/`/`.cursor/` twitterapi skill dirs. The repo is a Clash of Clans bot only — there is no `BOT_MODE` switch.
- Deleted stale planning docs (`WORKDISTRIBUTION.md`, `BASEFINDER_PLAN.md`, `modules/base_finder/PROGRESS.md`). **`README.md` (users) and this file (agent conventions) are now the only source-of-truth docs** — don't reintroduce a separate roadmap/ownership doc; append status here instead.
- Added the `guild_settings` table to [database/models.py](database/models.py) (used by the ping_automator cog: `guild_id` PK, `ping_channel_id`, `pings_enabled`, `updated_at`). It was previously only described in the now-deleted work-split doc.
- Deploy moved from VM/systemd to local Mac Studio + launchd: `deploy/setup.sh`, `start.sh`, `stop.sh`, `install-service.sh`, `uninstall-service.sh`, `com.julybot.plist.template`. There is a `.venv` in the repo root; run via `.venv/bin/python`.
- Switched the database from local Docker Postgres to **Supabase** (hosted). Removed `deploy/docker-compose.postgres.yml` and all Docker references from `setup.sh`/`start.sh`/docs. The bot runs on the Mac Studio; the DB is remote. See the Supabase gotcha above for the `statement_cache_size=0` requirement.
- State of play: module logic is implemented; **all Discord cogs still return placeholder text** — wiring them to the module functions is the next build step (see the per-command table in README).

## 2026-06-18 — twitter_monitor module

- Added `modules/twitter_monitor/` — polls watched X accounts every 5 minutes via `tweety-ns` using burner-account cookies from `TWITTER_COOKIES`. Posts new tweets as Discord embeds (profile pic, name, text, link). Retweets are skipped.
- New tables: `twitter_watched_accounts`, `seen_tweets`. Extended `guild_settings` with `twitter_channel_id` and `twitter_enabled` (idempotent `ALTER TABLE` in `create_tables`).
- Admin-only slash commands use `default_member_permissions=disnake.Permissions(administrator=True)` — hidden from and blocked for non-admins at the Discord API level.
- Twitter is **optional**: leave `TWITTER_COOKIES` empty to disable the scheduler job; cog commands reply that monitoring is not configured.
- Session files persist under `data/twitter/` (gitignored). Use a dedicated burner X account; re-export cookies when auth fails.
- `create_scheduler(pool, bot)` now takes the Discord bot so the twitter job can post to channels. `main.py` passes `bot` and calls `close_twitter_client()` on shutdown.

## 2026-06-19 — youtube_feed module

- Added `modules/youtube_feed/` — polls watched YouTube channels every 10 minutes via `feedparser` RSS (`feeds/videos.xml`). Tracks only the latest video ID per channel; posts a Discord embed when it changes. Seeds `last_seen_video_id` on startup, on add, and as a poll safety net when NULL.
- New table: `youtube_watched_channels`. Extended `guild_settings` with `youtube_channel_id` and `youtube_enabled` (idempotent `ALTER TABLE` in `create_tables`).
- Admin-only slash commands: `/setyoutubechannel`, `/toggleyoutube`, `/youtube_add`, `/youtube_remove`, `/youtube_list`.
- `YOUTUBE_FEED_POLL_INTERVAL_MINUTES` env var (default 10). Twitter poll default also changed from 5 → 10 minutes.
- APScheduler job `poll_youtube_feed` is always registered (no auth gate). `main.py` calls `seed_unseeded_channels(pool)` before starting the scheduler.

## 2026-07-21 — /purgeword (moderation)

- Added `modules/moderation/purge.py` — `purge_user_messages(guild, target, word, moderator)` deletes every message from `target` whose content contains `word` (case-insensitive substring) across all text channels + active threads the bot can `read_message_history` + `manage_messages`. Returns a `PurgeResult` dataclass (deleted / channels_scanned / channels_skipped / failed). Pool-free — it only touches the Discord API, so tests mock disnake objects, not a pool.
- **Discord has no "all messages by user" endpoint.** The only way is to walk each channel's `history()` and filter by author — inherently slow and rate-limited. The `/purgeword` cog command therefore `defer(ephemeral=True)`s before scanning.
- **14-day bulk-delete rule:** messages younger than `BULK_DELETE_MAX_AGE_DAYS` are batched via `channel.delete_messages` (max 100/call); older ones are deleted individually with a `INDIVIDUAL_DELETE_DELAY_SECONDS` pause to respect rate limits. Constants live at the top of `purge.py`.
- **`MAX_DELETIONS_PER_RUN = 500` caps *deletions*, not messages scanned.** The cap check sits after the author + word filter, so non-matching messages never consume the budget. When hit, `PurgeResult.capped=True` and the cog tells the user to re-run; because deleted messages vanish from history, the next run's newest-first scan resumes past them (no re-deletion). Trade-off: each run re-*reads* history from the top — reads are cheap/unthrottled, so we accepted it over a `before`-timestamp resume param.
- Extended `logging.py` `Action` literal + colour/title maps with `"purge"`. Admin-only (`default_member_permissions`). No new tables or env vars.
