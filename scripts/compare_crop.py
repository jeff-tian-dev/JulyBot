#!/usr/bin/env python3
"""Extract a single raw frame and show it alongside the normalized version
so we can verify the top/bottom crops aren't cutting off the base."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DISCORD_TOKEN", "compare-crop")
os.environ.setdefault("COC_API_TOKEN", "compare-crop")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/none")
os.environ.setdefault("BASE_IMAGE_DIR", "./data/bases")

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2
import yt_dlp

from modules.base_finder.detector import _crop_black_bars
from modules.base_finder.normalizer import normalize_base, TOP_UI_FRACTION, BOTTOM_UI_FRACTION


def main():
    video_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.youtube.com/watch?v=MLAuybQ7qKs"
    # Pick a known attack timestamp from the validated scan
    timestamp_seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 21.0  # ~3s after first attack

    print(f"Extracting frame at {timestamp_seconds}s from {video_url}")

    # Resolve stream URL
    ydl_opts = {"quiet": True, "no_warnings": True, "format": "best[ext=mp4]/best"}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
        stream_url = info["url"]

    cap = cv2.VideoCapture(stream_url)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    target_frame = int(timestamp_seconds * fps)

    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ok, raw_frame = cap.read()
    cap.release()
    if not ok:
        print("ERROR: failed to read frame")
        return

    print(f"Raw frame shape: {raw_frame.shape}")

    # Step 1: black bar crop
    debar = _crop_black_bars(raw_frame)
    print(f"After black-bar crop: {debar.shape}")

    # Visualize the top/bottom crop lines on a copy of debar
    annotated = debar.copy()
    h, w = annotated.shape[:2]
    top_y = int(h * TOP_UI_FRACTION)
    bottom_y = int(h * (1.0 - BOTTOM_UI_FRACTION))

    # Red lines showing crop boundaries
    cv2.line(annotated, (0, top_y), (w, top_y), (0, 0, 255), 3)
    cv2.line(annotated, (0, bottom_y), (w, bottom_y), (0, 0, 255), 3)
    cv2.putText(annotated, f"TOP CROP {TOP_UI_FRACTION:.0%}", (10, top_y - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
    cv2.putText(annotated, f"BOTTOM CROP {BOTTOM_UI_FRACTION:.0%}", (10, bottom_y + 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

    # Save
    out_dir = Path("data/bases")
    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / "_compare_raw.png"), raw_frame)
    cv2.imwrite(str(out_dir / "_compare_debar.png"), debar)
    cv2.imwrite(str(out_dir / "_compare_annotated.png"), annotated)
    normalized = normalize_base(raw_frame)
    if normalized is not None:
        cv2.imwrite(str(out_dir / "_compare_normalized.png"), normalized)

    print()
    print("Saved 4 files in data/bases/ for comparison:")
    print("  _compare_raw.png        - original frame")
    print("  _compare_debar.png      - after black-bar crop only")
    print("  _compare_annotated.png  - with crop-line markers (RED lines)")
    print("  _compare_normalized.png - final normalized output")


if __name__ == "__main__":
    main()
