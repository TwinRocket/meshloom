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
- **Bounds.** ``SIZE_LIMIT`` (200 MiB), ``MAX_ENTRIES`` (least recently
  used entries dropped before a write past it) and ``TTL_S`` (1 h) per entry. The
  size counts ``volume()`` (the files plus the SQLite index), the ``-shm`` file
  and ``wal_budget()``, reserved for the write-ahead log whatever it weighs now. ``_set`` takes a write lock,
  drops expired entries, then evicts the least recently used ones BEFORE
  writing until the new object fits, so the measured total never goes over the
  limit, even with concurrent fills (diskcache's own culling writes first and
  evicts in batches of ten: it is off, ``cull_limit=0``). The guarantee is
  approximate: the log is truncated back under its budget after a write
  (``journal_size_limit`` and ``wal_autocheckpoint`` bound it too), but a
  reader pinning a snapshot blocks truncation; the checkpoint then backs off
  and the part of the log beyond the budget is not counted (evicting cannot
  shrink it). An object larger than the cap is not stored.
- **diskcache internals.** The least-recently-used order has no public
  accessor (``peekitem`` orders by insertion), so ``_lru_key`` reads the
  ``Cache`` table through ``cache._sql`` (diskcache is pinned ``<5.7``).
  ``_open`` runs that query once as a self-check; if it, or a later call,
  fails, a warning is logged and a write over the limit clears the cache first,
  then stores the new object.
- **Keys and values.** The key is the share id; the value is the raw bytes, the
  validated content type its ``tag``. An encrypted object is stored as its
  ciphertext envelope (``application/octet-stream``) and decrypted per request,
  so no plaintext and no key material ever reaches the cache. Bytes and strings are stored raw by
  diskcache, never pickled.

