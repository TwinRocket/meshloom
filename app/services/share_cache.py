"""Disk cache of shared objects (``GET /api/share/{id}``): a thin ``diskcache`` wrapper.

The backend fetches a shared image once and serves it from disk afterwards, so a
second view makes no request to the share service. ``diskcache.Cache`` does the
work (SQLite index, atomic writes, least-recently-used eviction past
``size_limit``); this module only bounds it and runs its blocking calls in the
default executor. No hand-written LRU, index or atomic write.

- **Where.** ``<data dir>/share-cache/``, the data dir being the folder of
  ``MESHCORE_DATABASE_PATH`` (``/var/lib/meshloom`` for the packages, inside the
  service's ``ReadWritePaths``; ``./data`` in Docker and a checkout). Created
  (mode 0700) on first use.
- **Bounds.** ``SIZE_LIMIT`` (200 MiB, least-recently-used eviction by diskcache),
  ``MAX_ENTRIES`` (oldest inserted entries dropped past it) and ``TTL_S`` per
  entry.
- **Keys and values.** The key is the share id (from T28, the id and a
  fingerprint of the decryption key); the value is the raw bytes, the
  validated content type its ``tag``. Bytes and strings are stored raw by
  diskcache, never pickled.

Only validated objects are stored (``app/services/share.py``, ``read_object``).
Tests: ``tests/test_share_cache.py``.
"""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import diskcache

SIZE_LIMIT = 200 * 1024 * 1024
MAX_ENTRIES = 5000
# How long a stored object is served without asking the service again. Short
# enough that a block or a deletion on the service reaches this cache within hours.
TTL_S = 6 * 3600.0
CACHE_DIRNAME = "share-cache"

T = TypeVar("T")


@dataclass(frozen=True)
class CachedObject:
    content: bytes
    content_type: str


def default_directory() -> Path:
    """``<data dir>/share-cache``: next to the database."""
    from app.config import settings

    return Path(settings.database_path).resolve().parent / CACHE_DIRNAME


class ShareCache:
    """A bounded ``diskcache.Cache``, opened lazily, with async accessors."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        size_limit: int = SIZE_LIMIT,
        max_entries: int = MAX_ENTRIES,
        ttl_s: float = TTL_S,
    ) -> None:
        self._directory = directory
        self.size_limit = size_limit
        self.max_entries = max_entries
        self.ttl_s = ttl_s
        self._cache: diskcache.Cache | None = None
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._directory if self._directory is not None else default_directory()

    @property
    def opened(self) -> bool:
        return self._cache is not None

    def _open(self) -> diskcache.Cache:
        with self._lock:
            if self._cache is None:
                directory = self.directory
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                self._cache = diskcache.Cache(
                    str(directory),
                    size_limit=self.size_limit,
                    eviction_policy="least-recently-used",
                    # Every object is a file: the size accounting (and the
                    # bytes clear() frees) then covers all of them.
                    disk_min_file_size=0,
                )
            return self._cache

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(fn, *args))

    # Blocking bodies -------------------------------------------------------

    def _get(self, key: str) -> CachedObject | None:
        # diskcache's return types are unions over its options: read them as Any.
        cache: Any = self._open()
        value, tag = cache.get(key, default=None, tag=True)
        if not isinstance(value, bytes) or not isinstance(tag, str):
            return None
        return CachedObject(content=value, content_type=tag)

    def _set(self, key: str, obj: CachedObject) -> None:
        cache: Any = self._open()
        cache.set(key, obj.content, expire=self.ttl_s, tag=obj.content_type)
        # diskcache bounds the size only: trim the entry count, oldest first.
        while len(cache) > self.max_entries:
            try:
                oldest, _ = cache.peekitem(last=False, expire_time=False)
            except KeyError:
                break
            cache.delete(oldest)

    def _clear(self) -> int:
        cache = self._open()
        before = cache.volume()
        cache.clear()
        return max(0, before - cache.volume())

    def _count(self) -> int:
        cache: Any = self._open()
        return len(cache)

    def _volume(self) -> int:
        return self._open().volume()

    # Async API -------------------------------------------------------------

    async def get(self, key: str) -> CachedObject | None:
        return await self._run(self._get, key)

    async def set(self, key: str, obj: CachedObject) -> None:
        await self._run(self._set, key, obj)

    async def clear(self) -> int:
        """Empty the cache; the bytes freed (``volume()`` before minus after)."""
        return await self._run(self._clear)

    async def volume(self) -> int:
        return await self._run(self._volume)

    async def count(self) -> int:
        return await self._run(self._count)

    def close(self) -> None:
        with self._lock:
            if self._cache is not None:
                self._cache.close()
                self._cache = None


_cache = ShareCache()


def share_cache() -> ShareCache:
    return _cache


def reset_share_cache_for_tests(cache: ShareCache | None = None) -> None:
    """Empty and close the current cache if it was opened, then use ``cache``."""
    global _cache
    old = _cache
    if old._cache is not None:
        old._cache.clear()
    old.close()
    _cache = cache or ShareCache()
