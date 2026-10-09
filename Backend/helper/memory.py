"""Memory measurement, garbage collection, and task lifecycle management."""
from __future__ import annotations

import asyncio
import ctypes
import gc
import os
import resource
import sys
import threading
import time
import tracemalloc
from typing import Any, Coroutine, Dict, List, Optional, Set

from Backend.logger import LOGGER

_TRACKED_TASKS: Set[asyncio.Task] = set()

_DEBUG_MEM: bool = os.getenv("DEBUG_MEM", "").strip().lower() in ("1", "true", "yes")
_last_snapshot: Optional[tracemalloc.Snapshot] = None

if _DEBUG_MEM:
    try:
        if not tracemalloc.is_tracing():
            tracemalloc.start(15)
            _last_snapshot = tracemalloc.take_snapshot()
            LOGGER.info("[Memory] tracemalloc started with 15 frames (DEBUG_MEM=1)")
    except Exception as e:
        LOGGER.warning(f"[Memory] Failed to start tracemalloc: {e}")


def is_tracemalloc_enabled() -> bool:
    return tracemalloc.is_tracing()


def get_tracemalloc_diff(top_n: int = 25) -> List[Dict[str, Any]]:
    """Return the top N allocation diffs since the last snapshot."""
    global _last_snapshot
    if not tracemalloc.is_tracing():
        return []
    try:
        current = tracemalloc.take_snapshot()
        if _last_snapshot is None:
            _last_snapshot = current
            return []
        stats = current.compare_to(_last_snapshot, "lineno")
        _last_snapshot = current
        diffs = []
        for stat in stats[:top_n]:
            diffs.append({
                "trace": str(stat.traceback),
                "size_diff_kb": round(stat.size_diff / 1024.0, 2),
                "size_kb": round(stat.size / 1024.0, 2),
                "count_diff": stat.count_diff,
                "count": stat.count,
            })
        return diffs
    except Exception as e:
        LOGGER.warning(f"[Memory] Error calculating tracemalloc diff: {e}")
        return []


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


async def memory_watchdog_loop(interval_seconds: int = 60) -> None:
    """Periodically check RSS; if above MEM_WATCHDOG_MB and idle, release memory."""
    threshold_str = os.getenv("MEM_WATCHDOG_MB", "0").strip()
    try:
        threshold_mb = float(threshold_str)
    except ValueError:
        threshold_mb = 0.0

    if threshold_mb <= 0:
        return

    LOGGER.info(f"[Memory Watchdog] Started with threshold {threshold_mb}MB (interval {interval_seconds}s)")

    while True:
        await asyncio.sleep(interval_seconds)
        rss_kb = get_rss_kb()
        rss_mb = round(rss_kb / 1024.0, 2)

        if rss_mb <= threshold_mb:
            continue

        streaming_active = False
        try:
            from Backend.helper import custom_dl
            if hasattr(custom_dl, "ACTIVE_STREAMS"):
                streaming_active = bool(custom_dl.ACTIVE_STREAMS)
        except Exception:
            pass

        scan_active = False
        try:
            from Backend.helper import scan_manager
            if hasattr(scan_manager, "scan_manager") and getattr(scan_manager.scan_manager, "state", {}).get("status") == "running":
                scan_active = True
            elif hasattr(scan_manager, "dbcheck_manager") and getattr(scan_manager.dbcheck_manager, "state", {}).get("status") == "running":
                scan_active = True
            elif hasattr(scan_manager, "duplicate_manager") and getattr(scan_manager.duplicate_manager, "state", {}).get("status") == "running":
                scan_active = True
        except Exception:
            pass

        if streaming_active or scan_active:
            continue

        rel = await asyncio.to_thread(release_memory, "watchdog")
        if rel["rss_after_mb"] > threshold_mb:
            LOGGER.warning(
                f"[Memory Watchdog] RSS remains above threshold after trim: {rel['rss_after_mb']}MB > {threshold_mb}MB"
            )


def start_memory_watchdog() -> Optional[asyncio.Task]:
    threshold_str = os.getenv("MEM_WATCHDOG_MB", "0").strip()
    try:
        threshold_mb = float(threshold_str)
    except ValueError:
        threshold_mb = 0.0

    if threshold_mb > 0:
        return spawn(memory_watchdog_loop(), name="memory-watchdog")
    return None
