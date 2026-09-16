"""Bundled MeshCore hashtag names.

The name list is the frontend CC0 snapshot
(``frontend/src/data/meshcoreChannels.snapshot.json``). Do not edit a second
copy — load that file so the backend and the finder cannot drift.
"""

from __future__ import annotations

import json
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

_REPO_SNAPSHOT = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "src"
    / "data"
    / "meshcoreChannels.snapshot.json"
)
# Docker / nFPM copy the same frontend file next to this module so packaged
# installs do not need frontend/src. Do not author a second name list here.
_PACKAGED_SNAPSHOT = Path(__file__).resolve().parent / "meshcoreChannels.snapshot.json"


def snapshot_path() -> Path:
    if _REPO_SNAPSHOT.is_file():
        return _REPO_SNAPSHOT
    return _PACKAGED_SNAPSHOT


def packaged_snapshot_path() -> Path:
    return _PACKAGED_SNAPSHOT


def hashtag_key_from_name(name: str) -> bytes:
    """``SHA256("#" + name)[:16]`` after stripping one leading ``#``."""
    text = (name or "").strip()
    if text.startswith("#"):
        text = text[1:]
    return sha256(f"#{text}".encode()).digest()[:16]


def channel_key_hash_byte(key: bytes) -> str:
    """Lowercase two-hex first byte of ``SHA256(key)``."""
    return sha256(key).digest()[:1].hex()


def hashtag_hash_byte_from_name(name: str) -> str:
    return channel_key_hash_byte(hashtag_key_from_name(name))


@lru_cache(maxsize=1)
def bundled_hashtag_names() -> tuple[str, ...]:
    path = snapshot_path()
    if not path.is_file():
        return ()
    payload = json.loads(path.read_text(encoding="utf-8"))
    names = payload.get("names")
    if not isinstance(names, list):
        return ()
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        if not isinstance(raw, str):
            continue
        name = raw.strip()
        if not name or name.startswith("#") or name in seen:
            continue
        seen.add(name)
        cleaned.append(name)
    return tuple(cleaned)


@lru_cache(maxsize=1)
def bundled_names_by_hash_byte() -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[str]] = {}
    for name in bundled_hashtag_names():
        hb = hashtag_hash_byte_from_name(name)
        buckets.setdefault(hb, []).append(name)
    return {key: tuple(values) for key, values in buckets.items()}
