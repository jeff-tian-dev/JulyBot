"""Normalize raw frames into clean, comparable base screenshots.

NOTE FOR CV ENGINEER: The UI crop percentages are approximate.
Verify against actual CoC screenshots at common resolutions (1080p, 1440p).
"""
from __future__ import annotations

import logging
import os
import uuid

import cv2
import imagehash
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


# --- Tunable parameters ----------------------------------------------------
TOP_UI_FRACTION = 0.08      # Fraction of frame height occupied by the top bar (gold/elixir/XP).
BOTTOM_UI_FRACTION = 0.20   # Fraction occupied by the troop / spell bar at the bottom.
CANONICAL_SIZE = (1080, 1080)  # (width, height) for stored base images.
MIN_VALID_BRIGHTNESS = 25      # Mean brightness below this -> reject as too dark.


def normalize_base(frame: np.ndarray) -> np.ndarray | None:
    """Crop UI bands and resize to a canonical square. Returns None if invalid."""
    if frame is None or frame.size == 0:
        return None
    if frame.mean() < MIN_VALID_BRIGHTNESS:
        return None

    h = frame.shape[0]
    top = int(h * TOP_UI_FRACTION)
    bottom = int(h * (1.0 - BOTTOM_UI_FRACTION))
    if bottom <= top:
        return None
    cropped = frame[top:bottom]

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
