"""``app/services/share_cache.py``: the bounded disk cache of shared objects."""

from __future__ import annotations

import stat
from pathlib import Path

from app.services import share_cache
from app.services.share_cache import CachedObject, ShareCache


async def test_round_trip_and_clear(tmp_path):
    cache = ShareCache(tmp_path / "share-cache")
    try:
        assert await cache.get("AbCdEfG") is None
        await cache.set("AbCdEfG", CachedObject(b"\x89PNG" + b"x" * 1000, "image/png"))
        hit = await cache.get("AbCdEfG")
        assert hit == CachedObject(b"\x89PNG" + b"x" * 1000, "image/png")
        freed = await cache.clear()
        assert freed > 0
        assert await cache.get("AbCdEfG") is None
        assert await cache.count() == 0
    finally:
        cache.close()


async def test_directory_is_private_and_created_on_first_use(tmp_path):
    directory = tmp_path / "data" / "share-cache"
    cache = ShareCache(directory)
    try:
        assert not directory.exists()
        await cache.set("AbCdEfG", CachedObject(b"x", "image/png"))
        assert directory.is_dir()
        assert stat.S_IMODE(directory.stat().st_mode) & 0o077 == 0
    finally:
        cache.close()


async def test_size_cap_is_honoured(tmp_path):
    limit = 256 * 1024
    cache = ShareCache(tmp_path / "c", size_limit=limit)
    try:
        for i in range(40):
            await cache.set(f"Obj{i:04d}", CachedObject(bytes([i]) * 32 * 1024, "image/png"))
            assert await cache.volume() <= limit
        # The oldest were evicted, the newest kept.
        assert await cache.get("Obj0000") is None
        assert await cache.get("Obj0039") is not None
    finally:
        cache.close()


async def test_size_cap_holds_with_mixed_sizes(tmp_path):
    """Many small objects then large ones: diskcache alone would overshoot."""
    limit = 1_000_000
    cache = ShareCache(tmp_path / "c", size_limit=limit)
    try:
        for i in range(500):
            await cache.set(f"Sml{i:04d}", CachedObject(b"x" * 100, "image/png"))
            assert await cache.volume() <= limit
        for i in range(30):
            await cache.set(f"Big{i:04d}", CachedObject(b"y" * 200_000, "image/png"))
            assert await cache.volume() <= limit
        # The newest large object stays; the oldest went first.
        assert await cache.get("Big0029") is not None
        assert await cache.get("Big0000") is None
        assert await cache.get("Sml0000") is None
    finally:
        cache.close()


async def test_eviction_follows_recent_use(tmp_path):
    cache = ShareCache(tmp_path / "c", size_limit=500_000)
    try:
        # Two fit with the index, the shm file and the log budget, a third does not.
        await cache.set("Old0001", CachedObject(b"a" * 150_000, "image/png"))
        await cache.set("Old0002", CachedObject(b"b" * 150_000, "image/png"))
        assert await cache.get("Old0001") is not None  # touched: now most recent
        await cache.set("New0003", CachedObject(b"c" * 150_000, "image/png"))
        assert await cache.get("Old0002") is None
        assert await cache.get("Old0001") is not None
        assert await cache.get("New0003") is not None
    finally:
        cache.close()


def test_ttl_is_one_hour():
    assert share_cache.TTL_S == 3600


async def test_entry_cap_drops_the_least_recently_used(tmp_path):
    cache = ShareCache(tmp_path / "c", max_entries=3)
    try:
        for i in range(3):
            await cache.set(f"Obj{i:04d}", CachedObject(b"x", "image/png"))
        assert await cache.get("Obj0000") is not None  # oldest inserted, now most recent
        for i in range(3, 5):
            await cache.set(f"Obj{i:04d}", CachedObject(b"x", "image/png"))
        assert await cache.count() == 3
        assert await cache.get("Obj0000") is not None
        assert await cache.get("Obj0001") is None
        assert await cache.get("Obj0002") is None
        assert await cache.get("Obj0004") is not None
    finally:
        cache.close()


async def test_entries_expire(tmp_path, monkeypatch):
    import time

    now = [1000.0]
    monkeypatch.setattr(time, "time", lambda: now[0])
    cache = ShareCache(tmp_path / "c", ttl_s=60)
    try:
        await cache.set("AbCdEfG", CachedObject(b"x", "image/png"))
        assert await cache.get("AbCdEfG") is not None
        now[0] += 61
        assert await cache.get("AbCdEfG") is None
    finally:
        cache.close()


def test_default_directory_is_next_to_the_database(monkeypatch, tmp_path):
    """Under the packages' ReadWritePaths: /var/lib/meshloom/share-cache."""
    from app.config import settings

    monkeypatch.setattr(settings, "database_path", str(tmp_path / "meshcore.db"))
    assert share_cache.default_directory() == tmp_path.resolve() / "share-cache"
    monkeypatch.setattr(settings, "database_path", "/var/lib/meshloom/meshcore.db")
    expected = Path("/var/lib/meshloom").resolve() / "share-cache"
    assert share_cache.default_directory() == expected


