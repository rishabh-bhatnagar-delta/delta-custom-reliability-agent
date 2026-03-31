"""
File-based cache that persists across process restarts.

Works alongside the in-memory cache in fetcher.py:
  1. Check in-memory cache (fast)
  2. Check file cache (survives restarts)
  3. Fetch from AWS (slow)

Cache files are stored in .cache/ at the project root as JSON with TTL metadata.
"""

import hashlib
import json
import logging
import os
import time
from typing import Any, Optional

from src.core.constants import CACHE_TTL_MINUTES

logger = logging.getLogger(__name__)

_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".cache")
_CACHE_TTL = CACHE_TTL_MINUTES * 60


def _cache_path(namespace: str, key: str) -> str:
    """Generate a file path for a cache entry."""
    safe_key = hashlib.md5(key.encode()).hexdigest()
    ns_dir = os.path.join(_CACHE_DIR, namespace)
    os.makedirs(ns_dir, exist_ok=True)
    return os.path.join(ns_dir, f"{safe_key}.json")


def get(namespace: str, key: str) -> Optional[Any]:
    """Read from file cache if entry exists and hasn't expired."""
    path = _cache_path(namespace, key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            entry = json.load(f)
        if time.time() - entry.get("ts", 0) >= _CACHE_TTL:
            os.remove(path)
            logger.debug(f"file_cache: expired {namespace}/{key}")
            return None
        logger.info(f"file_cache: hit {namespace}/{key}")
        return entry["data"]
    except (json.JSONDecodeError, KeyError, OSError) as e:
        logger.warning(f"file_cache: read error {namespace}/{key} - {e}")
        return None


def put(namespace: str, key: str, data: Any) -> None:
    """Write data to file cache with current timestamp."""
    path = _cache_path(namespace, key)
    try:
        entry = {"ts": time.time(), "key": key, "data": data}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entry, f, default=str)
        logger.debug(f"file_cache: stored {namespace}/{key}")
    except OSError as e:
        logger.warning(f"file_cache: write error {namespace}/{key} - {e}")


def clear(namespace: str = None) -> None:
    """Clear file cache. If namespace given, clear only that namespace."""
    import shutil
    target = os.path.join(_CACHE_DIR, namespace) if namespace else _CACHE_DIR
    if os.path.exists(target):
        shutil.rmtree(target)
        logger.info(f"file_cache: cleared {'namespace ' + namespace if namespace else 'all'}")
