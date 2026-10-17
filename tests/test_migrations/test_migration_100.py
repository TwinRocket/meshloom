import json

import aiosqlite
import pytest

from app.migrations import _refuse_duplicate_numbers, run_migrations, set_version


async def _with_rows(conn: aiosqlite.Connection, rows: list[tuple[str, str, dict]]) -> None:
    await set_version(conn, 99)
    await conn.execute(
        "CREATE TABLE fanout_configs (id TEXT PRIMARY KEY, type TEXT NOT NULL, name TEXT NOT NULL,"
        " enabled INTEGER DEFAULT 0, config TEXT NOT NULL DEFAULT '{}',"
        " scope TEXT NOT NULL DEFAULT '{}', sort_order INTEGER DEFAULT 0,"
        " created_at INTEGER NOT NULL)"
    )
    for row_id, row_type, config in rows:
        await conn.execute(
            "INSERT INTO fanout_configs (id, type, name, config, created_at) VALUES (?, ?, ?, ?, 0)",
            (row_id, row_type, row_id, json.dumps(config)),
        )
    await conn.commit()


async def _config(conn: aiosqlite.Connection, row_id: str) -> dict:
    cursor = await conn.execute("SELECT config FROM fanout_configs WHERE id = ?", (row_id,))
    return json.loads((await cursor.fetchone())[0])


@pytest.mark.asyncio
async def test_existing_guarded_rows_are_grandfathered():
    conn = await aiosqlite.connect(":memory:")
    try:
        await _with_rows(
            conn,
            [
                ("w", "webhook", {"url": "http://192.168.1.5/hook"}),
                ("a", "apprise", {"urls": "json://192.168.1.5"}),
                ("s", "sqs", {"queue_url": "https://sqs.local/q", "endpoint_url": "http://lan"}),
                ("m", "map_upload", {"api_url": ""}),
            ],
        )
        await run_migrations(conn)
        for row_id in ("w", "a", "s", "m"):
            config = await _config(conn, row_id)
            assert config["allow_private_network"] is True
            assert config["private_network_grandfathered"] is True
        assert (await _config(conn, "w"))["url"] == "http://192.168.1.5/hook"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_other_types_and_rows_with_the_key_are_untouched():
    conn = await aiosqlite.connect(":memory:")
    try:
        await _with_rows(
            conn,
            [
                ("p", "mqtt_private", {"broker_host": "10.0.0.1"}),
                ("w", "webhook", {"url": "https://example.com", "allow_private_network": False}),
            ],
        )
        await run_migrations(conn)
        assert await _config(conn, "p") == {"broker_host": "10.0.0.1"}
        assert await _config(conn, "w") == {
            "url": "https://example.com",
            "allow_private_network": False,
        }
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_table_does_not_fail():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 99)
        await run_migrations(conn)
    finally:
        await conn.close()


def test_two_migrations_with_one_number_are_refused():
    with pytest.raises(RuntimeError, match="share number 100"):
        _refuse_duplicate_numbers(
            [(99, "_099_a"), (100, "_100_fanout_allow_private_network"), (100, "_100_other")]
        )
    _refuse_duplicate_numbers([(99, "_099_a"), (100, "_100_b")])
