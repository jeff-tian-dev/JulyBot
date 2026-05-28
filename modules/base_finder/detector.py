"""Loading-screen detection for CoC attack VODs.

Detection strategy:
The CoC attack loading screen is visually simple — a single dragon icon on a
uniform white-cloud or dark-cloud background. Gameplay frames are visually
complex — hundreds of buildings, UI elements, text, troop deployment bar.

This visual complexity gap is most cleanly captured by EDGE DENSITY (Canny
edge detection). To make measurements resolution-invariant, every frame is
first resized to REFERENCE_SIZE before signals are computed.

Tuned against TH18 samples covering both bright and dark cloud variants.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# --- Tuned thresholds (from sample analysis) --------------------------------
REFERENCE_SIZE = (1280, 720)        # All frames resized to this before measuring
EDGE_DENSITY_THRESHOLD = 0.03       # Loading: ~0.005, Gameplay: 0.10+
PIXEL_STD_THRESHOLD = 38.0          # Loading: 14-32, Gameplay: 45+
BASE_VIEW_MIN_EDGE_DENSITY = 0.08   # Captured frames below this are likely menus
                                    # or popups, not actual base views
CANNY_LOW_THRESHOLD = 50
CANNY_HIGH_THRESHOLD = 150


def _crop_black_bars(frame: np.ndarray, threshold: int = 15) -> np.ndarray:
    """Detect and crop black pillarbox/letterbox bars.

    Scans rows/columns for ones that are >95% near-black pixels and crops them.
    This avoids fake high-contrast edges between black bars and content.
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape

    # Find non-black columns (left-right bars)
    col_means = gray.mean(axis=0)
    non_black_cols = np.where(col_means > threshold)[0]
    if len(non_black_cols) == 0:
        return frame
    x0, x1 = non_black_cols[0], non_black_cols[-1] + 1

    # Find non-black rows (top-bottom bars)
    row_means = gray.mean(axis=1)
    non_black_rows = np.where(row_means > threshold)[0]
    if len(non_black_rows) == 0:
        return frame
    y0, y1 = non_black_rows[0], non_black_rows[-1] + 1

    return frame[y0:y1, x0:x1]


def _normalize(frame: np.ndarray) -> np.ndarray:
    """Crop black bars and resize to reference size for invariant measurements."""
    cropped = _crop_black_bars(frame)
    if cropped.size == 0:
        return frame
    if cropped.shape[1] == REFERENCE_SIZE[0] and cropped.shape[0] == REFERENCE_SIZE[1]:
        return cropped
    return cv2.resize(cropped, REFERENCE_SIZE, interpolation=cv2.INTER_AREA)


def _edge_density(frame: np.ndarray) -> float:
    """Fraction of pixels classified as edges by Canny detection.

    Loading screens: ~0.005 (smooth backgrounds, minimal detail)
    Gameplay: 0.10+ (buildings, UI, text — all produce edges)
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, CANNY_LOW_THRESHOLD, CANNY_HIGH_THRESHOLD)
    return float(edges.mean()) / 255.0


def _pixel_std(frame: np.ndarray) -> float:
    """Standard deviation of grayscale pixel values.

    Loading screens: ~15-31 (uniform background dominates)
    Gameplay: 45+ (varied colors and brightness everywhere)
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return float(gray.std())


def is_loading_screen(frame: np.ndarray) -> bool:
    """Detect whether a frame is a CoC attack loading screen.

    Frame is resized to REFERENCE_SIZE first so thresholds work across
    any input resolution. Edge density is the primary signal; pixel std
    is a confirmation check.
    """
    if frame is None or frame.size == 0:
        return False

    normalized = _normalize(frame)

    edge_d = _edge_density(normalized)
    if edge_d >= EDGE_DENSITY_THRESHOLD:
        return False

    px_std = _pixel_std(normalized)
    if px_std >= PIXEL_STD_THRESHOLD:
        return False

    return True


def looks_like_base(frame: np.ndarray) -> bool:
    """Final gate before caching: confirm a frame contains an actual base view.

    `is_loading_screen()` says "this image is too simple" — which is true of
    loading screens, but also of menus, popups (troop donation, clan castle),
    account switchers, and other low-detail UI states. After SWEEPING ends on
    such a state, the WAIT/CAPTURE phase would otherwise cache a menu instead
    of a base.

    This function applies the inverse signal: gameplay frames have high edge
    density (~0.10-0.17). Anything below 0.08 is likely UI, not a base.
    """
    if frame is None or frame.size == 0:
        return False
    normalized = _normalize(frame)
    return _edge_density(normalized) >= BASE_VIEW_MIN_EDGE_DENSITY


def find_loading_screen_end(frames: list[np.ndarray]) -> int | None:
    """Return the index of the first non-loading-screen frame after a loading screen."""
    seen_loading = False
    for idx, frame in enumerate(frames):
        if is_loading_screen(frame):
            seen_loading = True
        elif seen_loading:
            return idx
    return None
