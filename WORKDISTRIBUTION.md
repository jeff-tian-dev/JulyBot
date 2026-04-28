# Work Distribution — JulyBot

This document defines the exact ownership split between the two developers.
Read this before touching any file. If a task isn't listed under your name, ask before working on it.

---

## Schema addition (do this together first, before splitting)

Before either developer starts their work, add the missing `guild_settings` table to `database/models.py`.
Add it to `CREATE_TABLES` alongside the existing four tables:

```sql
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id BIGINT PRIMARY KEY,
    ping_channel_id BIGINT,
    pings_enabled BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

Also add `"guild_settings"` to the `ALL_TABLES` tuple in `models.py` so `drop_tables()` covers it.
Run `python scripts/init_db.py` after adding it to confirm the table creates cleanly.
Commit this change on `main` before either developer branches off.

---

## Developer A — Base Finder (image pipeline, CV, Discord base commands)

### Owns these files entirely
- `modules/base_finder/detector.py`
- `modules/base_finder/normalizer.py`
- `modules/base_finder/matcher.py`
- `modules/base_finder/pipeline.py`
- `discord_bot/commands/base_finder_commands.py`
- `tests/test_base_finder.py`

### Does NOT touch
- Anything in `modules/account_linker/`
- Anything in `modules/legend_tracker/`
- `modules/ping_automator/scheduler.py` (read-only reference is fine)
- `discord_bot/commands/account_commands.py`
- `discord_bot/commands/legend_commands.py`
- `discord_bot/commands/ping_commands.py`

---

### Task 1 — Tune the loading screen detector

File: `modules/base_finder/detector.py`

The three threshold blocks at the top of the file are placeholders marked `NOTE FOR CV ENGINEER`.
Your job is to tune them against real data.

Steps:
1. Pick 5-10 recent VODs from CoC YouTubers (use channels you'll eventually add to `watched_channels`).
2. Write a throwaway script (do not commit it) that samples frames from a video at 2fps and saves
   every 10th frame to a local folder so you can visually inspect them.
3. Find frames that are definitively loading screens and frames that are definitively not.
4. Tune these three constants until `is_loading_screen()` returns True on all loading frames
   and False on all non-loading frames in your sample:
   - `BRIGHTNESS_THRESHOLD` — loading screens are dark; find the right mean-V cutoff
   - `PROGRESS_BAR_Y_RANGE` — the vertical band where the loading bar lives; measure it as a fraction of frame height
   - `COLOR_SIGNATURE_HSV_LOWER` / `COLOR_SIGNATURE_HSV_UPPER` — the warm brown/orange palette of the CoC loading screen
5. Once tuned, add a docstring to `is_loading_screen()` documenting what you found:
   tested resolution(s), threshold values chosen, and rough false-positive/false-negative rate on your sample.

No structural changes to the function signatures — only the constant values change.

---

### Task 2 — Verify and fix the base normalizer

File: `modules/base_finder/normalizer.py`

The UI crop fractions (`TOP_UI_FRACTION = 0.08`, `BOTTOM_UI_FRACTION = 0.20`) are guesses.
Verify them against real CoC screenshots.

Steps:
1. Take at least 3 screenshots from the CoC game client at different resolutions
   (1080p, 1440p, and your phone if applicable). These should be the fully-zoomed-out
   base view immediately after a loading screen clears — the exact frames the pipeline will capture.
2. Measure in pixels where the top UI bar ends and where the bottom troop bar begins,
   as a fraction of total frame height.
3. Update `TOP_UI_FRACTION` and `BOTTOM_UI_FRACTION` to the correct values.
4. Verify that `normalize_base()` on one of your test frames:
   - Returns a non-None result
   - Produces a 1080x1080 image
   - Contains no visible UI chrome (no gold/elixir bar at top, no troop bar at bottom)
5. Update the docstring with the resolutions tested and measurements taken.

Also verify `save_base_image()` works end-to-end:
- Call it with a real normalized frame
- Confirm the file is written to `BASE_IMAGE_DIR`
- Confirm the returned path exists on disk

---

### Task 3 — Fix matcher scaling and test it

File: `modules/base_finder/matcher.py`

The current `find_matching_bases()` does a full table scan (`SELECT phash FROM base_cache`)
and computes Hamming distance in Python. This is fine for a 100-entry cache but will slow down
noticeably at 750 entries and is doing unnecessary round-trips.

Fix:
1. Replace the full table scan in `is_duplicate()` with a query that pulls only the pHash column
   and compares in Python — this is already what it does, but add a comment explaining why
   pgvector isn't used here (pHash is a string, not a float vector; pgvector is for embedding search).
2. In `find_matching_bases()`, add a `LIMIT 200` to the SELECT as a pragmatic cap — at 750 entries
   you're comparing every row anyway, but this prevents runaway queries if the cache ever grows.
3. Add an index to `base_cache.phash` in `database/models.py` so exact-match lookups are fast:
   ```sql
   CREATE INDEX IF NOT EXISTS idx_base_cache_phash ON base_cache (phash);
   ```
   Add this immediately after the `CREATE_BASE_CACHE` DDL statement. Do not change the table definition.
4. Write 2 new tests in `tests/test_base_finder.py`:
   - `test_find_matching_bases_no_results`: mock pool returns empty fetch, assert empty list returned
   - `test_find_matching_bases_returns_sorted`: mock pool returns 3 rows with known pHashes at
     different distances from the query hash, assert results are sorted by similarity_score descending

---

### Task 4 — Test the pipeline end-to-end

File: `modules/base_finder/pipeline.py`

The pipeline logic is structurally complete but untested against real YouTube content.

Steps:
1. Add a YouTube channel to your local `watched_channels` table directly via `scripts/init_db.py`
   or a psql/Supabase SQL editor:
   ```sql
   INSERT INTO watched_channels (channel_id, channel_name)
   VALUES ('UCxxxxxxxxxxxxxxxx', 'YourTestChannel');
   ```
2. Write a throwaway test script (do not commit) that calls `run_pipeline(pool)` directly
   and prints the summary dict. Run it and confirm:
   - `get_video_urls()` returns a non-empty list
   - At least one video is processed without exception
   - At least one base image appears in `data/bases/`
   - The `base_cache` table has new rows in Supabase
3. Test `enforce_cache_limit()` by temporarily setting `BASE_CACHE_SIZE=5` in `.env`,
   inserting 10 dummy rows into `base_cache`, and confirming the function deletes the 5 oldest.
4. Fix any bugs found. Common ones to watch for:
   - yt-dlp channel URL format — some channels use `/channel/UC...` others use `/@handle`.
     Handle both in `get_video_urls()` by detecting whether the input looks like a UC... ID
     or a handle and constructing the URL accordingly.
   - OpenCV failing to open the stream URL — add a retry (max 2 attempts, 3s delay) before
     giving up on a video.
   - `captured_at` ordering in `enforce_cache_limit` — confirm `ORDER BY captured_at DESC OFFSET $1`
     correctly keeps the newest rows and deletes the oldest.

---

### Task 5 — Wire the Discord base finder commands

File: `discord_bot/commands/base_finder_commands.py`

Replace the three stubs with real implementations. Each command must call the relevant
module function and return a formatted embed. No business logic in the command file.

**`/findbase <image>`**

```
1. Download the attachment bytes using `await image.read()`
2. Decode to a numpy array: cv2.imdecode(np.frombuffer(bytes, np.uint8), cv2.IMREAD_COLOR)
3. Call matcher.find_matching_bases(pool, query_image, top_n=5)
4. If empty result: reply ephemeral "No matching bases found in the cache."
5. If results: build a disnake.Embed with:
   - Title: "Base matches found"
   - One field per result: "Match {n} — {similarity_score:.0%} similarity"
     with source_channel and source_url (if available) as the field value
   - Footer: "Cache contains X bases" (fetch count with SELECT COUNT(*) FROM base_cache)
