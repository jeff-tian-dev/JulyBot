# Base Finder — Implementation Plan

A pipeline that watches Clash of Clans YouTuber VODs, extracts clean screenshots of bases
at the moment an attack begins, stores them in a searchable cache, and lets Discord users
upload a screenshot of a base they've encountered to find it in the cache — along with a
link to the source video showing how it was attacked.

Phases are sequential — each is a hard dependency of the next, except Phase 4 which is
conditional on Phase 3 results.

---

## Current status (2026-05-28)

**Scope locked to TH18.** All tuning and validation is against TH18 YouTube VODs.

| Phase | Status |
|-------|--------|
| 1 — Detection | **COMPLETE** |
| 2 — Extraction & storage | **COMPLETE** (pending menu threshold tuning — see below) |
| 3 — Matching baseline (pHash) | **IN PROGRESS** — query set ready, benchmark script not yet built |
| 4 — Object detection (YOLO) | Not started — conditional on Phase 3 failing |
| 5 — Discord UX | Not started |

**Validation tooling (no DB needed):**
- [scripts/scan_video.py](scripts/scan_video.py) — detection-only, prints timestamps
- [scripts/run_local_pipeline.py](scripts/run_local_pipeline.py) — full pipeline → PNGs + metadata.json; also saves query candidates to `data/query_candidates/`
- [scripts/compare_crop.py](scripts/compare_crop.py) — extract one frame and visualize crop boundaries
- [scripts/compare_signals.py](scripts/compare_signals.py) — compare signal values across sample categories
- [scripts/debug_video.py](scripts/debug_video.py) — sample frames at specific timestamps
- [scripts/test_detector.py](scripts/test_detector.py) — regression test against `data/samples/`

**Outstanding before Phase 3 benchmark is meaningful:**
1. Expand menu sample set (`data/samples/menus/`) and re-tune `BASE_VIEW_MIN_EDGE_DENSITY`
   (currently 0.08, set from gameplay samples only — army composition screen still sneaks through).
   Run `scripts/compare_signals.py` on the 3-category set to find a clean threshold.

---

## Phase 1 — Detection ✓

**COMPLETE.** See [modules/base_finder/PROGRESS.md](modules/base_finder/PROGRESS.md) for full notes.

Strategy: edge density (Canny) + pixel std, with black-bar crop and resize to `REFERENCE_SIZE`
before measuring. Validated against 4 TH18 VODs; zero false positives.

---

## Phase 2 — Extraction and Storage ✓

**COMPLETE.** See [modules/base_finder/PROGRESS.md](modules/base_finder/PROGRESS.md) for full notes.

4-state pipeline (SCANNING → SWEEPING → WAITING → CAPTURING). Normalizer tuned for TH18:
`BOTTOM_UI_FRACTION = 0.33`, `CANONICAL_SIZE = (1080, 490)`. `looks_like_base()` gate added
to reject menus at capture time, threshold pending tuning (see outstanding items above).

`run_local_pipeline.py` now saves frame 0 per attack to `data/bases/` (cache) and frames 1–9
to `data/query_candidates/` (Phase 3 test inputs), each with `expected_phash` linking back
to its cache entry.

---

## Phase 3 — Matching Baseline (pHash)

**Goal:** Determine whether pHash is accurate enough for `/findbase`, and produce a benchmark
to compare against Phase 4 if needed.

**Query set status:** 26 cache entries + 234 query candidates collected from 3 clean VODs.
Feed more videos via `run_local_pipeline.py` to grow the set before benchmarking.

**Work:**

1. **Build `scripts/benchmark_matcher.py`.** For each entry in `data/query_candidates/metadata.json`:
   - Load the query image
   - Run `find_matching_bases()` against the local `data/bases/metadata.json` (no DB)
   - Check whether the `expected_phash` appears in the top-1 / top-5 results
   - Record top-1 accuracy, top-5 accuracy, false positive rate, per-query latency

2. **Tune `phash_threshold`** in `matcher.py`. Run the benchmark at threshold 5, 10, 15, 20
   and pick the value that maximises top-5 accuracy without unacceptable false positives.

3. **Make the go/no-go decision on Phase 4.** Top-5 ≥ 80% and FP rate < 15% → skip to Phase 5.
   Below that → proceed to Phase 4 with the failure cases documented.

**Notes:**
- Query candidates are frames from the same attack as their cache entry — same base, slightly
  different timestamp and minor camera drift. This is a controlled lower bound; real user
  screenshots will be harder.
- 750-entry linear pHash scan is fast (< 100ms). pgvector embedding column already exists in
  `base_cache` if Phase 4 is needed.

---

## Phase 4 — Object Detection (conditional)

**Only enter if Phase 3 fails.** Skip to Phase 5 if pHash is good enough.

**Goal:** Replace pHash with a layout-based fingerprint from detected building positions,
making matching zoom- and scenery-invariant.

**Work:**

1. **Check Roboflow Hub first** — search for CoC building detectors trained at TH18. Takes
   20 minutes and could save weeks. If a usable model exists, load via Ultralytics YOLOv8 and
   skip to step 3.

2. **Build a labeled dataset** (only if step 1 finds nothing) — 500–1000 base screenshots,
   labeled with Roboflow's annotation tool, train YOLOv8n/s to mAP ≥ 0.7.

3. **Define the layout fingerprint** — normalize building positions to 0.0–1.0 relative to
   the base bounding box, encode as a fixed-length vector, store in the `embedding vector(512)`
   column in `base_cache`.

4. **Replace the pHash query** in `matcher.py` with pgvector nearest-neighbor:
   `ORDER BY embedding <-> $1 LIMIT $2`. Keep pHash as fallback.

5. **Re-run the Phase 3 benchmark** and compare.

**Notes:**
- Don't add `ultralytics` to `requirements.txt` until this phase is entered.
- Run YOLO only on frames already selected by the loading-screen pipeline, not every sampled frame.

---

## Phase 5 — Discord UX

**Goal:** Wire module logic into the Discord Cog stubs.

**Work:**

1. **`/findbase <image>`** — download attachment, call `find_matching_bases(pool, image, top_n=5)`,
   format embed with similarity score + source channel + TH level + source video link.
   Return a clear "no match" message rather than low-confidence results.

2. **`/addchannel <youtube_url>`** — extract channel ID via yt-dlp, insert into `watched_channels`,
   confirm with next scheduled ingest time.

3. **`/cachestats`** — row count + breakdown by channel, oldest/newest `captured_at`,
   number of watched channels.

4. Handle errors gracefully — ephemeral user-facing messages, no raw tracebacks.

**Exit condition:** A clan member uploads a phone screenshot, runs `/findbase`, and gets back
a YouTube link they can click to watch the base being attacked.

---

## Open questions

- Does a usable CoC building detector exist on Roboflow Hub? (check if Phase 4 is entered)
- Is pHash matching good enough? (answer in Phase 3)
- Should base images live in local or object storage (S3/R2)? Local is fine for a single-server
  bot; revisit if running multiple instances.

---

## Future work (deferred)

### Stream-overlay content (live stream recordings)

Tested against `5w0VNobseZA` (Bilibili-style, 640×360, persistent chat panel on the right).
Zero detections — edge density was 0.13–0.14 everywhere because overlays are always in frame.

Fix requires a **game-region locator**: find the game window bounding box within the full stream
frame, crop to it, then run `is_loading_screen()` on that crop. When revisiting, start with
contour detection to find the largest dark-bordered rectangle in the frame.
