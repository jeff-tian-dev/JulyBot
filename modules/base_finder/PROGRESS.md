# Base Finder — Progress Log

Tracks completed work for the base finder module specifically. Append new dated entries at the top; do not rewrite history. For the implementation roadmap see [BASEFINDER_PLAN.md](../../BASEFINDER_PLAN.md).

---

## 2026-05-19 — Phase 2: Pipeline state machine

**File:** [pipeline.py](pipeline.py)

Replaced the simple `in_loading` flag with an explicit 4-state machine:

- **SCANNING** — samples at `TARGET_SAMPLE_FPS` (default 1fps) looking for loading screens
- **SWEEPING** — frame-by-frame after detection to find the exact loading-screen end
- **WAITING** — counts down `POST_LOADING_DELAY_FRAMES` (default 30, ~1s at 30fps) for camera to settle
- **CAPTURING** — grabs `CAPTURE_FRAMES_COUNT` (default 10) candidates spaced `CAPTURE_FRAME_SPACING` (default 6) raw frames apart, then returns to SCANNING

All four constants are module-level tunables. `POST_LOADING_DELAY_FRAMES` is the main knob to tune — trades "camera settled / transition done" against "player hasn't moved the screen yet."

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
