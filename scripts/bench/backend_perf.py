"""Synthetic-database benchmark for backend hot paths.

Builds (once, cached) a SQLite database with 300k messages, 500 contacts,
30 channels and 200k raw packets, then times:

- advert ingest (duplicate observations of an already-known contact's advert)
- ``GET /api/read-state/unreads`` (``MessageRepository.get_unread_counts``)
- GroupText ingest (decryptable packets and packets for unknown channels)

Usage (from the repo root)::

    uv run python scripts/bench/backend_perf.py [--workdir DIR] [--rebuild]

The script only uses public entry points so it can be run unchanged on two
revisions to compare before/after numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

N_MESSAGES = 300_000
N_CONTACTS = 500
N_CHANNELS = 30
N_RAW = 200_000
CHAN_SHARE = 0.8

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "websocket_events.json"


def _rng() -> random.Random:
    return random.Random(1234)


async def _create_schema(path: Path) -> None:
    from app.database import Database

    database = Database(str(path))
    await database.connect()
    await database.disconnect()


def _populate(path: Path, advert_pubkey: str, advert_name: str) -> list[str]:
    rnd = _rng()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA synchronous = OFF")
    now = int(time.time())

    names = [f"node-{i:04d}" for i in range(N_CONTACTS)]
    contacts = []
    for i, name in enumerate(names):
        pk = hashlib.sha256(f"contact-{i}".encode()).hexdigest()
        contacts.append(pk)
        conn.execute(
            "INSERT INTO contacts (public_key, name, type, last_seen, first_seen, last_read_at)"
            " VALUES (?, ?, 1, ?, ?, ?)",
            (pk, name, now, now - 86400 * 30, now - rnd.randint(0, 86400 * 10)),
        )
    # The advertiser used by the advert benchmark is already a known contact.
    conn.execute(
        "INSERT OR REPLACE INTO contacts (public_key, name, type, last_seen, first_seen)"
        " VALUES (?, ?, 1, ?, ?)",
        (advert_pubkey, advert_name, now, now - 86400),
    )

    channel_keys = []
    for i in range(N_CHANNELS):
        key = hashlib.sha256(f"channel-{i}".encode()).digest()[:16].hex().upper()
        channel_keys.append(key)
        conn.execute(
            "INSERT OR IGNORE INTO channels (key, name, is_hashtag, last_read_at, muted)"
            " VALUES (?, ?, 1, ?, ?)",
            (key, f"#chan{i}", now - rnd.randint(0, 86400 * 5), 1 if i % 10 == 9 else 0),
        )

    # Messages: oldest first, 30 days of history.
    rows = []
    start = now - 86400 * 30
    sender_pool = names + [f"stranger-{i}" for i in range(300)] + [advert_name]
    for i in range(N_MESSAGES):
        received = start + int(i * (86400 * 30) / N_MESSAGES)
        if rnd.random() < CHAN_SHARE:
            ck = channel_keys[rnd.randrange(N_CHANNELS)]
            sender = rnd.choice(sender_pool)
            sender_key = None if rnd.random() < 0.4 else contacts[rnd.randrange(N_CONTACTS)]
            text = f"{sender}: message {i} hello @[me]" if i % 97 == 0 else f"{sender}: msg {i}"
            rows.append(
                ("CHAN", ck, text, received, received, 0, sender, sender_key, rnd.random() < 0.05)
            )
        else:
            pk = contacts[rnd.randrange(N_CONTACTS // 5 * 4)]
            outgoing = rnd.random() < 0.4
            rows.append(("PRIV", pk, f"dm {i}", received, received, 0, None, pk, outgoing))
    conn.executemany(
        "INSERT INTO messages (type, conversation_key, text, sender_timestamp, received_at,"
        " txt_type, sender_name, sender_key, outgoing) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )

    raw_rows = []
    for i in range(N_RAW):
        data = bytes([0x15, 0x00]) + rnd.randbytes(rnd.randint(40, 120))
        raw_rows.append(
            (
                start + i * 10,
                data,
                (i + 1) if i % 2 == 0 and i < N_MESSAGES else None,
                hashlib.sha256(data[2:]).digest(),
            )
        )
    conn.executemany(
        "INSERT INTO raw_packets (timestamp, data, message_id, payload_hash) VALUES (?,?,?,?)",
        raw_rows,
    )
    conn.commit()
    conn.execute("ANALYZE")
    conn.close()
    return channel_keys


def _advert_fixture() -> tuple[bytes, str, str]:
    fixture = json.loads(FIXTURES.read_text())["advertisement_chat_node"]
    raw = bytes.fromhex(fixture["raw_packet_hex"])
    data = fixture["expected_ws_event"]["data"]
    return raw, data["public_key"], data["name"]


def _with_path(raw: bytes, hop: int) -> bytes:
    # header, path_len byte (1 hop, 1-byte hash), path, payload
    return bytes([raw[0], 0x01, hop]) + raw[2 + (raw[1] & 0x3F) :]


async def _time(fn, n: int) -> dict:
    samples = []
    for i in range(n):
        t0 = time.perf_counter()
        await fn(i)
        samples.append((time.perf_counter() - t0) * 1000)
    return {
        "n": n,
        "mean_ms": round(statistics.mean(samples), 3),
        "p50_ms": round(statistics.median(samples), 3),
        "max_ms": round(max(samples), 3),
    }


async def _run(db_path: Path) -> dict:
    os.environ["MESHCORE_DATABASE_PATH"] = str(db_path)
    import logging

    logging.disable(logging.INFO)
    from unittest.mock import patch

    import app.websocket as ws
    from app.database import db
    from app.decoder import encrypt_group_text
    from app.packet_processor import process_raw_packet
    from app.repository import ChannelRepository, MessageRepository

    db.db_path = str(db_path)
    await db.connect()
    results: dict = {}
    advert_raw, _pk, _name = _advert_fixture()
    with (
        patch.object(ws, "broadcast_event", lambda *a, **k: None),
        patch("app.packet_processor.broadcast_event", lambda *a, **k: None),
    ):
        # Warm up caches.
        await MessageRepository.get_unread_counts(name="me")
        await process_raw_packet(_with_path(advert_raw, 0))

        results["advert_duplicate"] = await _time(
            lambda i: process_raw_packet(_with_path(advert_raw, (i % 250) + 1)), 100
        )
        results["unreads"] = await _time(
            lambda i: MessageRepository.get_unread_counts(name="me"), 10
        )

        channels = await ChannelRepository.get_all()
        last = channels[-1]
        key = bytes.fromhex(last.key)
        base_ts = int(time.time())

        def gt_packet(i: int, k: bytes) -> bytes:
            payload = encrypt_group_text(k, base_ts + i, f"bench: hello {i}")
            return bytes([0x15, 0x00]) + payload

        results["grouptext_known_channel"] = await _time(
            lambda i: process_raw_packet(gt_packet(i, key), timestamp=base_ts + i), 200
        )
        unknown = hashlib.sha256(b"unknown").digest()[:16]
        with patch(
            "app.services.hashtag_catalogue.schedule_unknown_group_text_resolve",
            lambda *a, **k: None,
        ):
            results["grouptext_unknown_channel"] = await _time(
                lambda i: process_raw_packet(gt_packet(10_000 + i, unknown), timestamp=base_ts + i),
                200,
            )
    await db.disconnect()
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", default=os.path.join(tempfile.gettempdir(), "meshloom-bench"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    base = workdir / "base.db"
    _raw, pk, name = _advert_fixture()
    if args.rebuild or not base.exists():
        for suffix in ("", "-wal", "-shm"):
            Path(f"{base}{suffix}").unlink(missing_ok=True)
        asyncio.run(_create_schema(base))
        _populate(base, pk, name)
    run_db = workdir / "run.db"
    for suffix in ("", "-wal", "-shm"):
        Path(f"{run_db}{suffix}").unlink(missing_ok=True)
    shutil.copy(base, run_db)
    print(json.dumps(asyncio.run(_run(run_db)), indent=2))


if __name__ == "__main__":
    main()
