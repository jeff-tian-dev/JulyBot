#!/usr/bin/env python3
"""Phase 2 validation: run the base finder pipeline locally with no DB.

Extracts candidate base frames from a YouTube VOD using the production
_process_video_sync() function, saves the normalized PNGs to data/bases/,
and writes a metadata JSON alongside so we can visually inspect the output.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Stub required env vars before any project import (no DB / Discord needed for this script)
os.environ.setdefault("DISCORD_TOKEN", "local-pipeline-runner")
os.environ.setdefault("COC_API_TOKEN", "local-pipeline-runner")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/none")
os.environ.setdefault("BASE_IMAGE_DIR", "./data/bases")

sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.base_finder.pipeline import _process_video_sync


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_local_pipeline.py <youtube_url>")
        sys.exit(1)

    video_url = sys.argv[1]
    output_dir = Path("data/bases")
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / "metadata.json"

    print(f"Running local pipeline on {video_url}")
    print(f"Output directory: {output_dir}")
    print(f"This will take ~10-15 min for a 20-30 min video.\n")

    start_time = datetime.now()
    candidates = _process_video_sync(video_url, channel_id="LOCAL_TEST")
    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"\nExtracted {len(candidates)} candidate frames in {elapsed:.1f}s")

    # Phase 2 first pass: keep only the 5th candidate of each attack
    # (CAPTURE_FRAMES_COUNT=10 per attack, so candidates[4::10] = middle frame)
    candidates_to_save = candidates[4::10]
    print(f"Saving 5th candidate per attack: {len(candidates_to_save)} images")

    # Load existing metadata if any
    if metadata_path.exists():
        with open(metadata_path) as f:
            existing = json.load(f)
    else:
        existing = []

    # Save each selected candidate
    import cv2
    new_records = []
    for i, cand in enumerate(candidates_to_save):
        img = cand["image"]
        h, w = img.shape[:2]
        filename = f"base_{datetime.now().strftime('%Y%m%d_%H%M%S')}_attack{i:02d}.png"
        filepath = output_dir / filename
        cv2.imwrite(str(filepath), img)
        new_records.append({
            "filename": filename,
            "attack_index": i,
            "phash": cand["phash"],
            "source_url": cand["source_url"],
            "source_channel": cand["source_channel"],
            "width": w,
            "height": h,
            "captured_at": datetime.now().isoformat(),
        })

    # Write updated metadata
    with open(metadata_path, "w") as f:
        json.dump(existing + new_records, f, indent=2)

    print(f"Saved {len(new_records)} images to {output_dir}")
    print(f"Metadata: {metadata_path}")
    print()
    print("Next: open data/bases/ in an image viewer and inspect:")
    print("  - Are images 1080x1080?")
    print("  - Top UI bar (gold/elixir/XP) cropped cleanly?")
    print("  - Bottom troop bar cropped cleanly?")
    print("  - Black bars (pillarbox) removed?")
    print("  - Base centered and fully visible?")


if __name__ == "__main__":
    main()
