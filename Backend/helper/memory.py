"""Memory measurement, garbage collection, and task lifecycle management."""
from __future__ import annotations

import asyncio
import ctypes
import gc
import os
import re
import resource
import sys
import threading
import time
from typing import Any, Coroutine, Dict, Optional, Set

from Backend.logger import LOGGER

_TRACKED_TASKS: Set[asyncio.Task] = set()


def get_rss_kb() -> int:
    """Read VmRSS in KiB from /proc/self/status, falling back to resource.getrusage."""
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1])
    except Exception:
        pass

    try:
        ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if sys.platform == "darwin":
            return ru // 1024
        return ru
    except Exception:
        return 0


def _safe_len(obj: Any) -> int:
    if obj is None:
        return 0
    if hasattr(obj, "qsize") and callable(obj.qsize):
        try:
            return obj.qsize()
        except Exception:
            return 0
    if hasattr(obj, "__len__"):
        try:
            return len(obj)
        except Exception:
            return 0
    return 0


def _collect_cache_lens() -> Dict[str, Any]:
    caches: Dict[str, Any] = {}

    # Metadata caches (Task 1)
    try:
        from Backend.helper.metadata import common as mc
        for name in [
            "IMDB_CACHE",
            "TMDB_SEARCH_CACHE",
            "TMDB_DETAILS_CACHE",
            "EPISODE_CACHE",
            "ALT_TITLES_CACHE",
            "TVDB_CACHE",
            "KITSU_CACHE",
        ]:
            if hasattr(mc, name):
                caches[name] = _safe_len(getattr(mc, name))
        if hasattr(mc, "get_disk_cache_stats"):
            try:
                caches["DISK_CACHE"] = mc.get_disk_cache_stats()
            except Exception:
                pass
    except Exception:
        pass

    # Streamer and streaming state (Task 3 & 5)
    try:
        from Backend.helper import custom_dl
        if hasattr(custom_dl, "ACTIVE_STREAMS"):
            caches["ACTIVE_STREAMS"] = _safe_len(custom_dl.ACTIVE_STREAMS)
        if hasattr(custom_dl, "RECENT_STREAMS"):
            caches["RECENT_STREAMS"] = _safe_len(custom_dl.RECENT_STREAMS)
        if hasattr(custom_dl, "ByteStreamer") and hasattr(custom_dl.ByteStreamer, "_instances"):
            caches["ByteStreamer_instances"] = _safe_len(custom_dl.ByteStreamer._instances)
    except Exception:
        pass

    try:
        from Backend.fastapi.routes import stream_routes
        if hasattr(stream_routes, "_streamer_by_client"):
            caches["_streamer_by_client"] = _safe_len(stream_routes._streamer_by_client)
        if hasattr(stream_routes, "_title_cache"):
            caches["_title_cache"] = _safe_len(stream_routes._title_cache)
        if hasattr(stream_routes, "_thumb_cache"):
            caches["_thumb_cache"] = _safe_len(stream_routes._thumb_cache)
        if hasattr(stream_routes, "get_thumb_disk_cache_stats"):
            try:
                caches["_thumb_disk_cache"] = stream_routes.get_thumb_disk_cache_stats()
            except Exception:
                pass
    except Exception:
        pass

    # Indexing queue (Task 4)
    if "Backend.pyrofork.plugins.receiver" in sys.modules:
        try:
            rc = sys.modules["Backend.pyrofork.plugins.receiver"]
            if hasattr(rc, "file_queue"):
                caches["receiver_file_queue"] = _safe_len(rc.file_queue)
        except Exception:
            pass

    # Analytics caches (Task 5)
    try:
        from Backend.helper import analytics
        if hasattr(analytics, "_IP_CACHE"):
            caches["analytics_IP_CACHE"] = _safe_len(analytics._IP_CACHE)
        if hasattr(analytics, "_LAST_FULL"):
            caches["analytics_LAST_FULL"] = _safe_len(analytics._LAST_FULL)
    except Exception:
        pass

    # Global search caches (Task 5)
    try:
        from Backend.helper import global_search
        if hasattr(global_search, "_last_search_ts"):
            caches["search_last_search_ts"] = _safe_len(global_search._last_search_ts)
        if hasattr(global_search, "_result_cache"):
            caches["search_result_cache"] = _safe_len(global_search._result_cache)
        if hasattr(global_search, "_chat_title_cache"):
            caches["search_chat_title_cache"] = _safe_len(global_search._chat_title_cache)
    except Exception:
        pass

    # Fanart cache (Task 5)
    try:
        from Backend.helper import fanart
        if hasattr(fanart, "_tvdb_cache"):
            caches["fanart_tvdb_cache"] = _safe_len(fanart._tvdb_cache)
        if hasattr(fanart, "_inflight"):
            caches["fanart_inflight"] = _safe_len(fanart._inflight)
    except Exception:
        pass

    # Anime maps (Task 6)
    try:
        from Backend.helper.metadata import episode_maps
        if hasattr(episode_maps, "_anibridge"):
            caches["anibridge"] = _safe_len(episode_maps._anibridge)
        if hasattr(episode_maps, "_anime_lists"):
            caches["anime_lists"] = _safe_len(episode_maps._anime_lists)
        if hasattr(episode_maps, "_anime_lists_by_imdb"):
            caches["anime_lists_by_imdb"] = _safe_len(episode_maps._anime_lists_by_imdb)
    except Exception:
        pass

    caches["tracked_tasks"] = len(_TRACKED_TASKS)
    return caches


