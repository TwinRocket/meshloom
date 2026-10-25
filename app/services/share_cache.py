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
- **Bounds.** ``SIZE_LIMIT`` (200 MiB: ``volume()``, the files plus the SQLite
  index, plus the SQLite write-ahead log), ``MAX_ENTRIES`` (oldest inserted
  entries dropped past it) and ``TTL_S`` (1 h) per entry. The cap is
  approximate: the files and the index are exact, the log is read from its
  file size, and it is truncated back under ``wal_budget()`` after a write
  (``journal_size_limit`` and ``wal_autocheckpoint`` bound it between
  checkpoints too). diskcache's culling (``cull_limit`` entries per write, in
  one batch) both overshoots and over-evicts, so it is off (``cull_limit=0``)
  and ``_set`` drops expired entries, then evicts the least recently used ones
  until the total is back under the limit. Writes are serialised by a lock, so
  two concurrent fills cannot both pass the check; an object larger than the
  cap is not stored.
- **diskcache internals.** The least-recently-used order has no public
  accessor (``peekitem`` orders by insertion), so ``_lru_key`` reads the
  ``Cache`` table through ``cache._sql`` (diskcache is pinned ``<5.7``).
  ``_open`` runs that query once as a self-check; if it fails, ``_set`` logs a
  warning and falls back to clearing the whole cache when it is over the limit.
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
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import diskcache

logger = logging.getLogger(__name__)

SIZE_LIMIT = 200 * 1024 * 1024
MAX_ENTRIES = 5000
# How long a stored object is served without asking the service again: a
# moderation takedown or a deletion on the service reaches this cache within it.
TTL_S = 3600.0
CACHE_DIRNAME = "share-cache"
# Largest write-ahead log kept (and counted in the cap), at most an eighth of
# the cap for small test caches.
WAL_BUDGET = 4 * 1024 * 1024
SQLITE_PAGE_SIZE = 4096

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
        self._lock = threading.Lock()  # opening
        self._write_lock = threading.Lock()  # serialises _set / _clear
        self._lru_ok = True

    def wal_budget(self) -> int:
        return min(WAL_BUDGET, max(self.size_limit // 8, SQLITE_PAGE_SIZE))

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
                    # diskcache's own culling drops up to cull_limit entries in
                    # one batch whenever the volume is over: off, _set evicts
                    # exactly what is needed.
                    cull_limit=0,
                    # The write-ahead log is part of the cap: truncate it back
                    # to the budget after each checkpoint, and checkpoint
                    # often (pages of 4 KiB).
                    sqlite_journal_size_limit=self.wal_budget(),
                    sqlite_wal_autocheckpoint=max(1, self.wal_budget() // SQLITE_PAGE_SIZE // 4),
                )
                self._lru_ok = self._self_check(self._cache)
            return self._cache

    @staticmethod
    def _self_check(cache: Any) -> bool:
        try:
            cache._sql("SELECT key FROM Cache ORDER BY access_time LIMIT 1").fetchone()
        except Exception:  # noqa: BLE001 - any change of diskcache's internals
            logger.warning(
                "diskcache internals changed: share cache falls back to clear-on-overflow",
                exc_info=True,
            )
            return False
        return True

    def _wal_path(self) -> Path:
        return self.directory / "cache.db-wal"

    def _wal_size(self) -> int:
        try:
            return os.path.getsize(self._wal_path())
        except OSError:
            return 0

    def _used(self, cache: Any) -> int:
        return int(cache.volume()) + self._wal_size()

    def _truncate_wal(self) -> None:
        try:
            con = sqlite3.connect(str(self.directory / "cache.db"), timeout=1.0)
            try:
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            finally:
                con.close()
        except sqlite3.Error:
            logger.debug("share cache WAL checkpoint skipped", exc_info=True)

    def _lru_key(self, cache: Any) -> str | None:
        row = cache._sql("SELECT key FROM Cache ORDER BY access_time LIMIT 1").fetchone()
        return None if row is None else row[0]

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
        if len(obj.content) > self.size_limit - self.wal_budget():
            return  # would evict everything and still not fit
        with self._write_lock:
            cache.expire()  # automatic culling is off: drop expired entries here
            cache.set(key, obj.content, expire=self.ttl_s, tag=obj.content_type)
            # diskcache bounds the size only: trim the entry count, oldest first.
            while len(cache) > self.max_entries:
                try:
                    oldest, _ = cache.peekitem(last=False, expire_time=False)
                except KeyError:
                    break
                cache.delete(oldest)
            if self._wal_size() > self.wal_budget():
                self._truncate_wal()
            # Evict least recently used entries until files, index and log fit.
            while self._used(cache) > self.size_limit:
                if not self._lru_ok:
                    cache.clear()
                    break
                try:
                    victim = self._lru_key(cache)
                except Exception:  # noqa: BLE001 - internals changed after open
                    self._lru_ok = False
                    logger.warning("share cache LRU query failed, clearing", exc_info=True)
                    continue
                if victim is None:
                    break
                cache.delete(victim)
                if self._wal_size() > self.wal_budget():
                    self._truncate_wal()

    def _clear(self) -> int:
        cache = self._open()
        with self._write_lock:
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
