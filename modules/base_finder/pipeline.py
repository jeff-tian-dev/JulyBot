"""YouTube VOD ingestion pipeline.

Flow:
  1. List recent video URLs for each watched channel via yt-dlp.
  2. Stream each video (no full download).
  3. Sample frames at TARGET_SAMPLE_FPS scanning for loading screens.
  4. On detection: sweep frame-by-frame to find loading-screen end,
     wait POST_LOADING_DELAY_FRAMES for the camera to settle, then capture
     CAPTURE_FRAMES_COUNT candidates spaced CAPTURE_FRAME_SPACING apart.
  5. Normalize, pHash, and store new bases (skip duplicates).
  6. Enforce sliding-window cache size limit.
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
from modules.base_finder.detector import is_loading_screen, looks_like_base
from modules.base_finder.matcher import is_duplicate
from modules.base_finder.normalizer import compute_phash, normalize_base, save_base_image

logger = logging.getLogger(__name__)


# --- Tunable parameters ----------------------------------------------------
TARGET_SAMPLE_FPS = 1.0          # Initial scan rate when looking for loading screens
POST_LOADING_DELAY_FRAMES = 30   # Raw frames to wait after loading ends before capturing
                                 # (~1s at 30fps; tune to balance "camera settled" vs
                                 # "player hasn't moved screen yet")
CAPTURE_FRAMES_COUNT = 10        # Number of candidate frames to capture per attack
CAPTURE_FRAME_SPACING = 6        # Raw frames between captured candidates (~0.2s at 30fps)
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
    """Synchronous frame-sampling with state machine for capture phases.

    State machine:
      SCANNING  -> sample at TARGET_SAMPLE_FPS, look for loading screen
      SWEEPING  -> frame-by-frame after loading detected, find exact end
      WAITING   -> count POST_LOADING_DELAY_FRAMES to skip transition
      CAPTURING -> grab CAPTURE_FRAMES_COUNT candidates at CAPTURE_FRAME_SPACING
    """
    stream_url = _get_direct_stream_url(video_url)
    if not stream_url:
        logger.warning("Could not resolve stream URL for %s", video_url)
        return []

    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        logger.warning("OpenCV failed to open stream for %s", video_url)
        return []

    SCANNING, SWEEPING, WAITING, CAPTURING = "scanning", "sweeping", "waiting", "capturing"

    try:
        source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        scan_step = max(1, int(round(source_fps / TARGET_SAMPLE_FPS)))

        candidates: list[dict] = []
        frame_idx = 0
        state = SCANNING
        attack_id = 0
        wait_remaining = 0
        capture_remaining = 0
        spacing_counter = 0

        while True:
            ok, frame = cap.read()
            if not ok:
                break

            if state == SCANNING:
                # Only check every Nth frame to save compute
                if frame_idx % scan_step == 0 and is_loading_screen(frame):
                    state = SWEEPING
                    attack_id += 1

            elif state == SWEEPING:
                # Frame-by-frame until loading screen ends
                if not is_loading_screen(frame):
                    state = WAITING
                    wait_remaining = POST_LOADING_DELAY_FRAMES

            elif state == WAITING:
                wait_remaining -= 1
                if wait_remaining <= 0:
                    state = CAPTURING
                    capture_remaining = CAPTURE_FRAMES_COUNT
                    spacing_counter = 0

            elif state == CAPTURING:
                if spacing_counter == 0:
                    # Final gate: only save if the frame actually looks like
                    # a base view (rejects menus, popups, account switchers
                    # that passed is_loading_screen's "too simple" check).
                    if looks_like_base(frame):
                        normalized = normalize_base(frame)
                        if normalized is not None:
                            candidates.append({
                                "image": normalized,
                                "phash": compute_phash(normalized),
                                "source_url": video_url,
                                "source_channel": channel_id,
                                "attack_id": attack_id,
                            })
                    capture_remaining -= 1
                    if capture_remaining <= 0:
                        state = SCANNING
                    else:
                        spacing_counter = CAPTURE_FRAME_SPACING
                else:
                    spacing_counter -= 1

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
