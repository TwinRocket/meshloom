"""Feed filters on the messages listing: outgoing, since_id, query plans, timing."""

import json
import time
from pathlib import Path

import pytest

from app.repository import MessageRepository

CHAN_KEY = "ABC123DEF456ABC123DEF456ABC12345"
DM_KEY = "aa" * 32


async def _create(text, *, received_at, outgoing=False, msg_type="CHAN", key=None):
    msg_id = await MessageRepository.create(
        msg_type=msg_type,
        text=text,
        conversation_key=key or (CHAN_KEY if msg_type == "CHAN" else DM_KEY),
        sender_timestamp=received_at,
        received_at=received_at,
        outgoing=outgoing,
    )
    assert msg_id is not None
    return msg_id


@pytest.mark.asyncio
async def test_outgoing_filter_alone_and_combined(test_db):
    a = await _create("a", received_at=100, outgoing=True)
    b = await _create("b", received_at=110)
    c = await _create("c", received_at=120, outgoing=True, msg_type="PRIV")
    d = await _create("needle d", received_at=130, outgoing=True)

    assert {m.id for m in await MessageRepository.get_all(outgoing=True)} == {a, c, d}
    assert {m.id for m in await MessageRepository.get_all(outgoing=False)} == {b}
    assert len(await MessageRepository.get_all()) == 4
    got = await MessageRepository.get_all(outgoing=True, msg_type="CHAN")
    assert [m.id for m in got] == [d, a]
    got = await MessageRepository.get_all(outgoing=True, q="needle")
    assert [m.id for m in got] == [d]
    got = await MessageRepository.get_all(outgoing=True, before=130, before_id=d)
    assert [m.id for m in got] == [c, a]
    got = await MessageRepository.get_all(outgoing=True, after=100, after_id=a)
    assert [m.id for m in got] == [c, d]


@pytest.mark.asyncio
async def test_since_id_returns_historical_rows_in_id_order(test_db):
    first = await _create("first", received_at=1000)
    # Historical decryption: old received_at, new id.
    old = await _create("old", received_at=10)
    newer = await _create("newer", received_at=2000, outgoing=True)

    got = await MessageRepository.get_all(since_id=first)
    assert [m.id for m in got] == [old, newer]
    # The (received_at, id) cursor misses the historical row.
    fwd = await MessageRepository.get_all(after=1000, after_id=first)
    assert old not in {m.id for m in fwd}

    assert [m.id for m in await MessageRepository.get_all(since_id=first, outgoing=True)] == [newer]
    assert [m.id for m in await MessageRepository.get_all(since_id=first, limit=1)] == [old]
    assert await MessageRepository.get_all(since_id=newer) == []


@pytest.mark.asyncio
async def test_since_id_applies_blocked_list(test_db):
    await _create("seed", received_at=1)
    await MessageRepository.create(
        msg_type="PRIV",
        text="spam",
        conversation_key="bb" * 32,
        sender_timestamp=5,
        received_at=5,
        sender_key="bb" * 32,
    )
    got = await MessageRepository.get_all(since_id=1, blocked_keys=["bb" * 32])
    assert got == []


@pytest.mark.asyncio
async def test_conversation_key_prefix_is_escaped(test_db):
    await _create("x", received_at=1, msg_type="PRIV", key="ab" + "c" * 62)
    # '_' and '%' must match literally, not as wildcards.
    assert await MessageRepository.get_all(conversation_key="a_") == []
    assert await MessageRepository.get_all(conversation_key="%") == []
    assert len(await MessageRepository.get_all(conversation_key="abc")) == 1
    assert len(await MessageRepository.get_all(conversation_key="ab")) == 1


@pytest.mark.asyncio
async def test_since_id_api_validation(test_db, client):
    for qs in (
        "since_id=0",
        "since_id=-3",
        "since_id=99999999999999999999",
        f"since_id={2**63}",
        "since_id=1&before=5&before_id=2",
        "since_id=1&after=5&after_id=2",
        "since_id=1&offset=10",
    ):
        resp = await client.get(f"/api/messages?{qs}")
        assert resp.status_code == 400, qs
        assert "since_id" in resp.json()["detail"]
    resp = await client.get("/api/messages?since_id=abc")
    assert resp.status_code == 422
    assert (await client.get(f"/api/messages?since_id={2**63 - 1}")).status_code == 200
    assert (await client.get("/api/messages?since_id=1&offset=0")).status_code == 200


@pytest.mark.asyncio
async def test_outgoing_api(test_db, client):
    await _create("in", received_at=1)
    await _create("out", received_at=2, outgoing=True)
    resp = await client.get("/api/messages?outgoing=true")
    assert [m["text"] for m in resp.json()] == ["out"]
    resp = await client.get("/api/messages?outgoing=false")
    assert [m["text"] for m in resp.json()] == ["in"]
    assert len((await client.get("/api/messages")).json()) == 2
    assert (await client.get("/api/messages?outgoing=maybe")).status_code == 422


async def _plan(db, sql, params):
    async with db.readonly() as conn:
        async with conn.execute("EXPLAIN QUERY PLAN " + sql, params) as cur:
            return " | ".join(r[-1] for r in await cur.fetchall())


