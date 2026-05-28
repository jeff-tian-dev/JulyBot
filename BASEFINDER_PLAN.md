# Base Finder — Implementation Plan

A pipeline that watches Clash of Clans YouTuber VODs, extracts clean screenshots of bases
at the moment an attack begins, stores them in a searchable cache, and lets Discord users
upload a screenshot of a base they've encountered to find it in the cache — along with a
link to the source video showing how it was attacked.

The skeleton in `modules/base_finder/` has all interfaces and data flow in place. The CV
work is stubbed. This document describes what to build, in what order, and the decisions
that need to be made at each stage.

Phases are sequential — each is a hard dependency of the next, except Phase 4 which is
conditional on Phase 3 results.

---

## Current status (2026-05-19)

**Scope locked to TH18.** All tuning and validation is against TH18 content from YouTube VODs.

**Phase 1 — Detection: COMPLETE.**
- Detector pivoted from the original brightness + progress bar + color-signature approach to
  edge density + pixel std (much cleaner signal separation).
- Resolution-invariant: every frame resized to 1280×720 and black bars (pillarbox/letterbox)
  cropped before measuring.
- Validated against 4 TH18 VODs across multiple creators; all real loading screens detected.

**Phase 2 — Extraction & storage: MOSTLY COMPLETE.**
- 4-state pipeline machine (SCANNING → SWEEPING → WAITING → CAPTURING) with `POST_LOADING_DELAY_FRAMES`
  as the main tunable.
- Normalizer tuned for TH18: black-bar crop, `TOP_UI_FRACTION = 0.0` (top overlays semi-transparent,
  preserved), `BOTTOM_UI_FRACTION = 0.33` (troop bar fully cropped),
  `CANONICAL_SIZE = (1080, 490)` (preserves actual game aspect ratio).
- `looks_like_base()` filter added to detector.py as final gate before caching. Catches
  *some* menu false positives (main menu, donation popups) but `BASE_VIEW_MIN_EDGE_DENSITY = 0.08`
  was set from gameplay samples only — it's a guess for the menu side and still lets through
  busier-looking menus (e.g. army composition screen).
- Validation done via [scripts/run_local_pipeline.py](scripts/run_local_pipeline.py) — no DB
  required; saves PNGs + metadata.json to `data/bases/`.
- See [modules/base_finder/PROGRESS.md](modules/base_finder/PROGRESS.md) for detailed change log.

**Outstanding before Phase 3:**

1. **Expand sample set.** Currently 5 loading + 6 gameplay screenshots from 2 creators. Bump to
   15–20 per category across 5+ creators.
2. **Create `data/samples/menus/` category** with 15+ screenshots of false-positive UI states
   (army composition, donation popup, account switcher, clan castle, main menu, settings).
3. **Re-tune `BASE_VIEW_MIN_EDGE_DENSITY`** by running [scripts/compare_signals.py](scripts/compare_signals.py)
   on the 3-category sample set and picking a value above all menus but below gameplay.

**Validation tooling (no DB needed):**
- [scripts/scan_video.py](scripts/scan_video.py) — detection-only on a YouTube URL, prints timestamps
- [scripts/run_local_pipeline.py](scripts/run_local_pipeline.py) — full pipeline → PNGs in data/bases/
- [scripts/compare_crop.py](scripts/compare_crop.py) — extract one frame and visualize crop boundaries
- [scripts/compare_signals.py](scripts/compare_signals.py) — compare signal values across sample categories
- [scripts/debug_video.py](scripts/debug_video.py) — sample frames at specific timestamps
- [scripts/test_detector.py](scripts/test_detector.py) — regression test against `data/samples/`

---

## Phase 1 — Detection

**Goal:** Make the loading-screen trigger reliable before anything else is built.

After a CoC attack loading screen clears, the game guarantees a fully zoomed-out,
unobstructed view of the base before the attacker acts. That window is the capture
opportunity. Detecting the *end* of the loading screen is the trigger for everything
downstream. Without it, the pipeline captures garbage.

**Work:**

