#!/usr/bin/env python3
"""Phase 3 benchmark: measure pHash matching accuracy against the local query set.

For each entry in data/query_candidates/metadata.json:
  - Load the query image (already normalized — best-case scenario; real user
    screenshots will be harder)
  - Compare its pHash against every entry in data/bases/metadata.json
  - Check whether expected_phash appears in the top-1 / top-5 results

Reports top-1 accuracy, top-5 accuracy, not-found rate, and median latency
at threshold values 5, 10, 15, 20.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from statistics import median

os.environ.setdefault("DISCORD_TOKEN", "x")
os.environ.setdefault("COC_API_TOKEN", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://localhost/none")

sys.path.insert(0, str(Path(__file__).parent.parent))

import cv2

from modules.base_finder.matcher import PHASH_BITS, _phash_distance, _similarity_from_distance
from modules.base_finder.normalizer import compute_phash

CACHE_META = Path("data/bases/metadata.json")
QUERY_META = Path("data/query_candidates/metadata.json")
QUERY_DIR  = Path("data/query_candidates")
THRESHOLDS = [5, 10, 12, 15, 20]
TOP_N = 5


def _match(query_hash: str, cache: list[dict], threshold: int) -> list[dict]:
    """Return cache entries within threshold, sorted by similarity descending."""
    scored = []
    for entry in cache:
        try:
            dist = _phash_distance(query_hash, entry["phash"])
        except ValueError:
            continue
        if dist <= threshold:
            scored.append({**entry, "distance": dist,
                           "similarity": _similarity_from_distance(dist)})
    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:TOP_N]


def run_benchmark(threshold: int, cache: list[dict], queries: list[dict]) -> dict:
    top1 = top5 = not_found = 0
    latencies = []

    for q in queries:
        img_path = QUERY_DIR / q["filename"]
        if not img_path.exists():
            continue  # user moved this query out — skip, don't penalise
        frame = cv2.imread(str(img_path))
        if frame is None:
            continue

        t0 = time.perf_counter()
        q_hash = compute_phash(frame)
        results = _match(q_hash, cache, threshold)
        latencies.append(time.perf_counter() - t0)

        result_hashes = [r["phash"] for r in results]
        expected = q["expected_phash"]

        if result_hashes and result_hashes[0] == expected:
            top1 += 1
        if expected in result_hashes:
            top5 += 1
        else:
            not_found += 1

    total = len(queries)
    return {
        "threshold": threshold,
        "total": total,
        "top1_pct": 100 * top1 / total if total else 0,
        "top5_pct": 100 * top5 / total if total else 0,
        "not_found_pct": 100 * not_found / total if total else 0,
        "median_ms": 1000 * median(latencies) if latencies else 0,
    }


def main():
    if not CACHE_META.exists():
        print(f"ERROR: {CACHE_META} not found — run run_local_pipeline.py first")
        sys.exit(1)
    if not QUERY_META.exists():
        print(f"ERROR: {QUERY_META} not found — run run_local_pipeline.py first")
        sys.exit(1)

    cache_raw = json.loads(CACHE_META.read_text())
    queries = json.loads(QUERY_META.read_text())

    # Only include cache entries whose file still lives in data/bases/
    # (user may have moved false positives to data/no_match/)
    cache = [e for e in cache_raw if (Path("data/bases") / e["filename"]).exists()]
    removed = len(cache_raw) - len(cache)
    if removed:
        print(f"Skipped {removed} cache entries (files moved out of data/bases/)")

    # Skip queries whose expected match was moved out
    queries = [q for q in queries if any(e["phash"] == q["expected_phash"] for e in cache)]
    print(f"Cache entries: {len(cache)}   Query candidates: {len(queries)}\n")

    print(f"{'Threshold':>10} {'Top-1':>8} {'Top-5':>8} {'Not found':>10} {'Median ms':>10}")
    print("-" * 52)
    for t in THRESHOLDS:
        r = run_benchmark(t, cache, queries)
        print(f"{r['threshold']:>10}  {r['top1_pct']:>6.1f}%  {r['top5_pct']:>6.1f}%  "
              f"{r['not_found_pct']:>8.1f}%  {r['median_ms']:>8.2f}ms")

    print()
    print("Note: queries are pipeline frames (same attack, different timestamp) —")
    print("real user screenshots will likely score lower.")


if __name__ == "__main__":
    main()
