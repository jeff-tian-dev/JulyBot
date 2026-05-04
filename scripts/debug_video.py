#!/usr/bin/env python3
"""Debug: sample specific timestamps from a video and check what the detector sees."""
import sys
from pathlib import Path

import cv2
import yt_dlp

sys.path.insert(0, str(Path(__file__).parent.parent))
from modules.base_finder.detector import is_loading_screen, _edge_density, _pixel_std, _normalize


def debug_video(video_url: str, timestamps_sec: list):
    print(f"Resolving {video_url}...")
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestvideo[height<=720]/best[height<=720]",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        stream_url = info.get("url")
        if not stream_url:
            for fmt in info.get("formats", []):
                if fmt.get("vcodec") != "none" and fmt.get("height", 0) >= 480:
                    stream_url = fmt["url"]
                    break
        if not stream_url:
            print("ERROR: Could not find video stream")
            return

    cap = cv2.VideoCapture(stream_url)
    fps = cap.get(cv2.CAP_PROP_FPS)
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH)
    height = cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    print(f"FPS: {fps}, Resolution: {int(width)}x{int(height)}\n")

    print(f"{'Timestamp':<12} {'Edge':>8} {'Std':>7} {'IsLoading':>10}  {'Saved as':<30}")
    print("-" * 80)

    samples_dir = Path("data/samples/debug")
    samples_dir.mkdir(parents=True, exist_ok=True)

    for ts_sec in timestamps_sec:
        target_frame = int(ts_sec * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
        ret, frame = cap.read()
        if not ret:
            print(f"{ts_sec}s: Could not read frame")
            continue

        # Apply same normalization as detector
        normalized = _normalize(frame)
        edge_d = _edge_density(normalized)
        px_std = _pixel_std(normalized)
        is_loading = is_loading_screen(frame)

        save_name = f"frame_{int(ts_sec)}s.png"
        save_path = samples_dir / save_name
        cv2.imwrite(str(save_path), frame)

        print(f"{ts_sec}s{'':<8} {edge_d:>8.4f} {px_std:>7.1f} {str(is_loading):>10}  {save_name}")

    cap.release()


if __name__ == "__main__":
    url = "https://www.youtube.com/watch?v=MLAuybQ7qKs"
    timestamps = [0, 5, 10, 30, 60, 120, 180, 277, 300, 400, 500, 1000]
    debug_video(url, timestamps)
