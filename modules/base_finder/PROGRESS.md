# Base Finder — Progress Log

Tracks completed work for the base finder module specifically. Append new dated entries at the top; do not rewrite history. For the implementation roadmap see [BASEFINDER_PLAN.md](../../BASEFINDER_PLAN.md).

---

## 2026-05-19 — Phase 2: Extraction & storage validated

**Files:** [normalizer.py](normalizer.py), [pipeline.py](pipeline.py), [scripts/run_local_pipeline.py](../../scripts/run_local_pipeline.py)

**Pipeline state machine** (replaces simple `in_loading` flag):

- **SCANNING** — samples at `TARGET_SAMPLE_FPS` (1fps) looking for loading screens
- **SWEEPING** — frame-by-frame after detection to find exact loading-screen end
- **WAITING** — counts down `POST_LOADING_DELAY_FRAMES` (default 30, ~1s at 30fps) for camera to settle
- **CAPTURING** — grabs `CAPTURE_FRAMES_COUNT` (default 10) candidates spaced `CAPTURE_FRAME_SPACING` (default 6) raw frames apart, then returns to SCANNING

`POST_LOADING_DELAY_FRAMES` is the main tuning knob — trades "camera settled / transition animation done" against "player hasn't started moving the screen yet."

**Normalizer crop tuning** (validated against MLAuybQ7qKs):

- `TOP_UI_FRACTION = 0.0` — top overlays (Available Loot panel, resource counters, Legend rank, Battle-ends-in timer) are semi-transparent; base is still visible through/around them. Kept for maximum base coverage.
- `BOTTOM_UI_FRACTION = 0.33` — troop bar + Surrender/Boost buttons are fully opaque and completely block the base. Must be cropped.
- `CANONICAL_SIZE = (1080, 490)` — preserves the actual game aspect ratio (~2.20:1 after pillarbox + bottom crop). YouTube delivers 16:9 with side black bars; the underlying game content is ~1.47:1, so the canonical size is derived from the game's natural proportions, not the YouTube frame.
- Black-bar (pillarbox/letterbox) cropping added — imports `_crop_black_bars` from detector.py for a single source of truth.

**Acknowledged trade-offs:**

- Side UI panels (Available Loot on left, resource counters on right) are still visible in captured frames. A horizontal-band crop can't reach them. Acceptable because (a) the UI is in identical positions across all captures so it acts like consistent background, (b) pHash is largely tolerant of consistent backgrounds. Will revisit if Phase 3 matching is poor.

**Local validation runner:** [scripts/run_local_pipeline.py](../../scripts/run_local_pipeline.py) — runs the full pipeline without any DB, saves PNGs to `data/bases/` + metadata.json. Used for Phase 2 visual inspection.

Tests: 9/9 still passing.

---

## 2026-05-19 — Phase 1: Detector tuning complete

**Files:** [detector.py](detector.py), [scripts/scan_video.py](../../scripts/scan_video.py), [scripts/compare_signals.py](../../scripts/compare_signals.py)

Pivoted the detector away from the original brightness + progress bar + color-signature approach. The original plan failed because:

- Orange detection produced false positives during zoomed-in gameplay (player examining building placements)
- Brightness alone failed: loading screens have TWO variants (bright white-cloud and dark gray-cloud) that span gameplay's brightness range
- No visible progress bar on the TH18 loading screen in captured frames

**New detector strategy:** edge density (Canny) + pixel std deviation. Loading screens are visually simple (one icon, uniform background); gameplay is visually busy (hundreds of building tiles, UI, text). Clean 17x separation across both bright and dark variants.

**Pre-processing added:**

- **Resize to 1280×720 reference size** before measuring — makes thresholds resolution-invariant
- **Black bar crop** — pillarbox/letterbox edges were creating fake high-contrast boundaries that inflated edge density on YouTube streams

**Validation:** Scanned 4 TH18 VODs across different creators:

- `MLAuybQ7qKs` (30:34): 13 attack transitions, 1.6–2.1s durations, clean
- `mYkdiiJcvEs` (18:12): 10 attacks, 1.2–2.4s durations, clean
- `OGY4Ut0G-II` (21:04): 8 attacks, 0.3–1.9s durations (creator edits short — detector still catches every attack)
- `vrJJDbiz34c` (22:43): 18 detections (real attacks + non-attack loading screens such as loading into own base, account switches) — detector working as designed; pHash dedup will handle the pollution

Zero false positives observed (every detection is a real CoC loading screen of some kind). Compute time ~10–15 minutes per 20–30 min VOD (≈0.5x real-time, dominated by stream I/O not detection).

**Tunable constants (in detector.py):**

```python
REFERENCE_SIZE = (1280, 720)
EDGE_DENSITY_THRESHOLD = 0.03      # Loading: ~0.005, Gameplay: 0.10+
PIXEL_STD_THRESHOLD = 38.0         # Loading: 14-32, Gameplay: 45+
CANNY_LOW_THRESHOLD = 50
CANNY_HIGH_THRESHOLD = 150
```

---

## 2026-04-28 — Initial skeleton

Module created with placeholder thresholds (marked `NOTE FOR CV ENGINEER`). All interfaces and data flow in place; CV logic stubbed for tuning. 9 base finder + module tests passing.
