#!/usr/bin/env python3
"""Synthetic benchmark comparing memory usage between unbounded dictionary caching
and the two-tier (TTLCache + diskcache) cached_call architecture.

Supports isolated per-process execution:
  python scripts/bench_metadata_cache.py old
  python scripts/bench_metadata_cache.py new
  python scripts/bench_metadata_cache.py all   (runs both in separate child processes)
"""
import argparse
import asyncio
import os
import subprocess
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Backend.helper.memory import get_rss_kb, release_memory
from Backend.helper.metadata import common as mc


def generate_fake_payload(idx: int) -> dict:
    """Generate a realistic ~20 KB metadata payload."""
    padding_text = "lorem ipsum dolor sit amet " * 750  # ~20 KB
    return {
        "id": f"tt{idx:07d}",
        "title": f"Test Media Title {idx}",
        "year": 2020 + (idx % 5),
        "overview": padding_text,
        "cast": [{"name": f"Actor {j}", "character": f"Char {j}"} for j in range(25)],
        "posters": [f"https://image.tmdb.org/t/p/original/poster_{idx}_{k}.jpg" for k in range(5)],
        "extra_info": {"rating": 8.5, "votes": 12000, "source": "synthetic_bench"},
    }


async def run_unbounded_dict_bench(n: int = 2000):
    print("=" * 60)
    print(f"[Benchmark: OLD] Simulating unbounded dict caching ({n} items)...")
    await asyncio.sleep(0.1)
    rss_start = get_rss_kb() / 1024.0

    dict_store = {}
    for i in range(n):
        dict_store[f"key_{i}"] = generate_fake_payload(i)

    rss_peak = get_rss_kb() / 1024.0
    print(f"  Start RSS: {rss_start:.2f} MB")
    print(f"  Peak RSS:  {rss_peak:.2f} MB (+{rss_peak - rss_start:.2f} MB allocated)")
    print(f"  Items in store: {len(dict_store)}")

    dict_store.clear()
    del dict_store
    rel = release_memory("unbounded-dict-cleared")
    rss_final = get_rss_kb() / 1024.0
    print(f"  Final RSS: {rss_final:.2f} MB (freed: {rel['freed_mb']:.2f} MB)")
    print("=" * 60)
    return rss_start, rss_peak, rss_final


async def run_two_tier_cache_bench(n: int = 2000):
    print("=" * 60)
    print(f"[Benchmark: NEW] Simulating two-tier cached_call ({n} items)...")
    await mc.clear_metadata_caches()
    await asyncio.sleep(0.1)
    rss_start = get_rss_kb() / 1024.0

    for i in range(n):
        key = f"key_{i}"
        payload = generate_fake_payload(i)

        async def producer(p=payload):
            return p

        await mc.cached_call(mc.IMDB_CACHE, key, "bench_imdb", producer)

    rss_peak = get_rss_kb() / 1024.0
    disk_stats = mc.get_disk_cache_stats()
    print(f"  Start RSS: {rss_start:.2f} MB")
    print(f"  Peak RSS:  {rss_peak:.2f} MB (+{rss_peak - rss_start:.2f} MB allocated)")
    print(f"  RAM Cache Entries:  {len(mc.IMDB_CACHE)} (capped at {mc.METADATA_RAM_CACHE_SIZE})")
    print(f"  Disk Cache Entries: {disk_stats.get('items')} ({disk_stats.get('size_mb')} MB on disk)")

    mc.clear_metadata_ram_caches()
    rel = release_memory("two-tier-ram-cleared")
    rss_final = get_rss_kb() / 1024.0
    print(f"  Final RSS: {rss_final:.2f} MB (freed: {rel['freed_mb']:.2f} MB)")
    print("=" * 60)

    # Cleanup disk cache created by bench
    await mc.clear_metadata_caches()
    return rss_start, rss_peak, rss_final


def run_isolated(scenario: str, count: int):
    cmd = [sys.executable, __file__, scenario, "--count", str(count)]
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser(description="Synthetic Metadata Cache Benchmark")
    parser.add_argument(
        "scenario",
        nargs="?",
        default="all",
        choices=["old", "new", "all"],
        help="Benchmark scenario to run ('old', 'new', or 'all' in isolated processes)",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=2000,
        help="Number of items to generate (default: 2000)",
    )
    args = parser.parse_args()

    if args.scenario == "old":
        asyncio.run(run_unbounded_dict_bench(args.count))
    elif args.scenario == "new":
        asyncio.run(run_two_tier_cache_bench(args.count))
    else:
        print("Running Synthetic Metadata Cache Benchmark in Isolated Processes\n")
        run_isolated("old", args.count)
        print()
        run_isolated("new", args.count)


if __name__ == "__main__":
    main()
