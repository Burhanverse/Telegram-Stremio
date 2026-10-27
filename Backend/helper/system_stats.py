"""Host server and application process performance statistics.
Reads Linux /proc interfaces and os.statvfs directly with zero extra dependencies.
"""
from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from Backend.helper.disk_cache import CACHE_ROOT
from Backend.helper.memory import get_rss_kb
from Backend.helper.pyro import get_readable_file_size, get_readable_time
from Backend.logger import LOGGER

_StartTime = time.time()


def is_in_container() -> bool:
    """Detect whether the current process is running inside a Docker/OCI container."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        if os.path.exists("/proc/1/cgroup"):
            with open("/proc/1/cgroup", "r") as f:
                content = f.read()
                if any(x in content for x in ("docker", "containerd", "kubepods")):
                    return True
    except Exception:
        pass
    return False


def parse_meminfo(content: str) -> Optional[dict]:
    """Parse /proc/meminfo into memory and swap statistics (in bytes and percentages)."""
    if not content:
        return None
    raw: Dict[str, int] = {}
    for line in content.splitlines():
        if ":" not in line:
            continue
        parts = line.split(":", 1)
        k = parts[0].strip()
        v_parts = parts[1].strip().split()
        if v_parts and v_parts[0].isdigit():
            raw[k] = int(v_parts[0]) * 1024  # convert kB to bytes

    total = raw.get("MemTotal")
    if not total or total <= 0:
        return None

    free = raw.get("MemFree", 0)
    buffers = raw.get("Buffers", 0)
    cached = raw.get("Cached", 0)
    available = raw.get("MemAvailable", free + buffers + cached)
    available = min(total, max(0, available))

    used = max(0, total - available)
    usage_pct = round((used / total) * 100.0, 1)

    swap_total = raw.get("SwapTotal", 0)
    swap_free = raw.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    swap_usage_pct = round((swap_used / swap_total) * 100.0, 1) if swap_total > 0 else 0.0

    return {
        "total": total,
        "used": used,
        "available": available,
        "usage_pct": usage_pct,
        "swap_total": swap_total,
        "swap_used": swap_used,
        "swap_usage_pct": swap_usage_pct,
        "total_formatted": get_readable_file_size(total),
        "used_formatted": get_readable_file_size(used),
        "available_formatted": get_readable_file_size(available),
        "swap_total_formatted": get_readable_file_size(swap_total),
        "swap_used_formatted": get_readable_file_size(swap_used),
    }


def parse_stat_cpu(content: str) -> Tuple[Optional[Tuple[int, int]], Dict[str, Tuple[int, int]]]:
    """Parse /proc/stat into (overall_total, overall_idle) and {core_name: (total, idle)}."""
    if not content:
        return None, {}
    overall = None
    cores: Dict[str, Tuple[int, int]] = {}

    for line in content.splitlines():
        parts = line.split()
        if not parts:
            continue
        name = parts[0]
        if name == "cpu":
            try:
                vals = [int(x) for x in parts[1:]]
                total = sum(vals)
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                overall = (total, idle)
            except Exception:
                pass
        elif re.match(r"^cpu\d+$", name):
            try:
                vals = [int(x) for x in parts[1:]]
                total = sum(vals)
                idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
                cores[name] = (total, idle)
            except Exception:
                pass

    return overall, cores


def parse_loadavg(content: str) -> Optional[List[float]]:
    """Parse /proc/loadavg into [1m, 5m, 15m] load averages."""
    if not content:
        return None
    try:
        parts = content.strip().split()
        if len(parts) >= 3:
            return [round(float(parts[0]), 2), round(float(parts[1]), 2), round(float(parts[2]), 2)]
    except Exception:
        pass
    return None


def parse_uptime(content: str) -> Optional[float]:
    """Parse /proc/uptime into system uptime in seconds."""
    if not content:
        return None
    try:
        parts = content.strip().split()
        if parts:
            return float(parts[0])
    except Exception:
        pass
    return None


def parse_net_dev(content: str) -> Tuple[int, int]:
    """Parse /proc/net/dev returning total (bytes_recv, bytes_sent)."""
    if not content:
        return 0, 0
    non_lo_recv = 0
    non_lo_sent = 0
    all_recv = 0
    all_sent = 0
    has_non_lo = False

    for line in content.splitlines():
        if ":" not in line:
            continue
        parts = line.split(":", 1)
        iface = parts[0].strip()
        data = parts[1].split()
        if len(data) >= 9:
            try:
                r = int(data[0])
                s = int(data[8])
                all_recv += r
                all_sent += s
                if iface != "lo":
                    has_non_lo = True
                    non_lo_recv += r
                    non_lo_sent += s
            except Exception:
                pass

    if has_non_lo:
        return non_lo_recv, non_lo_sent
    return all_recv, all_sent


def parse_proc_stat_process(content: str) -> Optional[int]:
    """Parse /proc/self/stat returning total user+system CPU ticks for this process."""
    if not content:
        return None
    try:
        idx = content.rfind(")")
        if idx == -1:
            return None
        post = content[idx + 2:].split()
        if len(post) >= 13:
            utime = int(post[11])
            stime = int(post[12])
            return utime + stime
    except Exception:
        pass
    return None


def get_disk_usage(path: Path | str) -> Optional[dict]:
    """Get total, used, free bytes and usage_pct for the filesystem containing path."""
    try:
        p = str(path)
        if not os.path.exists(p):
            p = "/"
        stat = os.statvfs(p)
        total = stat.f_blocks * stat.f_frsize
        if total <= 0:
            return None
        free = stat.f_bavail * stat.f_frsize
        used = total - (stat.f_bfree * stat.f_frsize)
        used = max(0, min(total, used))
        usage_pct = round((used / total) * 100.0, 1)
        return {
            "path": p,
            "total": total,
            "used": used,
            "free": free,
            "usage_pct": usage_pct,
            "total_formatted": get_readable_file_size(total),
            "used_formatted": get_readable_file_size(used),
            "free_formatted": get_readable_file_size(free),
        }
    except Exception as e:
        LOGGER.warning(f"[SystemStats] Failed to read statvfs for {path}: {e}")
        return None


class SystemStatsCollector:
    """Thread-safe collector maintaining state for CPU and network rate deltas."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last_time: float = 0.0
        self._last_cpu_total: Optional[int] = None
        self._last_cpu_idle: Optional[int] = None
        self._last_per_core: Dict[str, Tuple[int, int]] = {}
        self._last_net_recv: Optional[int] = None
        self._last_net_sent: Optional[int] = None
        self._last_proc_ticks: Optional[int] = None
        self._last_cpu_pct: Optional[float] = None
        self._last_per_core_pct: List[float] = []
        self._last_net_rate_recv: float = 0.0
        self._last_net_rate_sent: float = 0.0
        self._last_proc_cpu_pct: Optional[float] = None

        try:
            self._tck = os.sysconf("SC_CLK_TCK")
        except Exception:
            self._tck = 100

    def collect(self) -> dict:
        now = time.time()
        in_container = is_in_container()
        net_label = "Container Network" if in_container else "Server Network"

        # 1. Memory
        mem_data = None
        try:
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    mem_data = parse_meminfo(f.read())
        except Exception as e:
            LOGGER.warning(f"[SystemStats] Failed to read /proc/meminfo: {e}")

        # 2. Load Average & CPU Cores
        core_count = os.cpu_count() or 1
        load_avg = None
        try:
            load_avg = list(os.getloadavg())
            load_avg = [round(x, 2) for x in load_avg]
        except Exception:
            try:
                if os.path.exists("/proc/loadavg"):
                    with open("/proc/loadavg", "r") as f:
                        load_avg = parse_loadavg(f.read())
            except Exception:
                pass

        # 3. CPU overall & per-core usage %
        cpu_overall = None
        cores_raw: Dict[str, Tuple[int, int]] = {}
        try:
            if os.path.exists("/proc/stat"):
                with open("/proc/stat", "r") as f:
                    cpu_overall, cores_raw = parse_stat_cpu(f.read())
        except Exception as e:
            LOGGER.warning(f"[SystemStats] Failed to read /proc/stat: {e}")

        # 4. Network totals
        net_recv = 0
        net_sent = 0
        has_net = False
        try:
            if os.path.exists("/proc/net/dev"):
                with open("/proc/net/dev", "r") as f:
                    net_recv, net_sent = parse_net_dev(f.read())
                    has_net = True
        except Exception as e:
            LOGGER.warning(f"[SystemStats] Failed to read /proc/net/dev: {e}")

        # 5. Process CPU ticks
        proc_ticks = None
        try:
            if os.path.exists("/proc/self/stat"):
                with open("/proc/self/stat", "r") as f:
                    proc_ticks = parse_proc_stat_process(f.read())
        except Exception as e:
            LOGGER.warning(f"[SystemStats] Failed to read /proc/self/stat: {e}")

        # Compute delta rates with lock
        with self._lock:
            delta_t = now - self._last_time if self._last_time > 0 else 0.0

            # Overall CPU %
            if cpu_overall and self._last_cpu_total is not None and delta_t >= 0.2:
                diff_total = cpu_overall[0] - self._last_cpu_total
                diff_idle = cpu_overall[1] - self._last_cpu_idle
                if diff_total > 0:
                    self._last_cpu_pct = round(100.0 * max(0, diff_total - diff_idle) / diff_total, 1)

            # Per-core CPU %
            if cores_raw and self._last_per_core and delta_t >= 0.2:
                per_core_list = []
                for c_name in sorted(cores_raw.keys(), key=lambda x: int(x[3:]) if x[3:].isdigit() else x):
                    cur_c = cores_raw[c_name]
                    last_c = self._last_per_core.get(c_name)
                    if last_c:
                        c_diff_total = cur_c[0] - last_c[0]
                        c_diff_idle = cur_c[1] - last_c[1]
                        if c_diff_total > 0:
                            per_core_list.append(round(100.0 * max(0, c_diff_total - c_diff_idle) / c_diff_total, 1))
                        else:
                            per_core_list.append(0.0)
                if per_core_list:
                    self._last_per_core_pct = per_core_list

            # Network rate
            if has_net and self._last_net_recv is not None and delta_t >= 0.2:
                self._last_net_rate_recv = round(max(0, net_recv - self._last_net_recv) / delta_t, 1)
                self._last_net_rate_sent = round(max(0, net_sent - self._last_net_sent) / delta_t, 1)

            # Process CPU %
            if proc_ticks is not None and self._last_proc_ticks is not None and delta_t >= 0.2:
                p_diff = proc_ticks - self._last_proc_ticks
                if p_diff >= 0 and delta_t > 0:
                    self._last_proc_cpu_pct = round(((p_diff / self._tck) / delta_t) * 100.0, 1)

            # Update sample snapshots
            if delta_t >= 0.2 or self._last_time == 0.0:
                self._last_time = now
                if cpu_overall:
                    self._last_cpu_total = cpu_overall[0]
                    self._last_cpu_idle = cpu_overall[1]
                if cores_raw:
                    self._last_per_core = cores_raw
                if has_net:
                    self._last_net_recv = net_recv
                    self._last_net_sent = net_sent
                if proc_ticks is not None:
                    self._last_proc_ticks = proc_ticks

            cpu_pct_res = self._last_cpu_pct
            per_core_res = self._last_per_core_pct if self._last_per_core_pct else None
            net_rate_recv_res = self._last_net_rate_recv
            net_rate_sent_res = self._last_net_rate_sent
            proc_cpu_pct_res = self._last_proc_cpu_pct

        # 6. Disk usage for host volume mount
        disk_data = get_disk_usage(CACHE_ROOT)

        # 7. Host Uptime vs App Uptime
        host_uptime_sec = None
        try:
            if os.path.exists("/proc/uptime"):
                with open("/proc/uptime", "r") as f:
                    host_uptime_sec = parse_uptime(f.read())
        except Exception:
            pass

        app_uptime_sec = int(now - _StartTime)

        # 8. Process stats
        rss_kb = get_rss_kb()
        host_core_count = len(cores_raw) if cores_raw else (os.cpu_count() or 1)

        proc_data = {
            "pid": os.getpid(),
            "rss_bytes": rss_kb * 1024,
            "rss_mb": round(rss_kb / 1024.0, 2),
            "rss_formatted": get_readable_file_size(rss_kb * 1024),
            "cpu_pct": proc_cpu_pct_res,
            "threads": threading.active_count(),
        }

        return {
            "cpu": {
                "usage_pct": cpu_pct_res,
                "cores": host_core_count,
                "per_core": per_core_res,
                "load_avg": load_avg,
            },
            "memory": mem_data,
            "disk": disk_data,
            "network": {
                "is_container": in_container,
                "label": net_label,
                "bytes_recv": net_recv,
                "bytes_sent": net_sent,
                "recv_rate_bps": net_rate_recv_res,
                "sent_rate_bps": net_rate_sent_res,
                "bytes_recv_formatted": get_readable_file_size(net_recv),
                "bytes_sent_formatted": get_readable_file_size(net_sent),
                "recv_rate_formatted": f"{get_readable_file_size(int(net_rate_recv_res))}/s",
                "sent_rate_formatted": f"{get_readable_file_size(int(net_rate_sent_res))}/s",
            } if has_net else None,
            "uptime": {
                "host_seconds": int(host_uptime_sec) if host_uptime_sec is not None else None,
                "host_formatted": get_readable_time(int(host_uptime_sec)) if host_uptime_sec is not None else None,
                "app_seconds": app_uptime_sec,
                "app_formatted": get_readable_time(app_uptime_sec),
            },
            "process": proc_data,
        }


_GLOBAL_STATS_COLLECTOR = SystemStatsCollector()


def get_server_system_stats() -> dict:
    """Return the current server and application performance metrics."""
    return _GLOBAL_STATS_COLLECTOR.collect()