1. Gather 10–20 real CoC YouTuber VOD clips covering a variety of channels, resolutions
   (1080p, 1440p, 4K), and town hall levels. The more varied the better — popular channels
   like Judo Sloth, Klaus, and Chief Pat are good starting points.
2. Run the existing `is_loading_screen()` stub against these clips frame-by-frame and log
   the raw values for each detection signal:
   - Mean V-channel brightness of the whole frame
   - Fraction of pixels in the bottom band that exceed the brightness threshold
   - Fraction of pixels matching the color signature HSV range
3. Inspect the logged values visually. Identify the natural clusters separating loading
   screen frames from gameplay frames.
4. Set the named constants in `detector.py` (`BRIGHTNESS_THRESHOLD`, `PROGRESS_BAR_Y_RANGE`,
   `PROGRESS_BAR_MIN_BRIGHT_RATIO`, `COLOR_SIGNATURE_HSV_LOWER/UPPER`, `COLOR_SIGNATURE_MIN_RATIO`)
   to values that cleanly separate the two clusters with margin.
5. Validate on a held-out set of clips. Check for both failure modes:
   - False positives: gameplay frames flagged as loading screens → cuts off capture early
   - Missed transitions: loading screen not detected → correct base frame never captured

**Exit condition:** `find_loading_screen_end()` correctly identifies the transition frame on
every clip in the test set. Zero false positives on the held-out set.

**Notes:**
- Don't tune thresholds on the same clips you gathered data from. Keep at least 5 clips
  as a held-out validation set.
- The structural approach in the stub (brightness + progress bar + color) is sound. Resist
  the urge to add more signals before testing the three-signal approach first.
- If the loading screen varies significantly across TH levels (it may), you may need
  per-TH-level thresholds or a wider range that covers all levels. Check this early.

**Follow-up work (carry into Phase 1.5):**

1. **Expand `data/samples/` coverage.** Current set is ~5 loading + 6 gameplay screenshots
   from 2 creators. Bump to 15-20 per category across 5+ creators to harden thresholds.

2. **Add `data/samples/menus/` category.** The current `is_loading_screen()` detects
   "visually simple frames" — which is true of loading screens but ALSO of:
   - Troop donation popups
   - Account switcher
   - Main menu (Battle / Practice / Ranked)
   - Clan castle / clan chat overlays
   - Settings / shop / season pass panels

   These all get falsely flagged. The pipeline now uses a `looks_like_base()` filter at
   capture time (edge density >= 0.08) to reject menus from being cached, but the
   threshold (`BASE_VIEW_MIN_EDGE_DENSITY`) is set from gameplay sample data only.
   Once a `menus/` sample set exists, re-run `scripts/compare_signals.py` to verify
   menus sit cleanly below the threshold.

3. **Add new transition-type detectors when needed** (post-attack screens, replay end
   cards, etc.) as separate detection functions in `detector.py` — same architecture,
   each gets its own threshold sample set.

---

## Phase 2 — Extraction and Storage

**Goal:** Run the full pipeline end-to-end on a real watched channel and confirm the cache
fills with clean, correctly-cropped base images.

**Work:**

1. **Validate `normalizer.py` crop percentages** against actual screenshots at 1080p, 1440p,
   and any other resolutions your target channels stream at. Open test frames in an image viewer,
   measure the pixel heights of the top bar (gold/elixir/XP) and bottom troop bar, and compute
   the actual fractions. Update `TOP_UI_FRACTION` and `BOTTOM_UI_FRACTION` if needed.

2. **Add a watched channel to the DB** (`scripts/init_db.py` seeds from `YOUTUBE_CHANNEL_IDS`
   in `.env`, or insert directly).

3. **Run `pipeline.run_pipeline(pool)`** and inspect the output:
   - Do extracted frames look like clean zoomed-out bases? No UI bars, no loading screen overlay?
   - Are the stored images 1080×1080 as expected?
   - Are duplicates within the same video correctly skipped by `is_duplicate()`?
   - Does `enforce_cache_limit()` evict correctly when the cache fills past 750?

