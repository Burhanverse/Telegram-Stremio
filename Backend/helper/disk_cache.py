from pathlib import Path
from typing import Optional

import diskcache

from Backend.logger import LOGGER

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_ROOT = PROJECT_ROOT / "cache"


def open_disk_cache(name: str, size_mb: int, label: str) -> Optional[diskcache.Cache]:
    """
    Open or create a diskcache.Cache located in CACHE_ROOT / name.
    """
    path = CACHE_ROOT / name
    try:
        path.mkdir(parents=True, exist_ok=True)
        return diskcache.Cache(
            str(path),
            size_limit=size_mb * 1024 * 1024,
        )
    except Exception as e:
        LOGGER.error(f"[{label}] Failed to initialize diskcache at {path}: {e}")
        return None


def disk_cache_stats(cache: Optional[diskcache.Cache], size_limit_mb: int) -> dict:
    """
    Return dictionary with keys: items, size_bytes, size_mb, size_limit_mb.
    Returns zeros if cache is None or any call raises.
    """
    if cache is None:
        return {"items": 0, "size_bytes": 0, "size_mb": 0.0, "size_limit_mb": size_limit_mb}
    try:
        vol = cache.volume()
        return {
            "items": len(cache),
            "size_bytes": vol,
            "size_mb": round(vol / (1024 * 1024), 2),
            "size_limit_mb": size_limit_mb,
        }
    except Exception:
        return {"items": 0, "size_bytes": 0, "size_mb": 0.0, "size_limit_mb": size_limit_mb}
