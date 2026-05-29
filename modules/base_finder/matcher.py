"""Query the base_cache for matches against a user-submitted image.

NOTE FOR CV ENGINEER: pHash comparison is the baseline approach here.
If you move to embedding-based search, replace the phash distance calc
with a pgvector nearest-neighbor query:
    ORDER BY embedding <-> $1 LIMIT $2
The embedding column already exists in base_cache for this purpose.
"""
from __future__ import annotations

import logging

import asyncpg
import imagehash
import numpy as np

from modules.base_finder.normalizer import compute_phash, normalize_base

logger = logging.getLogger(__name__)

PHASH_BITS = 64  # imagehash.phash() default produces 8x8 = 64 bits


def _phash_distance(a: str, b: str) -> int:
    """Hamming distance between two pHash hex strings."""
    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


def _similarity_from_distance(distance: int) -> float:
    """Map Hamming distance (0..PHASH_BITS) to similarity (1.0..0.0)."""
    return max(0.0, 1.0 - (distance / PHASH_BITS))


async def find_matching_bases(
    pool: asyncpg.Pool,
    query_image: np.ndarray,
    top_n: int = 5,
    phash_threshold: int = 15,
) -> list[dict]:
    """Find the closest matching bases in the cache for a query image."""
    normalized = normalize_base(query_image)
    if normalized is None:
        logger.info("Query image rejected by normalize_base")
        return []
    query_hash = compute_phash(normalized)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT image_path, phash, source_url, source_channel,
                   town_hall_level, captured_at
            FROM base_cache;
            """
        )

    scored: list[dict] = []
    for r in rows:
        try:
            dist = _phash_distance(query_hash, r["phash"])
        except ValueError:
            continue
        if dist > phash_threshold:
            continue
        scored.append({
            "image_path": r["image_path"],
            "phash": r["phash"],
            "source_url": r["source_url"],
            "source_channel": r["source_channel"],
            "town_hall_level": r["town_hall_level"],
            "similarity_score": _similarity_from_distance(dist),
            "captured_at": r["captured_at"],
        })

    scored.sort(key=lambda x: x["similarity_score"], reverse=True)
    return scored[:top_n]


async def is_duplicate(pool: asyncpg.Pool, phash: str, threshold: int = 10) -> bool:
    """True if a base with a sufficiently similar pHash is already cached."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT phash FROM base_cache;")
    for r in rows:
        try:
            if _phash_distance(phash, r["phash"]) <= threshold:
                return True
        except ValueError:
            continue
    return False