def mem_stats() -> Dict[str, Any]:
    """Gather process memory stats, coroutine count, thread count, gc counts, and cache sizes."""
    rss_kb = get_rss_kb()
    try:
        task_count = len(asyncio.all_tasks())
    except RuntimeError:
        task_count = 0

    return {
        "rss_kb": rss_kb,
        "rss_mb": round(rss_kb / 1024.0, 2),
        "asyncio_tasks": task_count,
        "thread_count": threading.active_count(),
        "gc_count": list(gc.get_count()),
        "caches": _collect_cache_lens(),
    }


def release_memory(reason: str = "") -> Dict[str, Any]:
    """Force garbage collection and glibc malloc_trim, logging RSS before and after."""
    start_t = time.perf_counter()
    rss_before_kb = get_rss_kb()
    collected = gc.collect()
    trimmed = False
    if sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL("libc.so.6")
            if hasattr(libc, "malloc_trim"):
                trimmed = bool(libc.malloc_trim(0))
        except Exception:
            pass

    rss_after_kb = get_rss_kb()
    diff_kb = rss_before_kb - rss_after_kb
    rss_before_mb = round(rss_before_kb / 1024.0, 2)
    rss_after_mb = round(rss_after_kb / 1024.0, 2)
    diff_mb = round(diff_kb / 1024.0, 2)
    duration_ms = round((time.perf_counter() - start_t) * 1000.0, 2)

    LOGGER.info(
        f"[Memory] release_memory({reason}): RSS {rss_before_mb}MB -> {rss_after_mb}MB "
        f"(freed {diff_mb}MB, gc_collected={collected}, malloc_trimmed={trimmed}, duration={duration_ms}ms)"
    )

    return {
        "reason": reason,
        "rss_before_mb": rss_before_mb,
        "rss_after_mb": rss_after_mb,
        "freed_mb": diff_mb,
        "gc_collected": collected,
        "malloc_trimmed": trimmed,
        "duration_ms": duration_ms,
    }


def spawn(coro: Coroutine, *, name: Optional[str] = None) -> asyncio.Task:
    """Create and track an asyncio.Task in a module-level set, logging any unhandled exception."""
    task = asyncio.create_task(coro, name=name)
    _TRACKED_TASKS.add(task)

    def _done_callback(t: asyncio.Task) -> None:
        _TRACKED_TASKS.discard(t)
        if not t.cancelled():
            exc = t.exception()
            if exc:
                task_name = t.get_name() if hasattr(t, "get_name") else str(t)
                LOGGER.error(
                    f"[Memory] Tracked task '{task_name}' failed with unhandled exception: {exc}",
                    exc_info=exc,
                )

    task.add_done_callback(_done_callback)
    return task


def get_tracked_tasks() -> Set[asyncio.Task]:
    return set(_TRACKED_TASKS)


async def cancel_all_tracked_tasks() -> int:
    """Cancel all active tracked tasks and await their completion."""
    tasks = [t for t in _TRACKED_TASKS if not t.done()]
    for t in tasks:
        t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    return len(tasks)


