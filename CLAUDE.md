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
- **`pgvector` requires a Postgres extension.** `init_db.py` runs `CREATE EXTENSION IF NOT EXISTS vector;` at the start of `create_tables`. The DB user must have privileges to create extensions on first run.
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
