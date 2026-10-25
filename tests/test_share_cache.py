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
            assert await cache.volume() <= limit + 64 * 1024  # SQLite index overhead
        # The oldest were evicted, the newest kept.
        assert await cache.get("Obj0000") is None
        assert await cache.get("Obj0039") is not None
    finally:
        cache.close()


async def test_entry_cap_drops_the_oldest(tmp_path):
    cache = ShareCache(tmp_path / "c", max_entries=3)
    try:
        for i in range(5):
            await cache.set(f"Obj{i:04d}", CachedObject(b"x", "image/png"))
        assert await cache.count() == 3
        assert await cache.get("Obj0000") is None
        assert await cache.get("Obj0001") is None
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