4. **Check pHash distribution** across stored images. A healthy cache has a spread of hash
   values — if many hashes are near-identical it likely means the same base is being stored
   multiple times from adjacent frames. Tighten `FRAMES_AFTER_LOADING` or the duplicate
   threshold if needed.

5. **Stress test on 3–5 channels** with different streaming styles (short clips vs. long
   multi-attack VODs). Confirm the pipeline handles videos where no loading screen appears
   (returns 0 new bases cleanly) and where multiple attacks happen in sequence.

**Exit condition:** The DB fills with visually clean, correctly-cropped base images across
multiple videos and channels. No UI artifacts in stored images. No duplicate bases slipping
through. Pipeline summary stats (`processed`, `new_bases`, `skipped_duplicates`, `errors`)
look sensible.

**Notes:**
- yt-dlp stream URLs expire. If you get OpenCV read failures on long videos, this is likely
  why — re-resolve the stream URL rather than caching it across sessions.
- The `asyncio.to_thread()` call in `process_video()` wraps the synchronous OpenCV frame
  loop. Keep it that way — OpenCV's `VideoCapture` is not async-safe.
- `data/bases/` is gitignored. Use a local path or object storage; don't commit images.

---

## Phase 3 — Matching Baseline (pHash)

**Goal:** Determine whether perceptual hashing is accurate enough for the `/findbase` use case,
and produce a benchmark number to compare against Phase 4 if needed.

**Why pHash might be good enough here:** The pipeline captures canonical fully-zoomed-out frames.
If the user's query image is also a zoomed-out screenshot (likely for legend/war base searches),
both images share the same perspective and scale. pHash's main weakness — zoom and crop variance —
is partially mitigated by the controlled capture condition.

**Work:**

1. **Build a test set** of 30–50 query images. For each, you need a ground-truth match (or
   confirmed non-match) in the cache. Collect these by having real users take screenshots of
   bases you know are in the cache, at their native device resolutions and zoom levels.

2. **Run `find_matching_bases()`** on each query image and record:
   - Top-1 accuracy: was the correct base the first result?
   - Top-5 accuracy: was it in the top 5?
   - False positive rate: did non-matching bases appear in results?
   - Latency: how long does a full cache scan take at 750 entries?

3. **Tune `phash_threshold`** in `matcher.py`. Lower = stricter (fewer false positives,
   more misses). Higher = looser (more matches, more false positives). Find the threshold
   that maximises top-5 accuracy without an unacceptable false positive rate.

4. **Identify failure cases.** Common pHash failure modes:
   - Different device aspect ratios cause the base to appear at different relative sizes
   - Scenery/background trees shift between captures
   - Partial UI obscuring the base in user screenshots

5. **Make the go/no-go decision on Phase 4.** If top-5 accuracy is above ~80% on the
   test set and false positives are low, pHash is likely good enough — skip to Phase 5.
   If accuracy is poor, proceed to Phase 4.

**Exit condition (pass):** Top-5 accuracy ≥ 80% on the test set with false positive rate < 15%.
Move to Phase 5.

**Exit condition (fail):** Accuracy clearly insufficient. Document the failure cases and move
to Phase 4 with that data to guide what the object detector needs to be invariant to.

**Notes:**
- 750-entry linear scan with pHash comparison is fast (< 100ms). If you move to pgvector
  embeddings in Phase 4, that column is already in `base_cache` waiting.
- Don't invest in query image preprocessing (cropping, denoising) yet. Test raw user
  screenshots first. If preprocessing helps, add it; if pHash is fundamentally inadequate,
  preprocessing won't save it.

---

## Phase 4 — Object Detection (conditional)

**Only enter this phase if Phase 3 fails.** If pHash accuracy was acceptable, skip to Phase 5.

**Goal:** Replace or augment pHash with a layout-based fingerprint derived from detected
building positions, making matching zoom-invariant and scenery-invariant.

**Approach:** Detect individual CoC buildings in each image, represent the base as a
normalized layout (building type → relative position), and compare layouts structurally
rather than pixel-by-pixel.

**Work:**

