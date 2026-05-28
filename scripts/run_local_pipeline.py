#!/usr/bin/env python3
"""Phase 2/3 validation: run the base finder pipeline locally with no DB.

For each detected attack, saves:
  - Frame 0  -> data/bases/         (the "cache" entry)
  - Frames 1-9 -> data/query_candidates/  (query set for Phase 3 benchmark)

Each query record stores expected_phash pointing back to its cache entry so
the benchmark script can check whether find_matching_bases() returns the right result.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

os.environ.setdefault("DISCORD_TOKEN", "local-pipeline-runner")
os.environ.setdefault("COC_API_TOKEN", "local-pipeline-runner")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/none")
os.environ.setdefault("BASE_IMAGE_DIR", "./data/bases")

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2

from modules.base_finder.pipeline import _process_video_sync


def main():
    if len(sys.argv) < 2:
        print("Usage: python run_local_pipeline.py <youtube_url>")
        sys.exit(1)

    video_url = sys.argv[1]
    cache_dir = Path("data/bases")
    query_dir = Path("data/query_candidates")
    cache_dir.mkdir(parents=True, exist_ok=True)
    query_dir.mkdir(parents=True, exist_ok=True)

    cache_meta_path = cache_dir / "metadata.json"
    query_meta_path = query_dir / "metadata.json"

    print(f"Running local pipeline on {video_url}")
    print(f"This will take ~10-15 min for a 20-30 min video.\n")

    start_time = datetime.now()
    candidates = _process_video_sync(video_url, channel_id="LOCAL_TEST")
    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"Extracted {len(candidates)} candidate frames in {elapsed:.1f}s")

    # Group by attack_id; within each attack, index 0 -> cache, rest -> queries
    by_attack: dict[int, list[dict]] = defaultdict(list)
    for cand in candidates:
        by_attack[cand["attack_id"]].append(cand)

    print(f"Attacks detected: {len(by_attack)}")

    # Load existing metadata files
    cache_records = json.loads(cache_meta_path.read_text()) if cache_meta_path.exists() else []
    query_records = json.loads(query_meta_path.read_text()) if query_meta_path.exists() else []

    new_cache = 0
    new_queries = 0
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for attack_id, frames in sorted(by_attack.items()):
        # Frame 0 is the cache entry
        cache_cand = frames[0]
        cache_filename = f"base_{ts}_a{attack_id:02d}.png"
        cache_filepath = cache_dir / cache_filename
        cv2.imwrite(str(cache_filepath), cache_cand["image"])
        h, w = cache_cand["image"].shape[:2]
        cache_records.append({
            "filename": cache_filename,
            "attack_id": attack_id,
            "phash": cache_cand["phash"],
            "source_url": cache_cand["source_url"],
            "source_channel": cache_cand["source_channel"],
            "width": w,
            "height": h,
            "captured_at": datetime.now().isoformat(),
        })
        new_cache += 1

        # Frames 1-9 are query candidates
        for q_idx, q_cand in enumerate(frames[1:], start=1):
            q_filename = f"query_{ts}_a{attack_id:02d}_f{q_idx:02d}.png"
            q_filepath = query_dir / q_filename
            cv2.imwrite(str(q_filepath), q_cand["image"])
            query_records.append({
                "filename": q_filename,
                "attack_id": attack_id,
                "frame_index": q_idx,
                "phash": q_cand["phash"],
                "expected_phash": cache_cand["phash"],   # what find_matching_bases() should return
                "source_url": q_cand["source_url"],
                "captured_at": datetime.now().isoformat(),
            })
            new_queries += 1

    cache_meta_path.write_text(json.dumps(cache_records, indent=2))
    query_meta_path.write_text(json.dumps(query_records, indent=2))

    print(f"Cache entries saved:   {new_cache}  -> {cache_dir}/")
    print(f"Query candidates saved:{new_queries} -> {query_dir}/")
    print(f"Metadata: {cache_meta_path}, {query_meta_path}")


if __name__ == "__main__":
    main()
