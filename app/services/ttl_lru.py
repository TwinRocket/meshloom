"""Bounded TTL caches with LRU eviction after expired entries are purged."""

from __future__ import annotations

from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TtlLruCache(Generic[K, V]):
    """Map of key → value with a per-entry expiry and a hard size cap.

    Expired entries are dropped on read and before a write evicts a live entry.
    """

    def __init__(self, maxsize: int) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self.maxsize = maxsize
        self._data: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K, now: float) -> V | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= now:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: K, value: V, expires_at: float, now: float) -> None:
        self.purge_expired(now)
        self._data[key] = (expires_at, value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def pop(self, key: K) -> V | None:
        item = self._data.pop(key, None)
        return None if item is None else item[1]

    def purge_expired(self, now: float) -> int:
        expired = [key for key, (expires_at, _) in self._data.items() if expires_at <= now]
        for key in expired:
            del self._data[key]
        return len(expired)

    def clear(self) -> None:
        self._data.clear()

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data
