"""Loading-screen detection for CoC attack VODs.

# NOTE FOR CV ENGINEER:
# The is_loading_screen() detection thresholds are placeholders.
# Before deploying, test against 10-20 real CoC YouTuber VODs and tune:
#   - BRIGHTNESS_THRESHOLD
#   - PROGRESS_BAR_Y_RANGE
#   - COLOR_SIGNATURE (HSV ranges)
# The structural approach (brightness + progress bar detection) is sound.
"""
from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# --- Tunable thresholds (placeholders) -------------------------------------
BRIGHTNESS_THRESHOLD = 70           # Mean V-channel value below this = "dark overlay"
PROGRESS_BAR_Y_RANGE = (0.78, 0.88) # Vertical band (fraction of frame height) where the loading bar sits
PROGRESS_BAR_MIN_BRIGHT_RATIO = 0.15  # Fraction of pixels in the band that must be "bright" (the bar itself)
PROGRESS_BAR_BRIGHTNESS = 180         # V threshold defining a "bright" pixel within the band

# CoC loading screen has a warm/orange-brown palette; tune empirically.
COLOR_SIGNATURE_HSV_LOWER = np.array([5, 40, 30], dtype=np.uint8)
COLOR_SIGNATURE_HSV_UPPER = np.array([30, 220, 200], dtype=np.uint8)
COLOR_SIGNATURE_MIN_RATIO = 0.25  # At least this fraction of pixels in signature range


def _frame_brightness(frame: np.ndarray) -> float:
    """Mean V-channel brightness of the frame."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    return float(hsv[:, :, 2].mean())


def _has_progress_bar(frame: np.ndarray) -> bool:
    """Check the lower band of the frame for a horizontal bright streak."""
    h = frame.shape[0]
    y0 = int(h * PROGRESS_BAR_Y_RANGE[0])
    y1 = int(h * PROGRESS_BAR_Y_RANGE[1])
    band = frame[y0:y1]
    if band.size == 0:
        return False
    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    bright = hsv[:, :, 2] >= PROGRESS_BAR_BRIGHTNESS
    return bright.mean() >= PROGRESS_BAR_MIN_BRIGHT_RATIO


def _matches_color_signature(frame: np.ndarray) -> bool:
    """Check whether the frame's HSV histogram matches the loading screen palette."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, COLOR_SIGNATURE_HSV_LOWER, COLOR_SIGNATURE_HSV_UPPER)
    ratio = float(mask.mean()) / 255.0
    return ratio >= COLOR_SIGNATURE_MIN_RATIO


def is_loading_screen(frame: np.ndarray) -> bool:
    """Detect whether a frame is a CoC attack loading screen."""
    if frame is None or frame.size == 0:
        return False
    if _frame_brightness(frame) >= BRIGHTNESS_THRESHOLD:
        return False
    if not _has_progress_bar(frame):
        return False
    if not _matches_color_signature(frame):
        return False
    return True


def find_loading_screen_end(frames: list[np.ndarray]) -> int | None:
    """Return the index of the first non-loading-screen frame after a loading screen."""
    seen_loading = False
    for idx, frame in enumerate(frames):
        if is_loading_screen(frame):
            seen_loading = True
        elif seen_loading:
            return idx
    return None