1. **Check Roboflow Hub first** (roboflow.com/universe). Search for CoC building detectors.
   This step takes 20 minutes and could save weeks. Specifically look for:
   - Models trained on CoC building sprites at the TH levels you care about
   - Models with high mAP on held-out CoC footage
   - Check how recently they were trained (game updates change building appearances)

   If a usable model exists: load it via the Ultralytics YOLOv8 API and move to step 3.
   If nothing usable exists: go to step 2.

2. **Build a labeled dataset** (only if step 1 finds nothing).
   - Capture 500–1000 base screenshots across your target TH levels
   - Label building bounding boxes and types using Roboflow's annotation tool
   - Train YOLOv8n or YOLOv8s (the smaller variants — inference speed matters more than
     marginal accuracy gains at this scale)
   - Target mAP ≥ 0.7 on a held-out validation set before using for fingerprinting

3. **Define the layout fingerprint.** For each detected base:
   - Normalize building positions relative to the base bounding box (0.0–1.0 coordinates)
   - Encode as a fixed-length vector: `[building_type_id, norm_x, norm_y]` for each
     detected building, padded/sorted consistently
   - Store in the `embedding vector(512)` column in `base_cache` (adjust dimension if needed)

4. **Replace the pHash query** in `matcher.py` with a pgvector nearest-neighbor query:
   ```sql
   ORDER BY embedding <-> $1 LIMIT $2
   ```
   Keep pHash as a secondary filter or fallback for images where building detection fails.

5. **Re-run the Phase 3 benchmark** with the new matcher. Compare against the pHash
   baseline numbers.

**Exit condition:** Top-5 accuracy clearly exceeds the Phase 3 pHash baseline on the same
test set. The delta justifies the added complexity.

**Notes:**
- Buildings look different at different TH levels. A detector trained only on TH16 will
  miss or misclassify buildings in TH12 bases. Be explicit about which TH levels are in
  scope for the initial version.
- YOLOv8 inference on CPU is fast enough for the ingestion pipeline (not real-time) but
  may be too slow for per-frame scanning during video processing. If so, only run detection
  on the frames already selected by the loading-screen pipeline, not every sampled frame.
- Don't add `ultralytics` to `requirements.txt` until this phase is entered. It's a
  heavy dependency.

---

## Phase 5 — Discord UX

**Goal:** Wire the working module logic into the Discord Cog stubs so users can actually
use the feature.

**Work:**

1. **`/findbase <image>`** — the core command.
   - Download the attached image to a temp file, load it with OpenCV
   - Call `find_matching_bases(pool, image, top_n=5)`
   - Format a response embed: for each match, show similarity score, source channel,
     town hall level (if known), and a link to the source video (`source_url`)
   - If no matches above threshold, say so clearly rather than returning low-confidence results
   - Delete the temp file after processing

2. **`/addchannel <youtube_url>`** — add a channel to the watched list.
   - Extract the channel ID from the URL (yt-dlp can do this)
   - Insert into `watched_channels`
   - Respond with confirmation and when the next scheduled ingest will run

3. **`/cachestats`** — show cache health.
   - Query count of rows in `base_cache`, breakdown by `source_channel`
   - Show oldest and newest `captured_at` timestamps
   - Show number of `watched_channels`

4. **Error handling.** Commands should catch module-level failures gracefully and respond
   with a user-friendly ephemeral message rather than silently failing or surfacing a
   Python traceback.

**Exit condition:** A clan member can attach a phone screenshot of a base they encountered,
run `/findbase`, and receive a result with a YouTube link they can actually click to watch
the base being attacked.

---

## Open questions (carry forward and answer as phases complete)

- Does a usable CoC building detector already exist on Roboflow Hub?
- What TH levels do the target YouTube channels primarily cover?
- Is pHash matching good enough given the canonical zoomed-out capture? (answer in Phase 3)
- What's realistic query volume? At 20–30 users, 750-entry linear scan is trivially fast.
- Should base images live in local storage or object storage (S3/R2)? Local is fine for a
  single-server bot; add object storage if you ever run multiple instances.