async def _listing_sql(db, **kw):
    """Capture the SQL get_all would run, without executing it."""
    captured = {}
    real = db.readonly

    class _Spy:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            captured["sql"], captured["params"] = sql, list(params)
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    class _Ctx:
        async def __aenter__(self):
            self.inner = real()
            return _Spy(await self.inner.__aenter__())

        async def __aexit__(self, *a):
            return await self.inner.__aexit__(*a)

    db.readonly = lambda: _Ctx()
    try:
        await MessageRepository.get_all(**kw)
    finally:
        del db.readonly
    return captured["sql"], captured["params"]


def _plan_cases():
    """Every server-side combination the feed sends: type x outgoing x cursor (+ blocked)."""
    cases = {}
    for mtype in (None, "CHAN", "PRIV"):
        for outgoing in (None, True, False):
            for cursor in ("first", "before", "since_id"):
                kw: dict = {"limit": 200}
                if mtype:
                    kw["msg_type"] = mtype
                if outgoing is not None:
                    kw["outgoing"] = outgoing
                if cursor == "before":
                    kw.update(before=1_700_000_000, before_id=500)
                elif cursor == "since_id":
                    kw["since_id"] = 1
                name = f"{mtype or 'any'}-{'any' if outgoing is None else outgoing}-{cursor}"
                cases[name] = kw
                if cursor == "first":
                    # The route always passes the blocked lists when the user has some.
                    cases[name + "-blocked"] = {
                        **kw,
                        "blocked_keys": ["ab" * 32],
                        "blocked_names": ["spam"],
                    }
    return cases


_PLAN_CASES = _plan_cases()


async def _seed_mixed(db, count: int = 3000):
    """A table the planner has something to choose on: both types, 10 % outgoing, 20 chats."""
    async with db.tx() as conn:
        await conn.executemany(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing, "
            "sender_timestamp) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    "CHAN" if i % 3 else "PRIV",
                    f"{i % 20:032x}",
                    f"m{i}",
                    1_700_000_000 + i,
                    1 if i % 10 == 0 else 0,
                    i,
                )
                for i in range(1, count + 1)
            ],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", list(_PLAN_CASES.values()), ids=list(_PLAN_CASES))
async def test_listing_plan_uses_index_without_temp_sort(test_db, kw):
    await _seed_mixed(test_db)
    sql, params = await _listing_sql(test_db, **kw)
    plan = await _plan(test_db, sql, params)
    assert "USING INDEX" in plan or "USING COVERING INDEX" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


@pytest.mark.slow
@pytest.mark.asyncio
async def test_sparse_outgoing_first_page_is_fast(test_db):
    async with test_db.tx() as conn:
        await conn.executemany(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing, "
            "sender_timestamp) VALUES ('CHAN', ?, ?, ?, ?, ?)",
            [(CHAN_KEY, f"m{i}", i, 1 if i % 100 == 0 else 0, i) for i in range(1, 50_001)],
        )
    for kw in ({"outgoing": True}, {"msg_type": "CHAN", "outgoing": True}):
        t0 = time.perf_counter()
        rows = await MessageRepository.get_all(limit=50, **kw)
        elapsed = time.perf_counter() - t0
        print(f"sparse outgoing first page {kw}: {elapsed * 1000:.1f} ms")
        assert len(rows) == 50
        assert elapsed < 0.5


_FEED_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "src"
    / "test"
    / "fixtures"
    / "feedFilters.json"
)


@pytest.mark.asyncio
async def test_server_side_pair_matches_the_frontend_filter_fixture(test_db, client):
    """`type` and `outgoing` are evaluated by the server and by `rowPassesFilters`.

    The vitest suite asserts every case of the fixture on the client. Here the cases the
    server evaluates (no mute or pending involved) are replayed against the route with
    the query the client would send: the row is returned exactly when the case expects it.
    """
    fixture = json.loads(_FEED_FIXTURE.read_text())
    cases = [c for c in fixture["cases"] if c["server"]]
    assert cases, "the fixture must hold server-evaluated cases"

    ids = {}
    for index, case in enumerate(cases):
        row = case["row"]
        key = fixture["dmKey"]
        if row["type"] == "CHAN":
            key = (
                fixture["channels"]
                .get(row.get("channel", "public"), {})
                .get("key", fixture["deletedChannelKey"])
            )
        ids[case["name"]] = await MessageRepository.create(
            msg_type=row["type"],
            text=f"case {index}",
            conversation_key=key,
            sender_timestamp=1000 + index,
            received_at=1000 + index,
            outgoing=row["outgoing"],
        )

    for case in cases:
        if case["query"] is None:
            # An empty selection sends no request: nothing is shown.
            assert case["expected"] is False, case["name"]
            continue
        params = {
            k: (str(v).lower() if isinstance(v, bool) else v) for k, v in case["query"].items()
        }
        resp = await client.get("/api/messages", params={"limit": 1000, **params})
        assert resp.status_code == 200
        returned = {m["id"] for m in resp.json()}
        assert (ids[case["name"]] in returned) is case["expected"], case["name"]