6. Reply with the embed (ephemeral=False so the clan can see it)
```

**`/addchannel <youtube_url>`**

```
1. Extract the channel ID from the URL. Accept two formats:
   - https://www.youtube.com/channel/UCxxxxxxx  → extract UCxxxxxxx
   - https://www.youtube.com/@handle            → store the handle as-is for now
2. INSERT INTO watched_channels (channel_id) VALUES ($1) ON CONFLICT DO NOTHING
3. Reply ephemeral: "Channel added." or "Channel already in watch list."
```

**`/cachestats`**

```
1. SELECT COUNT(*), MIN(captured_at), MAX(captured_at) FROM base_cache
2. SELECT COUNT(*) FROM watched_channels
3. Build an embed:
   - "Cached bases: X / 750"
   - "Watched channels: Y"
   - "Oldest entry: <date>"
   - "Newest entry: <date>"
4. Reply ephemeral
```

The `pool` must be injected into the Cog at construction time. Update `BaseFinderCommands.__init__`
to accept `pool: asyncpg.Pool` and store it as `self.pool`. Update `setup(bot)` to pass the pool:
the bot instance will need to carry the pool — see how Developer B handles this in the account
and legend Cogs and follow the same pattern.

---

### Task 6 — Expand base finder tests

File: `tests/test_base_finder.py`

Add these tests (all with mocked pool/HTTP, no real DB or network):

- `test_normalize_base_returns_canonical_size`: pass a valid 1280x720 checkerboard frame,
  assert output shape is (1080, 1080, 3)
- `test_save_base_image_creates_file`: call `save_base_image` with a real frame and a `tmp_path`
  (pytest fixture), assert the file exists and is a valid PNG
- `test_enforce_cache_limit_deletes_oldest`: mock pool returns 3 rows beyond the limit,
  mock `os.remove`, assert `enforce_cache_limit` returns 3
- `test_get_video_urls_invalid_channel`: pass a garbage channel ID to `get_video_urls`,
  assert it returns an empty list rather than raising

---

## Developer B — CoC API, Legend Tracker, Account Linker, Discord (all non-base commands)

### Owns these files entirely
- `modules/account_linker/linker.py`
- `modules/legend_tracker/poller.py`
- `modules/legend_tracker/snapshots.py`
- `modules/ping_automator/scheduler.py`
- `discord_bot/commands/account_commands.py`
- `discord_bot/commands/legend_commands.py`
- `discord_bot/commands/ping_commands.py`
- `discord_bot/bot.py` (pool injection pattern)
- `tests/test_account_linker.py`
- `tests/test_legend_tracker.py`

### Does NOT touch
- Anything in `modules/base_finder/`
- `discord_bot/commands/base_finder_commands.py`
- `tests/test_base_finder.py`

---

### Task 1 — Verify CoC API response shapes

File: `modules/legend_tracker/poller.py`

The skeleton maps CoC API fields based on documentation assumptions. Verify against real API responses.

Steps:
1. Get your CoC API token from https://developer.clashofclans.com — whitelist your current IP.
2. Use curl or a throwaway Python script to call `GET /players/%23YOURTAG` and print the full response.
3. Verify these field paths in `get_legend_stats()`:
   - `player["league"]["id"]` — confirm `29000022` is the correct Legend League ID
   - `player["legendStatistics"]["currentSeason"]` — confirm this key exists and what it contains
   - `player["trophies"]` — confirm this is total trophies, not season trophies
   - `player["attackWins"]` and `player["defenseWins"]` — confirm these are season totals
4. Fix any field path mismatches found. The function signature and return shape must not change —
   only the internal field mapping.
5. Add a module-level comment block at the top of `poller.py` documenting the verified field paths
   and which API version was tested against.

---

### Task 2 — Implement pool injection into the Discord bot

File: `discord_bot/bot.py`

Currently `create_bot()` returns a bot with no reference to the database pool. Every Cog command
needs the pool. Implement a clean injection pattern:

1. Change `create_bot()` signature to `create_bot(pool: asyncpg.Pool) -> commands.InteractionBot`
2. Store the pool on the bot instance: `bot.pool = pool`
3. Update every `setup(bot)` call in the Cog files to pass `bot.pool` to the Cog constructor:
   ```python
   def setup(bot: commands.InteractionBot) -> None:
       bot.add_cog(AccountCommands(bot, bot.pool))
   ```
4. Update `main.py` to pass the pool to `create_bot(pool)` — the pool already exists at that point.
5. Update every Cog's `__init__` to accept `pool: asyncpg.Pool` as a second argument
   and store it as `self.pool`.

This pattern must be consistent across all four Cog files. Developer A will follow
the same pattern for `BaseFinderCommands` — coordinate so you're not writing conflicting
`bot.py` changes. Developer A should copy whatever you land on.

---

### Task 3 — Wire the account linker Discord commands

File: `discord_bot/commands/account_commands.py`

Replace all three stubs.

**`/link <coc_tag> <token>`**

```
1. Call linker.link_account(self.pool, inter.author.id, coc_tag, token)
2. On ValueError (bad tag format): reply ephemeral "Invalid CoC tag — make sure it starts with #"
3. On success=False: reply ephemeral f"Linking failed: {result['error']}"
4. On success=True: reply ephemeral f"Linked! Welcome, {result['coc_name']}."
```

**`/unlink`**

```
1. Call linker.unlink_account(self.pool, inter.author.id)
2. If True: reply ephemeral "Your account has been unlinked."
3. If False: reply ephemeral "You don't have a linked account."
```

**`/whois <discord_user>`**

```
1. Call linker.get_linked_account(self.pool, discord_user.id)
2. If None: reply ephemeral f"{discord_user.display_name} has no linked CoC account."
3. If found: build a small embed:
   - Title: discord_user.display_name
   - Fields: "CoC tag", "In-game name", "Verified" (yes/no), "Linked since"
