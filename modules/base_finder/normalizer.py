"""Normalize raw frames into clean, comparable base screenshots.

Pipeline: crop black bars (pillarbox/letterbox) -> crop UI bands
(top resource bar + bottom troop/spell bar + action buttons) -> resize
to canonical square.

UI fractions tuned against TH18 YouTube VODs at 720p with the
"available loot / Battle ends in / Surrender + Boost + troop bar"
gameplay layout.
"""
from __future__ import annotations

import logging
import os
import uuid

import cv2
import imagehash
import numpy as np
from PIL import Image

from modules.base_finder.detector import _crop_black_bars

logger = logging.getLogger(__name__)


# --- Tunable parameters ----------------------------------------------------
TOP_UI_FRACTION = 0.0       # Top overlays (Available Loot, resource counters, Legend rank,
                            # Battle-ends-in timer) are semi-transparent — base is still
                            # visible through/around them. Keep the whole top for base coverage.
BOTTOM_UI_FRACTION = 0.33   # Troop/spell deployment bar + Surrender/Boost buttons.
                            # Fully opaque, completely blocks the base — must be cropped.
# Canonical size preserves the game's actual aspect ratio post-crops.
# YouTube streams are 16:9 but include black pillarbox bars — the game
# content underneath is roughly 1.47:1 (~3:2, typical phone landscape).
# After 33% bottom crop, that becomes ~2.20:1 (1080 wide -> 490 tall).
CANONICAL_SIZE = (1080, 490)
MIN_VALID_BRIGHTNESS = 25      # Mean brightness below this -> reject as too dark.


def normalize_base(frame: np.ndarray) -> np.ndarray | None:
    """Crop black bars + UI bands, then resize to canonical square.
    Returns None if invalid (empty, too dark, or fully cropped away)."""
    if frame is None or frame.size == 0:
        return None
    if frame.mean() < MIN_VALID_BRIGHTNESS:
        return None

    # Step 1: strip pillarbox/letterbox black bars
    debar = _crop_black_bars(frame)
    if debar.size == 0:
        return None

    # Step 2: crop top/bottom UI bands
    h = debar.shape[0]
    top = int(h * TOP_UI_FRACTION)
    bottom = int(h * (1.0 - BOTTOM_UI_FRACTION))
    if bottom <= top:
        return None
    cropped = debar[top:bottom]
    if cropped.size == 0:
        return None

    return cv2.resize(cropped, CANONICAL_SIZE, interpolation=cv2.INTER_AREA)


def compute_phash(image: np.ndarray) -> str:
    """Perceptual hash of a base image as a hex string."""
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    return str(imagehash.phash(pil))


def save_base_image(image: np.ndarray, image_dir: str) -> str:
    """Persist a normalized base image. Returns the absolute file path."""
    os.makedirs(image_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    path = os.path.join(image_dir, filename)
    if not cv2.imwrite(path, image):
        raise IOError(f"Failed to write base image to {path}")
    logger.debug("Saved normalized base image to %s", path)
    return path
