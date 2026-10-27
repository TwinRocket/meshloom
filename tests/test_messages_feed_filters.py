"""Feed filters on the messages listing: outgoing, since_id, query plans, timing."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.repository.messages as messages_module
from app.repository import MessageRepository
from app.repository.channels import effective_channel_muted, effective_mute_sql
from app.repository.messages import MAX_CONVERSATION_KEYS, normalize_conversation_keys

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


async def _insert_channel(key, *, muted=0, muted_until=None, membership="adopted"):
    async with messages_module.db.tx() as conn:
        await conn.execute(
            "INSERT INTO channels (key, name, muted, muted_until, membership) VALUES (?, ?, ?, ?, ?)",
            (key, f"#{key[:4]}", muted, muted_until, membership),
        )


def _frozen_clock(monkeypatch, now):
    """The route reads ``time.time()`` once per request: give it a fixed clock."""
    import app.routers.messages as router

    monkeypatch.setattr(router, "time", SimpleNamespace(time=lambda: now))


@pytest.mark.asyncio
async def test_server_matches_the_frontend_filter_fixture(test_db, client, monkeypatch):
    """The route and `rowPassesFilters` answer the same question (type, outgoing, muted,
    membership, conversation pick).

    The vitest suite asserts every case of the fixture on the client and the query
    `filtersToQuery` builds. Here the same query is replayed against the route, with the
    fixture's channels in the table and its clock: the row is returned exactly when the case
    expects it. Cases whose pick the server cannot take (`server: false`) are client-only.
    """
    fixture = json.loads(_FEED_FIXTURE.read_text())
    _frozen_clock(monkeypatch, fixture["now"])
    for channel in fixture["channels"].values():
        await _insert_channel(
            channel["key"],
            muted=1 if channel["muted"] else 0,
            muted_until=channel["muted_until"],
            membership=channel["membership"],
        )
    cases = [c for c in fixture["cases"] if c["server"]]
    assert any(c.get("serverParams") for c in cases)
    assert len(cases) >= 40, "the fixture must hold server-evaluated cases"
    # Both outcomes and both muted/membership values must be exercised.
    assert {c["expected"] for c in cases} == {True, False}
    assert any(c.get("serverParams", {}).get("muted") == "exclude" for c in cases)
    assert any(c.get("serverParams", {}).get("membership") == "adopted" for c in cases)
    assert any(c.get("serverParams", {}).get("conversation_keys") for c in cases)

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
        # What the client sends, plus what the route also takes for the same question.
        sent = {**case["query"], **case.get("serverParams", {})}
        params = {k: (str(v).lower() if isinstance(v, bool) else v) for k, v in sent.items()}
        resp = await client.get("/api/messages", params={"limit": 1000, **params})
        assert resp.status_code == 200, case["name"]
        returned = {m["id"] for m in resp.json()}
        assert (ids[case["name"]] in returned) is case["expected"], case["name"]


# --- muted / membership / conversation_keys -------------------------------------------------

NOW = 1_700_000_000
K_PLAIN = "A1" * 16
K_FOREVER = "B2" * 16
K_EXPIRED = "C3" * 16
K_FUTURE = "D4" * 16
K_EDGE = "E5" * 16  # muted_until == now: the mute has just ended
K_PENDING = "F6" * 16
K_PENDING_MUTED = "07" * 16
K_DELETED = "18" * 16  # messages, no channels row
K_STALE_FLAG = "29" * 16  # muted = 0 with a leftover muted_until in the future
DM = "ab" * 32


async def _mute_world():
    """One CHAN message per channel case, one PRIV message, one for a deleted channel."""
    await _insert_channel(K_PLAIN)
    await _insert_channel(K_FOREVER, muted=1)
    await _insert_channel(K_EXPIRED, muted=1, muted_until=NOW - 60)
    await _insert_channel(K_FUTURE, muted=1, muted_until=NOW + 3600)
    await _insert_channel(K_EDGE, muted=1, muted_until=NOW)
    await _insert_channel(K_PENDING, membership="pending")
    await _insert_channel(K_PENDING_MUTED, muted=1, membership="pending")
    await _insert_channel(K_STALE_FLAG, muted=0, muted_until=NOW + 3600)
    ids = {}
    for index, (name, key, msg_type) in enumerate(
        [
            ("plain", K_PLAIN, "CHAN"),
            ("forever", K_FOREVER, "CHAN"),
            ("expired", K_EXPIRED, "CHAN"),
            ("future", K_FUTURE, "CHAN"),
            ("edge", K_EDGE, "CHAN"),
            ("pending", K_PENDING, "CHAN"),
            ("pending_muted", K_PENDING_MUTED, "CHAN"),
            ("stale_flag", K_STALE_FLAG, "CHAN"),
            ("deleted", K_DELETED, "CHAN"),
            ("dm", DM, "PRIV"),
        ]
    ):
        ids[name] = await _create(name, received_at=100 + index, msg_type=msg_type, key=key)
    return ids


async def _names(**kw):
    kw.setdefault("now", NOW)
    kw.setdefault("limit", 100)
    return {m.text for m in await MessageRepository.get_all(**kw)}


ALL_NAMES = {
    "plain",
    "forever",
    "expired",
    "future",
    "edge",
    "pending",
    "pending_muted",
    "stale_flag",
    "deleted",
    "dm",
}
MUTED_NOW = {"forever", "future", "pending_muted"}


@pytest.mark.asyncio
async def test_defaults_filter_nothing(test_db):
    await _mute_world()
    assert await _names() == ALL_NAMES
    assert await _names(muted="all", membership="all") == ALL_NAMES


@pytest.mark.asyncio
async def test_muted_exclude_is_null_safe_and_honours_the_expiry(test_db):
    await _mute_world()
    # PRIV rows and rows of a deleted channel have no channel (all columns NULL): kept.
    # An expired timed mute, one ending exactly now, and a stale muted_until under muted = 0: kept.
    assert await _names(muted="exclude") == ALL_NAMES - MUTED_NOW


@pytest.mark.asyncio
async def test_muted_only_keeps_rows_of_channels_muted_right_now(test_db):
    await _mute_world()
    assert await _names(muted="only") == MUTED_NOW


@pytest.mark.asyncio
async def test_a_timed_mute_follows_the_clock(test_db):
    await _mute_world()
    assert "future" in await _names(muted="only", now=NOW + 3599)
    assert "future" not in await _names(muted="only", now=NOW + 3600)
    assert "future" in await _names(muted="exclude", now=NOW + 3600)
    assert "future" not in await _names(muted="exclude", now=NOW + 3599)
    # muted_until == now is over; one second earlier it still holds.
    assert "edge" in await _names(muted="only", now=NOW - 1)
    assert "edge" not in await _names(muted="only", now=NOW)
    # The default clock is the wall clock: a mute that ended long ago does not hold.
    assert "expired" not in await _names(muted="only", now=None)
    assert "forever" in await _names(muted="only", now=None)


@pytest.mark.asyncio
async def test_membership_adopted_keeps_dms_and_deleted_channels(test_db):
    await _mute_world()
    assert await _names(membership="adopted") == ALL_NAMES - {"pending", "pending_muted"}


@pytest.mark.asyncio
async def test_membership_pending_keeps_only_pending_channels(test_db):
    await _mute_world()
    assert await _names(membership="pending") == {"pending", "pending_muted"}


@pytest.mark.asyncio
async def test_muted_and_membership_combine(test_db):
    await _mute_world()
    assert await _names(muted="exclude", membership="adopted") == ALL_NAMES - MUTED_NOW - {
        "pending"
    }
    assert await _names(muted="only", membership="pending") == {"pending_muted"}
    assert await _names(muted="only", membership="adopted") == {"forever", "future"}
    assert await _names(muted="exclude", membership="pending") == {"pending"}


@pytest.mark.asyncio
async def test_a_channel_with_a_lowercase_stored_key_still_joins(test_db):
    """The join is on the stored key as is: a channel stored in lowercase, with messages under
    the same lowercase key, is found, so its mute and membership apply to its rows."""
    low_muted, low_pending = "ab" * 16, "cd" * 16
    await _insert_channel(low_muted, muted=1)
    await _insert_channel(low_pending, membership="pending")
    await _create("low muted", received_at=1, key=low_muted)
    await _create("low pending", received_at=2, key=low_pending)
    assert await _names(muted="exclude") == {"low pending"}
    assert await _names(muted="only") == {"low muted"}
    assert await _names(membership="adopted") == {"low muted"}
    assert await _names(membership="pending") == {"low pending"}


@pytest.mark.asyncio
async def test_a_channel_with_a_null_muted_flag_is_not_muted(test_db):
    """`muted` is nullable (DEFAULT 0, no NOT NULL): NULL means not muted, like in Python."""
    await _insert_channel(K_PLAIN)
    async with test_db.tx() as conn:
        await conn.execute("UPDATE channels SET muted = NULL WHERE key = ?", (K_PLAIN,))
    await _create("null muted", received_at=1, key=K_PLAIN)
    assert await _names(muted="exclude") == {"null muted"}
    assert await _names(muted="only") == set()
    assert (await MessageRepository.get_unread_counts(now=NOW))["counts"] == {
        f"channel-{K_PLAIN}": 1
    }


@pytest.mark.asyncio
async def test_mute_and_membership_combine_with_every_other_filter(test_db):
    ids = await _mute_world()
    plain_out = await _create("plain out", received_at=500, outgoing=True, key=K_PLAIN)
    forever_out = await _create("forever out", received_at=501, outgoing=True, key=K_FOREVER)
    common = {"muted": "exclude", "membership": "adopted", "now": NOW}
    got = await MessageRepository.get_all(outgoing=True, msg_type="CHAN", **common)
    assert [m.id for m in got] == [plain_out]
    assert forever_out not in {m.id for m in await MessageRepository.get_all(**common)}
    got = await MessageRepository.get_all(q="plain", **common)
    assert [m.id for m in got] == [plain_out, ids["plain"]]
    # Cursors cut after the filter: a hidden row never takes a page slot or a cursor.
    page = await MessageRepository.get_all(limit=2, **common)
    nxt = await MessageRepository.get_all(
        limit=2, before=page[-1].received_at, before_id=page[-1].id, **common
    )
    assert not ({m.id for m in page} & {m.id for m in nxt})
    assert forever_out not in {m.id for m in page + nxt}
    got = await MessageRepository.get_all(since_id=ids["plain"], **common)
    assert [m.id for m in got] == sorted(m.id for m in got)
    assert forever_out not in {m.id for m in got}
    assert plain_out in {m.id for m in got}
    got = await MessageRepository.get_all(after=100, after_id=ids["plain"], **common)
    assert forever_out not in {m.id for m in got}
    # The blocked list still applies on top.
    await MessageRepository.create(
        msg_type="CHAN",
        text="spam",
        conversation_key=K_PLAIN,
        sender_timestamp=9,
        received_at=900,
        sender_key="bb" * 32,
    )
    got = await MessageRepository.get_all(blocked_keys=["bb" * 32], **common)
    assert "spam" not in {m.text for m in got}


@pytest.mark.asyncio
async def test_invalid_enumerations_and_combination_are_refused(test_db):
    for kw in (
        {"muted": "none"},
        {"muted": "EXCLUDE"},
        {"muted": ""},
        {"muted": "exclude; DROP TABLE messages"},
        {"membership": "any"},
        {"membership": "Pending"},
    ):
        with pytest.raises(ValueError):
            await MessageRepository.get_all(**kw)
    with pytest.raises(ValueError, match="conversation_key"):
        await MessageRepository.get_all(conversation_keys=[K_PLAIN], conversation_key=K_PLAIN)


# --- the shared mute fragment ---------------------------------------------------------------


@pytest.mark.parametrize("muted", [0, 1])
@pytest.mark.parametrize("muted_until", [None, NOW - 1, NOW, NOW + 1])
@pytest.mark.asyncio
async def test_mute_fragment_is_the_sql_twin_of_effective_channel_muted(
    test_db, muted, muted_until
):
    await _insert_channel(K_PLAIN, muted=muted, muted_until=muted_until)
    fragment = effective_mute_sql("c", NOW)
    async with test_db.readonly() as conn:
        async with conn.execute(
            f"SELECT {fragment} FROM channels c WHERE c.key = ?", (K_PLAIN,)
        ) as cur:
            (value,) = await cur.fetchone()
        # A LEFT JOIN row without a channel: NULL, never true.
        async with conn.execute(
            f"SELECT {fragment} FROM (SELECT 1) LEFT JOIN channels c ON c.key = 'none'"
        ) as cur:
            (missing,) = await cur.fetchone()
    assert bool(value) is effective_channel_muted(bool(muted), muted_until, NOW)
    assert not missing


def test_mute_fragment_text_and_guards():
    assert effective_mute_sql("ch", 42) == (
        "(ch.muted = 1 AND (ch.muted_until IS NULL OR ch.muted_until > 42))"
    )
    for bad in ("c; DROP TABLE x", "c d", "", "1c", "c.muted"):
        with pytest.raises(ValueError):
            effective_mute_sql(bad, 1)
    with pytest.raises(ValueError):
        effective_mute_sql("c", "1; DROP TABLE x")  # type: ignore[arg-type]
    assert "7" in effective_mute_sql("c", 7.9)  # a float clock is cut to whole seconds


@pytest.mark.asyncio
async def test_feed_and_unread_query_share_one_mute_fragment(test_db, monkeypatch):
    """Both queries build their mute rule from `effective_mute_sql`: replace it and both move."""
    await _insert_channel(K_PLAIN)
    await _create("hello", received_at=NOW - 10, key=K_PLAIN)
    never = lambda alias, now: "(0)"  # noqa: E731 - no channel is ever muted
    always = lambda alias, now: "(1)"  # noqa: E731 - every channel is muted
    calls = []

    def spy(alias, now):
        calls.append((alias, now))
        return effective_mute_sql(alias, now)

    monkeypatch.setattr(messages_module, "effective_mute_sql", spy)
    await MessageRepository.get_all(muted="exclude", now=NOW)
    await MessageRepository.get_unread_counts(now=NOW)
    assert ("channels", NOW) in calls and ("ch", NOW) in calls

    monkeypatch.setattr(messages_module, "effective_mute_sql", always)
    assert await _names(muted="exclude") == set()
    assert await _names(muted="only") == {"hello"}
    assert (await MessageRepository.get_unread_counts(now=NOW))["counts"] == {}
    monkeypatch.setattr(messages_module, "effective_mute_sql", never)
    assert await _names(muted="exclude") == {"hello"}
    assert await _names(muted="only") == set()
    counts = (await MessageRepository.get_unread_counts(now=NOW))["counts"]
    assert counts == {f"channel-{K_PLAIN}": 1}


@pytest.mark.asyncio
async def test_unread_counts_expired_mute_counts_again(test_db):
    """The drift this ticket fixes: the unread query ignored `muted_until`."""
    for key, kwargs in (
        (K_FOREVER, {"muted": 1}),
        (K_EXPIRED, {"muted": 1, "muted_until": NOW - 60}),
        (K_FUTURE, {"muted": 1, "muted_until": NOW + 60}),
        (K_EDGE, {"muted": 1, "muted_until": NOW}),
        (K_PLAIN, {}),
    ):
        await _insert_channel(key, **kwargs)
        await _create("u", received_at=NOW - 5, key=key)
    counts = (await MessageRepository.get_unread_counts(now=NOW))["counts"]
    assert counts == {
        f"channel-{K_EXPIRED}": 1,
        f"channel-{K_EDGE}": 1,
        f"channel-{K_PLAIN}": 1,
    }
    later = (await MessageRepository.get_unread_counts(now=NOW + 61))["counts"]
    assert f"channel-{K_FUTURE}" in later and f"channel-{K_FOREVER}" not in later


# --- conversation_keys ----------------------------------------------------------------------


def test_normalize_conversation_keys_cases_and_dedupe():
    assert normalize_conversation_keys(["ab" * 16, "AB" * 32]) == ["AB" * 16, "ab" * 32]
    assert normalize_conversation_keys(["Ab" * 16, "aB" * 16, "AB" * 16]) == ["AB" * 16]
    assert normalize_conversation_keys(["0f" * 32, "0F" * 32]) == ["0f" * 32]
    # Exactly 50 is fine, 51 is not (counted before the duplicates collapse).
    fifty = [f"{i:032x}" for i in range(MAX_CONVERSATION_KEYS)]
    assert len(normalize_conversation_keys(fifty)) == 50
    with pytest.raises(ValueError):
        normalize_conversation_keys([*fifty, fifty[0]])
    with pytest.raises(ValueError):
        normalize_conversation_keys([])


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " ",
        "g" * 32,
        "g" * 64,
        "a" * 31,
        "a" * 33,
        "a" * 63,
        "a" * 65,
        "a" * 128,
        "a" * 31 + "é",
        "a" * 32 + "\n",
        " " + "a" * 32,
        "a" * 32 + " ",
        "0x" + "a" * 30,
        "ab" * 16 + "," + "cd" * 16,
        "a" * 32 + "' OR '1'='1",
        "%" * 32,
        "_" * 32,
        "١" * 32,  # Arabic-indic digits are not hex
    ],
)
def test_normalize_conversation_keys_rejects(bad):
    with pytest.raises(ValueError):
        normalize_conversation_keys([K_PLAIN, bad])


@pytest.mark.asyncio
async def test_conversation_keys_filter_by_normalised_key_in_any_case(test_db):
    await _insert_channel(K_PLAIN)
    a = await _create("chan", received_at=1, key=K_PLAIN)
    b = await _create("dm", received_at=2, msg_type="PRIV", key=DM)
    await _create("other", received_at=3, key=K_FOREVER)
    await _create("other dm", received_at=4, msg_type="PRIV", key="cd" * 32)

    async def ids(keys, **kw):
        return {m.id for m in await MessageRepository.get_all(conversation_keys=keys, **kw)}

    for chan_form in (K_PLAIN, K_PLAIN.lower(), K_PLAIN.swapcase()):
        assert await ids([chan_form]) == {a}
    for dm_form in (DM, DM.upper(), DM.swapcase()):
        assert await ids([dm_form]) == {b}
    assert await ids([K_PLAIN.lower(), DM.upper()]) == {a, b}
    assert await ids([K_PLAIN, K_PLAIN.lower()]) == {a}
    assert await ids(["00" * 16]) == set()
    assert await ids([K_PLAIN], msg_type="PRIV") == set()
    assert await ids([K_PLAIN, DM], msg_type="CHAN") == {a}


@pytest.mark.asyncio
async def test_conversation_keys_are_bound_never_inlined(test_db):
    keys = [f"{i:032X}" for i in range(1, 11)]
    sql, params = await _listing_sql(test_db, conversation_keys=keys, msg_type="CHAN")
    assert sql.count("?") == len(params)
    assert f"IN ({', '.join('?' * 10)})" in sql
    assert all(k not in sql for k in keys)
    assert set(keys) <= set(params)
    sql, params = await _listing_sql(test_db, conversation_keys=[keys[0].lower()])
    assert "IN (?)" in sql and params[0] == keys[0]
    # No pick: no IN clause at all.
    sql, _ = await _listing_sql(test_db)
    assert "IN (" not in sql
    # No filter: no join predicate on channels beyond the join itself.
    assert "channels.muted" not in sql and "channels.membership" not in sql


@pytest.mark.asyncio
async def test_conversation_keys_combine_with_cursors_and_filters(test_db):
    await _insert_channel(K_PLAIN)
    await _insert_channel(K_FOREVER, muted=1)
    mine = [await _create(f"m{i}", received_at=10 + i, key=K_PLAIN) for i in range(5)]
    muted = await _create("muted", received_at=20, key=K_FOREVER)
    await _create("elsewhere", received_at=21, key=K_FUTURE)
    kw = {"conversation_keys": [K_PLAIN, K_FOREVER], "muted": "exclude", "now": NOW}
    got = await MessageRepository.get_all(limit=3, **kw)
    assert [m.id for m in got] == mine[::-1][:3]
    got = await MessageRepository.get_all(
        limit=3, before=got[-1].received_at, before_id=got[-1].id, **kw
    )
    assert [m.id for m in got] == mine[::-1][3:]
    got = await MessageRepository.get_all(conversation_keys=[K_PLAIN, K_FOREVER], now=NOW)
    assert muted in {m.id for m in got}


# --- the route -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_route_enumerations_and_pick(test_db, client, monkeypatch):
    _frozen_clock(monkeypatch, NOW)
    await _mute_world()

    async def texts(**params):
        resp = await client.get("/api/messages", params={"limit": 100, **params})
        assert resp.status_code == 200, (params, resp.text)
        return {m["text"] for m in resp.json()}

    assert await texts() == ALL_NAMES
    assert await texts(muted="exclude", membership="adopted") == ALL_NAMES - MUTED_NOW - {"pending"}
    assert await texts(muted="only") == MUTED_NOW
    assert await texts(membership="pending") == {"pending", "pending_muted"}
    assert await texts(conversation_keys=[K_PLAIN.lower(), DM.upper()]) == {"plain", "dm"}
    assert await texts(conversation_keys=[K_PLAIN, K_FOREVER], muted="exclude") == {"plain"}
    # A single value, a repeated parameter and a type all go through the same list.
    assert await texts(conversation_keys=K_PLAIN, type="CHAN") == {"plain"}
    assert await texts(conversation_keys=[DM], type="CHAN") == set()


@pytest.mark.asyncio
async def test_route_timed_mute_uses_the_request_clock(test_db, client, monkeypatch):
    await _insert_channel(K_FUTURE, muted=1, muted_until=NOW + 100)
    await _create("x", received_at=1, key=K_FUTURE)
    _frozen_clock(monkeypatch, NOW)
    assert [m["text"] for m in (await client.get("/api/messages?muted=only")).json()] == ["x"]
    assert (await client.get("/api/messages?muted=exclude")).json() == []
    _frozen_clock(monkeypatch, NOW + 100)
    assert (await client.get("/api/messages?muted=only")).json() == []
    assert len((await client.get("/api/messages?muted=exclude")).json()) == 1


@pytest.mark.asyncio
async def test_route_validation(test_db, client):
    await _create("seed", received_at=1)
    fifty = [f"{i:032x}" for i in range(50)]
    ok = await client.get("/api/messages", params={"conversation_keys": fifty})
    assert ok.status_code == 200
    bad_requests = [
        {"conversation_keys": [*fifty, "f" * 32]},  # 51 values
        {"conversation_keys": ["g" * 32]},
        {"conversation_keys": ["a" * 31]},
        {"conversation_keys": ["a" * 40]},
        {"conversation_keys": [""]},
        {"conversation_keys": [K_PLAIN, "xyz"]},
        {"conversation_keys": [K_PLAIN], "conversation_key": K_PLAIN},  # both parameters
        {"conversation_keys": [K_PLAIN], "conversation_key": "ab"},
        {"conversation_keys": ["a" * 32 + "' OR '1'='1"]},
    ]
    for params in bad_requests:
        resp = await client.get("/api/messages", params=params)
        assert resp.status_code == 400, params
        assert "conversation_key" in resp.json()["detail"], params
    for params in (
        {"muted": "maybe"},
        {"muted": "EXCLUDE"},
        {"membership": "none"},
        {"membership": ""},
    ):
        assert (await client.get("/api/messages", params=params)).status_code == 422, params
    # An empty value list is no parameter at all.
    assert (await client.get("/api/messages?conversation_keys=")).status_code == 400
    assert (
        await client.get("/api/messages", params={"since_id": 1, "muted": "exclude"})
    ).status_code == 200


# --- query plans ------------------------------------------------------------------------------


def _filter_plan_cases():
    one = [f"{1:032X}"]
    ten = [f"{i:032X}" for i in range(10)]
    dm = [f"{1:064x}"]
    cases = {}
    for label, extra in {
        "mute-exclude": {"muted": "exclude"},
        "mute-only": {"muted": "only"},
        "adopted": {"membership": "adopted"},
        "pending": {"membership": "pending"},
        "exclude-adopted": {"muted": "exclude", "membership": "adopted"},
        "one-key": {"conversation_keys": one},
        "ten-keys": {"conversation_keys": ten},
        "dm-key": {"conversation_keys": dm},
        "ten-keys-exclude-adopted": {
            "conversation_keys": ten,
            "muted": "exclude",
            "membership": "adopted",
        },
    }.items():
        for mtype in (None, "CHAN", "PRIV"):
            for cursor in ("first", "before", "since_id"):
                kw: dict = {"limit": 200, "now": NOW, **extra}
                if mtype:
                    kw["msg_type"] = mtype
                if cursor == "before":
                    kw.update(before=1_700_000_000, before_id=500)
                elif cursor == "since_id":
                    kw["since_id"] = 1
                cases[f"{label}-{mtype or 'any'}-{cursor}"] = kw
    return cases


_FILTER_PLAN_CASES = _filter_plan_cases()


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", list(_FILTER_PLAN_CASES.values()), ids=list(_FILTER_PLAN_CASES))
async def test_filter_plans_use_an_index_without_temp_sort(test_db, kw):
    await _seed_mixed(test_db)
    sql, params = await _listing_sql(test_db, **kw)
    plan = await _plan(test_db, sql, params)
    assert "USING INDEX" in plan or "USING COVERING INDEX" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


@pytest.mark.asyncio
async def test_one_key_with_a_type_seeks_the_pagination_index(test_db):
    """type + 1 key is the exact shape of idx_messages_pagination: seek, no scan, no sort."""
    await _seed_mixed(test_db)
    for kw in (
        {"msg_type": "CHAN", "conversation_keys": [f"{1:032X}"]},
        {
            "msg_type": "CHAN",
            "conversation_keys": [f"{1:032X}"],
            "before": 1_700_000_500,
            "before_id": 9,
        },
        {
            "msg_type": "CHAN",
            "conversation_keys": [f"{1:032X}"],
            "muted": "exclude",
            "membership": "adopted",
        },
    ):
        sql, params = await _listing_sql(test_db, limit=200, now=NOW, **kw)
        plan = await _plan(test_db, sql, params)
        assert "idx_messages_pagination (type=? AND conversation_key=?)" in plan, plan


@pytest.mark.asyncio
async def test_ten_keys_with_a_type_walk_a_type_index_in_order(test_db):
    """type + 10 keys: no index orders by received_at across keys, so the planner walks a
    (type, received_at) index newest first and filters the IN list: ordered, no temp sort."""
    await _seed_mixed(test_db)
    keys = [f"{i:032X}" for i in range(10)]
    sql, params = await _listing_sql(test_db, limit=200, msg_type="CHAN", conversation_keys=keys)
    plan = await _plan(test_db, sql, params)
    assert "idx_messages_feed_type" in plan, plan
    assert "TEMP B-TREE" not in plan, plan


@pytest.mark.asyncio
async def test_the_mute_predicate_does_not_demote_the_channel_join(test_db):
    """The join on channels stays a primary-key seek whatever the mute/membership filter."""
    await _seed_mixed(test_db)
    for extra in ({"muted": "exclude"}, {"muted": "only"}, {"membership": "pending"}):
        sql, params = await _listing_sql(test_db, limit=200, msg_type="CHAN", now=NOW, **extra)
        plan = await _plan(test_db, sql, params)
        assert "SCAN channels" not in plan, plan
        assert "sqlite_autoindex_channels_1" in plan or "SCAN messages" in plan, plan


@pytest.mark.slow
@pytest.mark.asyncio
async def test_sparse_key_first_page_on_50000_rows_is_fast(test_db):
    """A sparse conversation among 50 000 rows, 1 and 10 keys: loose bound, prints the numbers."""
    sparse = f"{999:032X}"
    async with test_db.tx() as conn:
        await conn.executemany(
            "INSERT INTO messages (type, conversation_key, text, received_at, outgoing, "
            "sender_timestamp) VALUES ('CHAN', ?, ?, ?, 0, ?)",
            [
                (sparse if i % 2500 == 0 else f"{i % 30:032X}", f"m{i}", i, i)
                for i in range(1, 50_001)
            ],
        )
    absent = [f"{i + 100:032X}" for i in range(9)]  # no row: the worst case for a type walk
    dense = [f"{i:032X}" for i in range(10)]
    cases = {
        "1 key": ([sparse], 20),
        "10 keys, one sparse, nine absent": ([sparse, *absent], 20),
        "10 dense keys": (dense, 20),
    }
    for label, (keys, expected) in cases.items():
        for extra in ({}, {"muted": "exclude", "membership": "adopted"}):
            t0 = time.perf_counter()
            rows = await MessageRepository.get_all(
                limit=20, msg_type="CHAN", conversation_keys=keys, now=NOW, **extra
            )
            elapsed = time.perf_counter() - t0
            print(f"sparse key, {label}, {extra or 'no mute filter'}: {elapsed * 1000:.1f} ms")
            assert len(rows) == expected
            assert elapsed < 1.0
    only_sparse = await MessageRepository.get_all(
        limit=20, msg_type="CHAN", conversation_keys=[sparse], now=NOW
    )
    assert len(only_sparse) == 20 and all(m.conversation_key == sparse for m in only_sparse)