def parse_mem_threshold_mb(val: Any) -> float:
    """
    Parse a memory threshold into megabytes (float).
    Supports raw numbers (e.g. 800, 1024) and human unit strings:
    - '1gb', '1GB', '1g', '1G' -> 1024.0
    - '1.5gb', '1.5GB' -> 1536.0
    - '800mb', '800MB', '800m' -> 800.0
    - '800' -> 800.0
    """
    if val is None:
        return 0.0
    s = str(val).strip().strip("\"'").lower()
    if not s:
        return 0.0

    m = re.match(r"^([\d.]+)\s*([a-z]*)$", s)
    if not m:
        return 0.0

    try:
        num = float(m.group(1))
    except ValueError:
        return 0.0

    unit = m.group(2).strip()
    if not unit or unit in ("mb", "m", "mib"):
        return round(num, 2)
    elif unit in ("gb", "g", "gib"):
        return round(num * 1024.0, 2)
    elif unit in ("kb", "k", "kib"):
        return round(num / 1024.0, 2)
    elif unit in ("b",):
        return round(num / (1024.0 * 1024.0), 2)

    return round(num, 2)


def _memory_limit_mb() -> float:
    """
    Detect the effective memory limit in MB.
    Candidates:
      - cgroup v2: /sys/fs/cgroup/memory.max (bytes, only when all digits)
      - cgroup v1: /sys/fs/cgroup/memory/memory.limit_in_bytes (bytes, only when all digits)
      - Host RAM: MemTotal from /proc/meminfo (kB)
    Returns the smallest candidate in MB (cgroup limit if container-constrained,
    or host MemTotal when unconstrained), or 0.0 if none.
    """
    candidates = []

    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    content = f.read().strip()
                if content.isdigit():
                    bytes_val = int(content)
                    if bytes_val > 0:
                        candidates.append(bytes_val / (1024.0 * 1024.0))
        except Exception:
            pass

    try:
        if os.path.exists("/proc/meminfo"):
            with open("/proc/meminfo", "r") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        if len(parts) >= 2 and parts[1].isdigit():
                            candidates.append(int(parts[1]) / 1024.0)
                        break
    except Exception:
        pass

    if not candidates:
        return 0.0

    return round(min(candidates), 2)


def get_watchdog_threshold_mb() -> float:
    """Watchdog threshold calculated as 75% of the detected memory limit."""
    return round(_memory_limit_mb() * 0.75, 2)


async def memory_watchdog_loop(interval_seconds: int = 60) -> None:
    """Periodically check RSS; if above 75% memory limit, trigger a background memory trim."""
    threshold_mb = get_watchdog_threshold_mb()
    if threshold_mb <= 0:
        LOGGER.info("[Memory Watchdog] Memory limit is unknown (threshold 0MB). Watchdog standing down.")
        return

    LOGGER.info(f"[Memory Watchdog] Started with threshold {threshold_mb}MB (75% limit, interval {interval_seconds}s)")

    while True:
        await asyncio.sleep(interval_seconds)

        rss_kb = get_rss_kb()
        rss_mb = round(rss_kb / 1024.0, 2)

        if rss_mb <= threshold_mb:
            continue

        LOGGER.info(
            f"[Memory Watchdog] RSS ({rss_mb}MB) exceeded threshold ({threshold_mb}MB). "
            f"Triggering background memory release."
        )

        # Evict transient metadata RAM caches so live dictionary objects can be reclaimed
        try:
            from Backend.helper.metadata.common import clear_metadata_ram_caches
            clear_metadata_ram_caches()
        except Exception:
            pass

        rel = await asyncio.to_thread(release_memory, "watchdog")
        if rel["rss_after_mb"] > threshold_mb:
            LOGGER.warning(
                f"[Memory Watchdog] RSS remains above threshold after trim: {rel['rss_after_mb']}MB > {threshold_mb}MB"
            )
            # If little memory was freed, wait a longer cooldown (15m) before re-checking to avoid CPU burn & log spam
            if rel.get("freed_mb", 0) < 5.0:
                await asyncio.sleep(900)


def start_memory_watchdog() -> Optional[asyncio.Task]:
    """Start the background memory watchdog loop."""
    return spawn(memory_watchdog_loop(), name="memory-watchdog")