def test_package_cache_directory_is_writable_by_the_service(monkeypatch):
    """The packaged env file and unit: the cache lands inside ReadWritePaths."""
    import re

    from app.config import settings

    pkg = Path(__file__).resolve().parent.parent / "pkg" / "nfpm"
    env = (pkg / "meshloom.env").read_text(encoding="utf-8")
    database = re.search(r"^MESHCORE_DATABASE_PATH=(.+)$", env, re.M)
    assert database is not None
    unit = (pkg / "meshloom.service").read_text(encoding="utf-8")
    writable = re.search(r"^ReadWritePaths=(.+)$", unit, re.M)
    assert writable is not None
    roots = [p.lstrip("-") for p in writable.group(1).split()]
    monkeypatch.setattr(settings, "database_path", database.group(1).strip())
    directory = share_cache.default_directory()
    assert any(directory.is_relative_to(Path(root).resolve()) for root in roots)


async def test_concurrent_fills_never_exceed_the_cap(tmp_path):
    import asyncio

    limit = 600_000
    cache = ShareCache(tmp_path / "c", size_limit=limit)
    peak = 0

    async def watch(stop: asyncio.Event) -> None:
        nonlocal peak
        while not stop.is_set():
            if cache.opened:
                peak = max(peak, cache.measured(cache._open()))
            await asyncio.sleep(0)

    try:
        stop = asyncio.Event()
        watcher = asyncio.create_task(watch(stop))
        await asyncio.gather(
            *(
                cache.set(f"Con{i:04d}", CachedObject(bytes([i]) * 150_000, "image/png"))
                for i in range(24)
            )
        )
        stop.set()
        await watcher
        assert peak <= limit
        assert cache.measured(cache._open()) <= limit
        assert await cache.count() >= 1
    finally:
        cache.close()


async def test_oversized_object_is_not_stored(tmp_path):
    cache = ShareCache(tmp_path / "c", size_limit=100_000)
    try:
        await cache.set("Keep001", CachedObject(b"k" * 1000, "image/png"))
        await cache.set("Huge001", CachedObject(b"h" * 200_000, "image/png"))
        assert await cache.get("Huge001") is None
        assert await cache.get("Keep001") is not None
    finally:
        cache.close()


async def test_wal_is_bounded_and_counted(tmp_path):
    limit = 2_000_000
    cache = ShareCache(tmp_path / "c", size_limit=limit)
    try:
        for i in range(200):
            await cache.set(f"Wal{i:04d}", CachedObject(bytes([i % 256]) * 20_000, "image/png"))
            assert cache._wal_size() <= cache.wal_budget() * 2
            assert await cache.volume() + cache._wal_size() <= limit
    finally:
        cache.close()


async def test_fallback_clears_then_stores_the_new_object(tmp_path, monkeypatch):
    monkeypatch.setattr(ShareCache, "_self_check", staticmethod(lambda cache: False))
    cache = ShareCache(tmp_path / "c", size_limit=300_000)
    try:
        await cache.set("Fb00000", CachedObject(b"x" * 100_000, "image/png"))
        await cache.set("Fb00001", CachedObject(b"x" * 100_000, "image/png"))
        # 200 kB held, 300 kB cap with the log budget: the third does not fit.
        await cache.set("Fb00002", CachedObject(b"x" * 100_000, "image/png"))
        assert await cache.get("Fb00002") is not None
        assert await cache.get("Fb00000") is None
        assert await cache.count() == 1
    finally:
        cache.close()


async def test_lru_query_failing_after_open_falls_back(tmp_path, monkeypatch, caplog):
    import logging

    cache = ShareCache(tmp_path / "c", size_limit=300_000)
    try:
        await cache.set("Rt00000", CachedObject(b"x" * 100_000, "image/png"))
        await cache.set("Rt00001", CachedObject(b"x" * 100_000, "image/png"))

        def boom(self, c):
            raise RuntimeError("schema changed")

        monkeypatch.setattr(ShareCache, "_lru_key", boom)
        with caplog.at_level(logging.WARNING):
            await cache.set("Rt00002", CachedObject(b"x" * 100_000, "image/png"))
        assert cache._lru_ok is False
        assert "LRU query failed" in caplog.text
        assert await cache.get("Rt00002") is not None
        assert await cache.get("Rt00000") is None
    finally:
        cache.close()


async def test_reader_pinning_the_log_does_not_wipe_the_cache(tmp_path):
    import sqlite3
    import time

    limit = 2_000_000
    cache = ShareCache(tmp_path / "c", size_limit=limit)
    reader = None
    try:
        for i in range(10):
            await cache.set(f"A{i:03d}", CachedObject(bytes([i]) * 50_000, "image/png"))
        reader = sqlite3.connect(str(tmp_path / "c" / "cache.db"), isolation_level=None)
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM Cache").fetchone()  # pinned snapshot
        start = time.monotonic()
        for i in range(150):
            await cache.set(f"B{i:03d}", CachedObject(bytes([i % 256]) * 2_000, "image/png"))
        # Checkpoints back off: no 1-2 s stall per write, and the cache survives.
        assert time.monotonic() - start < 20
        assert await cache.count() >= 100
        assert await cache.get("B149") is not None
    finally:
        if reader is not None:
            reader.execute("COMMIT")
            reader.close()
        cache.close()


def test_self_check_passes_on_the_pinned_diskcache(tmp_path):
    cache = ShareCache(tmp_path / "c")
    try:
        cache._open()
        assert cache._lru_ok is True
    finally:
        cache.close()