4. Reply ephemeral
```

---

### Task 4 — Wire the legend tracker Discord commands

File: `discord_bot/commands/legend_commands.py`

**`/legend`**

```
1. Call linker.get_linked_account(self.pool, inter.author.id)
   If None: reply ephemeral "You need to /link your account first."
2. Call poller.get_legend_stats(account["coc_tag"])
   If None: reply ephemeral "You are not currently in Legend League."
3. Call snapshots.compute_day_diff(self.pool, account["coc_tag"])
4. Build an embed:
   - Title: f"{account['coc_name']} — Legend Stats"
   - Fields:
     "Trophies": stats["trophies"]
     "Today's change": f"+{diff['trophy_change']}" or diff if None
     "Attacks used today": diff["attacks_used"] if diff else "N/A"
5. Reply (not ephemeral — let the clan see)
```

**`/legend_history <days>`**

```
1. Resolve the user's linked account (same as /legend step 1)
2. Call snapshots.get_recent_snapshots(self.pool, coc_tag, days)
3. If empty: reply ephemeral "No snapshot history yet."
4. Build an embed with one field per snapshot:
   "{date}: {trophies} trophies (+/-{change} from prior day)"
   Compute the per-day change inline from the list.
5. Cap days at 30 to prevent embed overflow. If user passes >30, clamp and note it.
```

**`/leaderboard`**

```
1. SELECT coc_tag, coc_name, trophies FROM users
   JOIN legend_snapshots ON users.coc_tag = legend_snapshots.coc_tag
   WHERE snapshot_date = CURRENT_DATE
   ORDER BY trophies DESC
   LIMIT 10
