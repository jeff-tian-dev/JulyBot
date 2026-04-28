"""YouTube VOD ingestion pipeline.

Flow:
  1. List recent video URLs for each watched channel via yt-dlp.
  2. Stream each video (no full download) and sample frames at ~2 fps.
  3. Detect attack loading screens; capture the next 2-3 frames after each.
  4. Normalize, pHash, and store new bases (skip duplicates).
  5. Enforce sliding-window cache size limit.
"""
from __future__ import annotations

import asyncio
import logging
import os

import asyncpg
import cv2
import numpy as np
import yt_dlp

from config.settings import settings
from modules.base_finder.detector import is_loading_screen
from modules.base_finder.matcher import is_duplicate
from modules.base_finder.normalizer import compute_phash, normalize_base, save_base_image

logger = logging.getLogger(__name__)


# --- Tunable parameters ----------------------------------------------------
TARGET_SAMPLE_FPS = 2.0       # Approximate frame sample rate.
FRAMES_AFTER_LOADING = 3      # Frames to capture once a loading screen ends.
MAX_VIDEOS_PER_CHANNEL = 10
YTDLP_LIST_OPTS = {
    "extract_flat": True,
    "quiet": True,
    "skip_download": True,
}
YTDLP_STREAM_OPTS = {
    "quiet": True,
    "skip_download": True,
    "format": "best[ext=mp4]/best",
}


def get_video_urls(channel_id: str, max_videos: int = MAX_VIDEOS_PER_CHANNEL) -> list[str]:
    """List recent video URLs for a channel without downloading them."""
    channel_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    opts = {**YTDLP_LIST_OPTS, "playlistend": max_videos}
    urls: list[str] = []
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)
        entries = (info or {}).get("entries") or []
        for entry in entries[:max_videos]:
            url = entry.get("url") or entry.get("webpage_url")
            if not url:
                continue
            if not url.startswith("http"):
                url = f"https://www.youtube.com/watch?v={url}"
            urls.append(url)
    return urls


def _get_direct_stream_url(video_url: str) -> str | None:
    """Resolve a YouTube page URL to a direct media stream URL."""
    with yt_dlp.YoutubeDL(YTDLP_STREAM_OPTS) as ydl:
        info = ydl.extract_info(video_url, download=False)
    return (info or {}).get("url")


def _process_video_sync(video_url: str, channel_id: str) -> list[dict]:
    """Synchronous frame-sampling. Returns list of candidate base records."""
    stream_url = _get_direct_stream_url(video_url)
    if not stream_url:
        logger.warning("Could not resolve stream URL for %s", video_url)
        return []

    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        logger.warning("OpenCV failed to open stream for %s", video_url)
        return []

    try:
        source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(source_fps / TARGET_SAMPLE_FPS)))

        candidates: list[dict] = []
        frame_idx = 0
        in_loading = False
        captured_after_loading = 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_idx % step != 0:
                frame_idx += 1
                continue

            if is_loading_screen(frame):
                in_loading = True
                captured_after_loading = 0
            elif in_loading and captured_after_loading < FRAMES_AFTER_LOADING:
                normalized = normalize_base(frame)
                if normalized is not None:
                    candidates.append({
                        "image": normalized,
                        "phash": compute_phash(normalized),
                        "source_url": video_url,
                        "source_channel": channel_id,
                    })
                captured_after_loading += 1
                if captured_after_loading >= FRAMES_AFTER_LOADING:
                    in_loading = False

            frame_idx += 1
    finally:
        cap.release()

    return candidates


async def process_video(pool: asyncpg.Pool, video_url: str, channel_id: str) -> int:
    """Process a single video URL. Returns number of new bases stored."""
    candidates = await asyncio.to_thread(_process_video_sync, video_url, channel_id)

    new_count = 0
    for cand in candidates:
        if await is_duplicate(pool, cand["phash"]):
            continue
        path = save_base_image(cand["image"], settings.BASE_IMAGE_DIR)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO base_cache (image_path, phash, source_url, source_channel)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (phash) DO NOTHING;
                """,
                path,
                cand["phash"],
                cand["source_url"],
                cand["source_channel"],
            )
            new_count += 1
    return new_count


async def enforce_cache_limit(pool: asyncpg.Pool) -> int:
    """Delete the oldest rows past BASE_CACHE_SIZE and their image files."""
    async with pool.acquire() as conn:
        rows_to_delete = await conn.fetch(
            """
            SELECT id, image_path
            FROM base_cache
            ORDER BY captured_at DESC
            OFFSET $1;
            """,
            settings.BASE_CACHE_SIZE,
        )
        if not rows_to_delete:
            return 0
        ids = [r["id"] for r in rows_to_delete]
        await conn.execute("DELETE FROM base_cache WHERE id = ANY($1::int[]);", ids)

    deleted = 0
    for r in rows_to_delete:
        try:
            os.remove(r["image_path"])
            deleted += 1
        except OSError as e:
            logger.warning("Could not delete %s: %s", r["image_path"], e)
    logger.info("Cache trim: removed %d rows (deleted %d files)", len(rows_to_delete), deleted)
    return len(rows_to_delete)


async def run_pipeline(pool: asyncpg.Pool) -> dict:
    """Run ingestion across every watched channel."""
    async with pool.acquire() as conn:
        channels = await conn.fetch("SELECT channel_id FROM watched_channels;")

    summary = {"processed": 0, "new_bases": 0, "skipped_duplicates": 0, "errors": 0}

    for ch in channels:
        channel_id = ch["channel_id"]
        try:
            urls = await asyncio.to_thread(get_video_urls, channel_id)
        except Exception:
            logger.exception("Failed to list videos for channel %s", channel_id)
            summary["errors"] += 1
            continue

        for url in urls:
            try:
                new = await process_video(pool, url, channel_id)
                summary["processed"] += 1
                summary["new_bases"] += new
            except Exception:
                logger.exception("Failed to process video %s", url)
                summary["errors"] += 1

    try:
        await enforce_cache_limit(pool)
    except Exception:
        logger.exception("enforce_cache_limit failed")
        summary["errors"] += 1

    return summary
