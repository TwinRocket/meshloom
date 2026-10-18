import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


@pytest.mark.asyncio
async def test_adds_push_allow_private_endpoints_off_for_an_existing_row():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 96)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("SELECT push_allow_private_endpoints FROM app_settings")
        assert (await cursor.fetchone())[0] == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_is_idempotent_and_keeps_an_existing_value():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 96)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " push_allow_private_endpoints INTEGER DEFAULT 0)"
        )
        await conn.execute(
            "INSERT INTO app_settings (id, push_allow_private_endpoints) VALUES (1, 1)"
        )
        await conn.commit()
        await run_migrations(conn)
        cursor = await conn.execute("PRAGMA table_info(app_settings)")
        names = [row[1] for row in await cursor.fetchall()]
        assert names.count("push_allow_private_endpoints") == 1
        cursor = await conn.execute("SELECT push_allow_private_endpoints FROM app_settings")
        assert (await cursor.fetchone())[0] == 1
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_missing_app_settings_table_does_not_fail():
    conn = await aiosqlite.connect(":memory:")
    try:
        await set_version(conn, 96)
        await run_migrations(conn)
    finally:
        await conn.close()


async def _with_subscriptions(conn: aiosqlite.Connection, endpoints: list[str]) -> None:
    await set_version(conn, 96)
    await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
    await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
    await conn.execute("CREATE TABLE push_subscriptions (id TEXT PRIMARY KEY, endpoint TEXT)")
    for i, endpoint in enumerate(endpoints):
        await conn.execute(
            "INSERT INTO push_subscriptions (id, endpoint) VALUES (?, ?)", (str(i), endpoint)
        )
    await conn.commit()
    await run_migrations(conn)


async def _flags(conn: aiosqlite.Connection) -> tuple[int, int]:
    cursor = await conn.execute(
        "SELECT push_allow_private_endpoints, push_private_endpoints_grandfathered"
        " FROM app_settings"
    )
    row = await cursor.fetchone()
    return row[0], row[1]


@pytest.mark.asyncio
async def test_browser_push_services_only_start_off():
    conn = await aiosqlite.connect(":memory:")
    try:
        await _with_subscriptions(
            conn,
            [
                "https://fcm.googleapis.com/fcm/send/abc",
                "https://updates.push.services.mozilla.com/wpush/v2/abc",
                "https://wns2-par02p.notify.windows.com/w/?token=abc",
                "https://web.push.apple.com/abc",
            ],
        )
        assert await _flags(conn) == (0, 0)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://192.168.1.20:8080/UP?token=abc",
        "https://ntfy.home.lan/upabc",
        "http://unifiedpush/up",
    ],
)
async def test_another_endpoint_grandfathers_the_opt_in(endpoint):
    conn = await aiosqlite.connect(":memory:")
    try:
        await _with_subscriptions(conn, ["https://fcm.googleapis.com/fcm/send/abc", endpoint])
        assert await _flags(conn) == (1, 1)
    finally:
        await conn.close()
