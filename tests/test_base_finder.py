"""Unit tests for modules.base_finder."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from modules.base_finder import matcher, normalizer


class _FakePoolAcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_pool(conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolAcquireCtx(conn))
    return pool


def _checkerboard(size: int = 256) -> np.ndarray:
    """A deterministic non-uniform image so phash isn't degenerate."""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[::2, ::2] = 200
    img[1::2, 1::2] = 200
    return img


def test_compute_phash_deterministic() -> None:
    img = _checkerboard()
    h1 = normalizer.compute_phash(img)
    h2 = normalizer.compute_phash(img.copy())
    assert h1 == h2


def test_normalize_base_invalid_frame() -> None:
    black = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert normalizer.normalize_base(black) is None


@pytest.mark.asyncio
async def test_is_duplicate_empty_cache() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = _fake_pool(conn)

    assert await matcher.is_duplicate(pool, phash="0" * 16) is False
