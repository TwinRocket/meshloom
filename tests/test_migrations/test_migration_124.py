"""Migration 124: Slack presets keep one ``webhook`` secret (MSHL-365)."""

import json

import aiosqlite
import pytest

from app.database import Database
from app.migrations import run_migrations, set_version

from .conftest import LATEST_SCHEMA_VERSION


async def _insert(conn, channel_id, config, secrets):
    await conn.execute(
        "INSERT INTO notification_channels (id, type, preset, name, enabled, language, config,"
        " secrets, allow_private_network, include_message_text, verify_tls, quiet_hours_exempt,"
        " sort_order, created_at) VALUES (?, 'apprise', 'slack', ?, 1, 'auto', ?, ?, 0,"
        " 'public_only', 1, 0, 0, 1)",
        (channel_id, channel_id, json.dumps(config), json.dumps(secrets)),
    )


@pytest.mark.asyncio
async def test_the_three_parts_are_joined_and_an_incomplete_channel_is_disabled(tmp_path):
    path = tmp_path / "a.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()
    conn = await aiosqlite.connect(path)
    try:
        await _insert(
            conn, "ok", {"token_a": "TDUMMY1", "token_b": "BDUMMY2"}, {"token_c": "dummysecret"}
        )
        await _insert(conn, "bad", {"token_a": "T/x", "token_b": "BDUMMY2"}, {"token_c": "zz"})
        await _insert(
            conn, "new", {}, {"webhook": "https://hooks.slack.com/services/a1b2c3/d4e5f6/g7h8i9"}
        )
        await set_version(conn, 123)
        await conn.commit()
        assert await run_migrations(conn) == LATEST_SCHEMA_VERSION - 123
        rows = {
            r[0]: r[1:]
            for r in await (
                await conn.execute("SELECT id, config, secrets, enabled FROM notification_channels")
            ).fetchall()
        }
        assert json.loads(rows["ok"][0]) == {}
        assert json.loads(rows["ok"][1]) == {
            "webhook": "https://hooks.slack.com/services/TDUMMY1/BDUMMY2/dummysecret"
        }
        assert rows["ok"][2] == 1
        assert json.loads(rows["bad"][0]) == {} and json.loads(rows["bad"][1]) == {}
        assert rows["bad"][2] == 0
        pending = await (
            await conn.execute(
                "SELECT value FROM notification_state WHERE key = 'secret_pending:bad'"
            )
        ).fetchone()
        assert json.loads(pending[0]) == ["webhook"]
        assert "webhook" in json.loads(rows["new"][1]) and rows["new"][2] == 1
    finally:
        await conn.close()
