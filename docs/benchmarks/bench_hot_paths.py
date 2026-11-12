import asyncio
import gc
import os
import sys
import time
import tracemalloc
from collections import deque
from typing import Dict, List, Optional, Tuple

# Ensure DATABASE is set so Backend can import if needed
os.environ.setdefault("DATABASE", "mongodb://localhost:27017/tracking,mongodb://localhost:27017/storage_1")

# ==============================================================================
# BENCHMARK 1: Range Request Header Parsing & TTFB Simulation (stream_routes.py)
# ==============================================================================
def bench_stream_headers():
    print("\n--- BENCHMARK 1: Range Header Calculation & Header Generation ---")
    # Both fork and upstream use _build_stream_headers, but let's measure its throughput and TTFB impact
    from Backend.fastapi.routes.stream_routes import _build_stream_headers

    mime_type = "video/mp4"
    file_name = "Big_Buck_Bunny_1080p.mkv"
    file_size = 2_147_483_648  # 2 GB
    range_headers = [
        "bytes=0-",
        "bytes=1048576-2097151",
        "bytes=1073741824-",
        "bytes=500000-1000000",
        None
    ]
    iterations = 50_000

    start_t = time.perf_counter()
    for i in range(iterations):
        rh = range_headers[i % len(range_headers)]
        if rh:
            # parse range
            parts = rh.replace("bytes=", "").split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if parts[1] else file_size - 1
            req_length = end - start + 1
        else:
            start = 0
            end = file_size - 1
            req_length = file_size
        headers, status = _build_stream_headers(mime_type, file_name, req_length, rh, start, end, file_size)
    elapsed = time.perf_counter() - start_t
    ops_sec = iterations / elapsed
    print(f"Header calculation throughput: {ops_sec:,.0f} ops/sec ({elapsed/iterations*1e6:.2f} µs/op)")


# ==============================================================================
# BENCHMARK 2: Title & Thumbnail Cache: BoundedDict vs TTLCache + DiskCache
# ==============================================================================
def bench_caches():
    print("\n--- BENCHMARK 2: Caching (BoundedDict vs TTLCache & DiskCache) ---")
    from Backend.helper.bounded import BoundedDict
    from cachetools import TTLCache

    n_keys = 5000
    cache_capacity = 2000

    # Test BoundedDict (Upstream implementation)
    bd = BoundedDict(cache_capacity)
    t0 = time.perf_counter()
    for i in range(n_keys):
        bd[f"title_{i}"] = {"name": f"Movie Title {i}", "ts": time.time()}
    # Read back 5000 keys (some exist, some evicted)
    hits_bd = sum(1 for i in range(n_keys) if bd.get(f"title_{i}") is not None)
    t_bd = time.perf_counter() - t0

    # Test TTLCache (Fork implementation)
    ttl_c = TTLCache(maxsize=cache_capacity, ttl=300)
    t0 = time.perf_counter()
    for i in range(n_keys):
        ttl_c[f"title_{i}"] = {"name": f"Movie Title {i}", "ts": time.time()}
    hits_ttl = sum(1 for i in range(n_keys) if ttl_c.get(f"title_{i}") is not None)
    t_ttl = time.perf_counter() - t0

    print(f"Upstream BoundedDict(2000): {n_keys} writes + reads in {t_bd*1000:.2f} ms (Retained: {len(bd)}, Hits: {hits_bd})")
    print(f"Fork TTLCache(2000, 300s):  {n_keys} writes + reads in {t_ttl*1000:.2f} ms (Retained: {len(ttl_c)}, Hits: {hits_ttl})")

    # Benchmark Thumbnail Memory Footprint
    # Upstream: stores 200 thumbnails in RAM (e.g. 50KB JPEG each -> 10MB in RAM)
    # Fork: stores up to 8MB in RAM TTLCache, offloads remainder to DiskCache (2GB)
    tracemalloc.start()
    snapshot1 = tracemalloc.take_snapshot()
    bd_thumbs = BoundedDict(200)
    sample_thumb = b"x" * 50_000 # 50 KB
    for i in range(200):
        bd_thumbs[f"thumb_{i}"] = (sample_thumb, time.time() + 3600)
    snapshot2 = tracemalloc.take_snapshot()
    tracemalloc.stop()
    bd_mem_kb = sum(stat.size_diff for stat in snapshot2.compare_to(snapshot1, 'lineno')) / 1024
    print(f"Upstream 200 thumbs in BoundedDict RAM footprint: ~{bd_mem_kb:.1f} KB")


