import aiosqlite
import pytest

from app.migrations import run_migrations, set_version


async def _upgrade(conn: aiosqlite.Connection, endpoints: list[str], flags=(None, None)) -> None:
    """A database at version 103; flags=(allow, grandfathered) preset the 097 outcome
    (None: let 097 decide, which is what a real upgrade from 96 does)."""
    if flags[0] is None:
        await set_version(conn, 96)
        await conn.execute("CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1))")
        await conn.execute("INSERT INTO app_settings (id) VALUES (1)")
    else:
        await set_version(conn, 103)
        await conn.execute(
            "CREATE TABLE app_settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
            " push_allow_private_endpoints INTEGER DEFAULT 0,"
            " push_private_endpoints_grandfathered INTEGER DEFAULT 0)"
        )
        await conn.execute("INSERT INTO app_settings VALUES (1, ?, ?)", flags)
    await conn.execute("CREATE TABLE push_subscriptions (id TEXT PRIMARY KEY, endpoint TEXT)")
    for i, endpoint in enumerate(endpoints):
        await conn.execute("INSERT INTO push_subscriptions VALUES (?, ?)", (str(i), endpoint))
    await conn.commit()
    await run_migrations(conn)


async def _review(conn: aiosqlite.Connection) -> int:
    cursor = await conn.execute("SELECT push_private_endpoints_review FROM app_settings")
    return (await cursor.fetchone())[0]


async def _flags(conn: aiosqlite.Connection) -> tuple[int, int]:
    cursor = await conn.execute(
        "SELECT push_allow_private_endpoints, push_private_endpoints_grandfathered"
        " FROM app_settings"
    )
    row = await cursor.fetchone()
    return row[0], row[1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "https://ntfy.sh/upabc",
        "https://fcm.googleapis.com.evil.example/x",
        "https://up.example.org:8443/UP",
    ],
)
async def test_public_distributor_is_no_longer_grandfathered(endpoint):
    conn = await aiosqlite.connect(":memory:")
    try:
        await _upgrade(conn, [endpoint])
        assert await _flags(conn) == (0, 0)
        # Already shipped 097 left (1, 1): 104 withdraws it.
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_withdraws_a_097_grandfathering_of_a_public_endpoint():
    conn = await aiosqlite.connect(":memory:")
    try:
        await _upgrade(conn, ["https://ntfy.sh/upabc"], flags=(1, 1))
        assert await _flags(conn) == (0, 0)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    [
        "http://192.168.1.20:8080/UP",
        "http://[fd00::1]/UP",
        "http://100.64.0.5/UP",
        "https://ntfy.home.lan/up",
        "https://up.local/up",
        "http://unifiedpush/up",
    ],
)
async def test_private_endpoint_stays_grandfathered(endpoint):
    conn = await aiosqlite.connect(":memory:")
    try:
        await _upgrade(conn, ["https://ntfy.sh/upabc", endpoint], flags=(1, 1))
        assert await _flags(conn) == (1, 1)
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_a_choice_the_user_made_is_kept():
    conn = await aiosqlite.connect(":memory:")
    try:
        # Notice dismissed or box ticked by hand: grandfathered flag is 0.
        await _upgrade(conn, ["https://ntfy.sh/upabc"], flags=(1, 0))
        assert await _flags(conn) == (1, 0)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "review"),
    [
        ("https://ntfy.tail1234.ts.net/up", 1),
        ("https://ntfy.example.com/up", 1),
        ("https://8.8.8.8/up", 0),
    ],
)
async def test_withdrawing_for_a_hostname_sets_the_review_notice(endpoint, review):
    conn = await aiosqlite.connect(":memory:")
    try:
        await _upgrade(conn, [endpoint], flags=(1, 1))
        assert await _flags(conn) == (0, 0)
        assert await _review(conn) == review
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_no_review_notice_when_the_opt_in_stays():
    conn = await aiosqlite.connect(":memory:")
    try:
        await _upgrade(conn, ["https://ntfy.tail1234.ts.net/up", "http://10.0.0.5/up"], (1, 1))
        assert await _flags(conn) == (1, 1)
        assert await _review(conn) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_browser_push_services_only_set_no_notice():
    conn = await aiosqlite.connect(":memory:")
    try:
        await _upgrade(conn, ["https://fcm.googleapis.com/fcm/send/abc"])
        assert await _flags(conn) == (0, 0)
        assert await _review(conn) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint",
    ["http://[::ffff:192.168.1.2]/up", "http://100.64.0.5/up", "http://[fe80::1%25eth0]/up"],
)
async def test_listed_private_networks(endpoint):
    conn = await aiosqlite.connect(":memory:")
    try:
        await _upgrade(conn, [endpoint], flags=(1, 1))
        assert await _flags(conn) == (1, 1)
    finally:
        await conn.close()