2. If no rows: reply ephemeral "No legend data for today yet."
3. Build an embed:
   - Title: "Legend Leaderboard — {today's date}"
   - Description: numbered list, one player per line
     "1. {coc_name} — {trophies} 🏆"
4. Reply not ephemeral
```

---

### Task 5 — Wire ping automator Discord commands and implement guild_settings

File: `discord_bot/commands/ping_commands.py`

This requires the `guild_settings` table added in the shared schema step.

**`/setpingchannel <channel>`**

```
1. INSERT INTO guild_settings (guild_id, ping_channel_id)
   VALUES ($1, $2)
   ON CONFLICT (guild_id) DO UPDATE SET ping_channel_id = EXCLUDED.ping_channel_id, updated_at = NOW()
2. Reply ephemeral f"Legend pings will be sent to {channel.mention}."
```

**`/togglepings`**

```
1. SELECT pings_enabled FROM guild_settings WHERE guild_id = $1
   If no row: INSERT with pings_enabled = FALSE (toggling from assumed True to False)
2. Flip the value: UPDATE guild_settings SET pings_enabled = NOT pings_enabled WHERE guild_id = $1
3. Reply ephemeral "Pings enabled." or "Pings disabled." based on new state
```

Also update `modules/ping_automator/scheduler.py`:

Implement `send_legend_ping(bot, discord_id, message)` properly:
```python
async def send_legend_ping(bot, discord_id: int, message: str) -> None:
    user = bot.get_user(discord_id) or await bot.fetch_user(discord_id)
    if user:
        try:
            await user.send(message)
        except disnake.Forbidden:
            logger.warning("Cannot DM discord_id=%s (DMs disabled)", discord_id)