# ==============================================================================
# BENCHMARK 3: Simulated Chunk Streaming & Concurrent Streams (custom_dl.py)
# ==============================================================================
async def bench_custom_dl_streaming():
    print("\n--- BENCHMARK 3: Chunk Streaming & Range Request Throughput (custom_dl.py) ---")
    chunk_size = 1024 * 1024  # 1MB chunk
    total_file_size = 50 * 1024 * 1024  # 50 MB simulated file

    # Simulated MTProto chunk fetcher
    async def mock_fetch_chunk(session_id: int, offset: int, fail_count: int = 0) -> bytes:
        # simulate MTProto network transit time ~5ms
        await asyncio.sleep(0.005)
        return b"A" * chunk_size

    # Fork stream pipeline simulation (single bot vs multi bot with retries)
    async def simulate_stream(session_pool_size: int, fail_first_client: bool, retry_across_bots: bool):
        queue = asyncio.Queue(maxsize=4)
        stop_event = asyncio.Event()
        start_time = time.perf_counter()
        first_chunk_time = None
        bytes_received = 0

        # Producer
        async def producer():
            num_chunks = 10  # stream 10 chunks (10MB)
            for seq_idx in range(num_chunks):
                if stop_event.is_set():
                    break
                # Upstream rotates bot on retry: (seq_idx + tries) % pool
                # Fork/Base kept same bot on retry: seq_idx % pool
                tries = 0
                chunk_data = None
                while tries < 3:
                    if retry_across_bots:
                        bot_idx = (seq_idx + tries) % session_pool_size
                    else:
                        bot_idx = seq_idx % session_pool_size
                    # simulate bot failure if fail_first_client and bot_idx == 0
                    if fail_first_client and bot_idx == 0:
                        tries += 1
                        await asyncio.sleep(0.002)
                        continue
                    else:
                        chunk_data = await mock_fetch_chunk(bot_idx, seq_idx * chunk_size)
                        break
                await queue.put((seq_idx, chunk_data))

        # Consumer
        async def consumer():
            nonlocal first_chunk_time, bytes_received
            for seq_idx in range(10):
                idx, chunk = await queue.get()
                if first_chunk_time is None:
                    first_chunk_time = time.perf_counter()
                if chunk:
                    bytes_received += len(chunk)
                queue.task_done()

        prod_task = asyncio.create_task(producer())
        cons_task = asyncio.create_task(consumer())
        await asyncio.gather(prod_task, cons_task)
        total_time = time.perf_counter() - start_time
        ttfb = (first_chunk_time - start_time) * 1000 if first_chunk_time else 0
        mb_per_sec = (bytes_received / (1024 * 1024)) / total_time if total_time > 0 else 0
        return ttfb, mb_per_sec, bytes_received

    # 1. Normal conditions (3 bots, healthy)
    ttfb_f, mbps_f, bytes_f = await simulate_stream(3, fail_first_client=False, retry_across_bots=False)
    ttfb_u, mbps_u, bytes_u = await simulate_stream(3, fail_first_client=False, retry_across_bots=True)
    print(f"Normal conditions (3 bots in pool, 10MB transfer):")
    print(f"  Fork/Base strategy:     TTFB: {ttfb_f:.2f} ms | Throughput: {mbps_f:.1f} MB/s | Delivered: {bytes_f/(1024*1024):.0f} MB")
    print(f"  Upstream retry strategy: TTFB: {ttfb_u:.2f} ms | Throughput: {mbps_u:.1f} MB/s | Delivered: {bytes_u/(1024*1024):.0f} MB")

    # 2. Degraded conditions (Bot 0 is rate-limited / erroring)
    ttfb_f_deg, mbps_f_deg, bytes_f_deg = await simulate_stream(3, fail_first_client=True, retry_across_bots=False)
    ttfb_u_deg, mbps_u_deg, bytes_u_deg = await simulate_stream(3, fail_first_client=True, retry_across_bots=True)
    print(f"Degraded conditions (Bot 0 failing / rate-limited):")
    print(f"  Fork/Base strategy (stays on bot 0):     TTFB: {ttfb_f_deg:.2f} ms | Throughput: {mbps_f_deg:.1f} MB/s | Delivered: {bytes_f_deg/(1024*1024):.0f} MB (Failed chunks drop!)")
    print(f"  Upstream strategy (retries on next bot): TTFB: {ttfb_u_deg:.2f} ms | Throughput: {mbps_u_deg:.1f} MB/s | Delivered: {bytes_u_deg/(1024*1024):.0f} MB (All recovered!)")


# ==============================================================================
# BENCHMARK 4: Receiver Consumer Queue & Resilience (receiver.py)
# ==============================================================================
async def bench_receiver_resilience():
    print("\n--- BENCHMARK 4: Receiver Queue Throughput & Crash Recovery (receiver.py) ---")
    queue = asyncio.Queue()

    # Upstream pattern: unmonitored consumer
    # Fork pattern: supervised consumer with task_done in finally

    processed_count = 0
    crashed_count = 0

    async def fork_supervised_process_file():
        nonlocal processed_count, crashed_count
        while True:
            item = await queue.get()
            try:
                # simulate processing with 5% chance of crash
                if item == "crash":
                    crashed_count += 1
                    raise RuntimeError("Simulated transient error")
                processed_count += 1
            finally:
                queue.task_done()

    async def fork_supervised_consumer():
        while True:
            try:
                await fork_supervised_process_file()
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(0.001)

    cons_task = asyncio.create_task(fork_supervised_consumer())

    # Feed 1000 items with 10 crash triggers
    t0 = time.perf_counter()
    for i in range(1000):
        if i % 100 == 50:
            await queue.put("crash")
        else:
            await queue.put(f"item_{i}")
    await queue.join()
    elapsed = time.perf_counter() - t0
    cons_task.cancel()
    try:
        await cons_task
    except asyncio.CancelledError:
        pass

    print(f"Fork Supervised Consumer: Processed {processed_count} items with {crashed_count} crashes recovered in {elapsed*1000:.2f} ms without queue deadlock!")


if __name__ == "__main__":
    bench_stream_headers()
    bench_caches()
    asyncio.run(bench_custom_dl_streaming())
    asyncio.run(bench_receiver_resilience())