``delete`` drops one entry when its author deletes the object
(``DELETE /api/share/{id}``). It first writes a tombstone (key
``TOMBSTONE_PREFIX + id``, an integer value, expiring after ``TTL_S``, so it
outlives the entry), then drops the entry: when the drop fails, the tombstone
still survives a restart, and ``get`` (which then retries the drop) and ``set``
treat a tombstoned id as absent. If the tombstone write itself fails (logged,
exception type only), the drop is still attempted; if both fail, the entry
expires on its own within ``TTL_S``. Tombstones count toward ``MAX_ENTRIES``
and ``clear`` drops them too. Only validated objects are stored (``app/services/share.py``, ``read_object``).
Tests: ``tests/test_share_cache.py``.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import sqlite3
import threading
import time
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
# Room kept for the index row (and its pages) an entry adds.
INDEX_HEADROOM = 2 * SQLITE_PAGE_SIZE
# After a checkpoint a reader blocked, leave the log alone for this long.
WAL_BUSY_BACKOFF_S = 30.0
# Key prefix of a deleted id's tombstone (outside the share id grammar).
TOMBSTONE_PREFIX = "deleted:"

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
        self._write_lock = threading.Lock()  # serialises _set / _clear / _delete
        self._lru_ok = True
        self._wal_retry_at = 0.0

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

    def _side_files(self) -> int:
        total = 0
        for name in ("cache.db-wal", "cache.db-shm"):
            try:
                total += os.path.getsize(self.directory / name)
            except OSError:
                pass
        return total

    def _wal_size(self) -> int:
        try:
            return os.path.getsize(self.directory / "cache.db-wal")
        except OSError:
            return 0

    def _used(self, cache: Any) -> int:
        """Files + index, the ``-shm`` file and the log's full budget.

        The budget is reserved up front, whatever the log weighs right now: it
        grows after the write (and after this check), so counting only its
        current size would let the total cross the limit. A reader pinning a
        snapshot stops SQLite from truncating the log; evicting entries (or
        clearing the cache) cannot shrink it, so the excess beyond the budget
        is left out rather than wiping the cache for nothing.
        """
        shm = self._side_files() - self._wal_size()
        return int(cache.volume()) + self.wal_budget() + shm

    def measured(self, cache: Any) -> int:
        """What is on disk now: files, index, log and shm (for tests and logs)."""
        return int(cache.volume()) + self._side_files()

    def _truncate_wal(self) -> None:
        """Checkpoint and truncate the log; back off while a reader blocks it."""
        if time.monotonic() < self._wal_retry_at:
            return
        try:
            con = sqlite3.connect(str(self.directory / "cache.db"), timeout=0.05)
            try:
                row = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            row = (1, 0, 0)
        if row is None or row[0] == 1:  # busy: a reader holds a snapshot
            self._wal_retry_at = time.monotonic() + WAL_BUSY_BACKOFF_S

    def _lru_key(self, cache: Any) -> str | None:
        row = cache._sql("SELECT key FROM Cache ORDER BY access_time LIMIT 1").fetchone()
        return None if row is None else row[0]

    def _evict_one(self, cache: Any) -> bool:
        """Drop the least recently used entry; False when none is left."""
        if self._lru_ok:
            try:
                victim = self._lru_key(cache)
            except Exception:  # noqa: BLE001 - internals changed after open
                self._lru_ok = False
                logger.warning("share cache LRU query failed, clearing", exc_info=True)
            else:
                if victim is None:
                    return False
                cache.delete(victim)
                return True
        if len(cache) == 0:
            return False
        cache.clear()
        return True

    async def _run(self, fn: Callable[..., T], *args: Any) -> T:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(fn, *args))

    # Blocking bodies -------------------------------------------------------

    @staticmethod
    def _tombstone(key: str) -> str:
        return f"{TOMBSTONE_PREFIX}{key}"

    def _get(self, key: str) -> CachedObject | None:
        # diskcache's return types are unions over its options: read them as Any.
        cache: Any = self._open()
        if cache.get(self._tombstone(key)) is not None:
            # Deleted, but the entry was left behind (a failed drop, then maybe
            # a restart): never served, and dropped now.
            try:
                with self._write_lock:
                    cache.delete(key)
            except Exception as exc:  # noqa: BLE001 - the tombstone still hides it
                logger.warning("Share cache could not drop a deleted entry: %s", type(exc).__name__)
            return None
        value, tag = cache.get(key, default=None, tag=True)
        if not isinstance(value, bytes) or not isinstance(tag, str):
            return None
        return CachedObject(content=value, content_type=tag)

    def _set(self, key: str, obj: CachedObject) -> None:
        cache: Any = self._open()
        need = len(obj.content) + INDEX_HEADROOM
        if need > self.size_limit - self.wal_budget():
            return  # would evict everything and still not fit
        with self._write_lock:
            if cache.get(self._tombstone(key)) is not None:
                return  # deleted by its author: never stored again
            cache.expire()  # automatic culling is off: drop expired entries here
            cache.delete(key)  # a rewrite frees the old copy first
            # Make room BEFORE writing, so the total never goes over the limit.
            while len(cache) >= self.max_entries and self._evict_one(cache):
                pass
            if self._side_files() > self.wal_budget():
                self._truncate_wal()
            while self._used(cache) + need > self.size_limit and self._evict_one(cache):
                pass
            cache.set(key, obj.content, expire=self.ttl_s, tag=obj.content_type)

    def _clear(self) -> int:
        cache = self._open()
        with self._write_lock:
            before = cache.volume()
            cache.clear()
            return max(0, before - cache.volume())

    def _delete(self, key: str) -> bool:
        cache: Any = self._open()
        with self._write_lock:
            # The tombstone first: if the drop below fails, it still hides the
            # entry, across a restart too, for as long as the entry can live.
            try:
                cache.set(self._tombstone(key), 1, expire=self.ttl_s)
            except Exception as exc:  # noqa: BLE001 - the drop below is still attempted
                logger.warning(
                    "Share cache could not write a deletion tombstone: %s", type(exc).__name__
                )
            return bool(cache.delete(key))

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

    async def delete(self, key: str) -> bool:
        """Drop one entry (an object its author deleted), leaving a tombstone; True when one was stored."""
        return await self._run(self._delete, key)

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
    """Empty and close the current cache, then use ``cache``.

    A closed cache whose directory exists is reopened to be emptied, so nothing
    (an entry, a tombstone) leaks into the next test.
    """
    global _cache
    old = _cache
    if old._cache is None and old.directory.is_dir():
        old._open()
    if old._cache is not None:
        old._cache.clear()
    old.close()
    _cache = cache or ShareCache()