```

Update `poll_legend_players()` in the scheduler to call `send_legend_ping` after saving snapshots
if the player's trophy change for the day is non-zero. Pass the bot instance into the scheduler
job at construction — update `create_scheduler(pool)` to `create_scheduler(pool, bot)` and store
`bot` so jobs can reference it. Update `main.py` accordingly: `create_scheduler(pool, bot)`.

---

### Task 6 — Expand account linker and legend tracker tests

Files: `tests/test_account_linker.py`, `tests/test_legend_tracker.py`

Add to `test_account_linker.py`:
- `test_link_account_coc_api_unavailable`: mock `_verify_token` to raise `aiohttp.ClientError`,
  assert result is `{"success": False, "error": "CoC API unavailable"}`
- `test_link_account_token_rejected`: mock `_verify_token` to return `{"status": "invalid"}`,
  assert result is `{"success": False, "error": "Token verification failed"}`
- `test_get_all_linked_accounts_empty`: mock fetch returns `[]`, assert returns `[]`
- `test_link_account_upserts_on_conflict`: mock `_verify_token` success + `_fetch_player_name`,
  assert `conn.execute` is called with the upsert query

Add to `test_legend_tracker.py`:
- `test_get_legend_stats_in_legend`: mock `get_player` to return a valid legend player response
  with `league.id == 29000022`, assert all five fields are present in the result
- `test_save_snapshot_new_row`: mock `conn.execute` returns `"INSERT 0 1"`, assert returns `True`
- `test_get_recent_snapshots_empty`: mock `conn.fetch` returns `[]`, assert returns `[]`
- `test_compute_day_diff_returns_correct_values`: mock both snapshots with known trophy values,
  assert `trophy_change` is computed correctly

---

## Shared rules for both developers

1. **Run `python -m pytest tests/ -v` before every commit.** All 9 existing tests must keep passing.
   New tests you add must also pass. Never commit with a failing test.

2. **Never put business logic in a Discord command file.** If you find yourself writing more than
   ~15 lines of non-formatting logic in a Cog method, extract it into the relevant module file.

3. **All new settings go in `config/settings.py` and `.env.example`.** If you need a new env var,
   add it to both files and append a row to the README env-var table.

4. **New database columns or tables go in `database/models.py` only.** Use `CREATE TABLE IF NOT EXISTS`
   and `CREATE INDEX IF NOT EXISTS` so the script stays idempotent. Re-run `python scripts/init_db.py`
   after any schema change.

5. **Log at INFO for significant events, DEBUG for per-frame/per-row noise.**
   No `print()` outside `scripts/`.

6. **Coordinate before touching `database/models.py`, `config/settings.py`, `main.py`,
   or `discord_bot/bot.py`** — these are shared files. Make changes on a short-lived branch
   and merge quickly so the other developer isn't blocked.
