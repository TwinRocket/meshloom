"""Equivalence tests: the skip-scan unreads query matches the previous ROW_NUMBER one.

legacy_get_unread_counts is the pre-rewrite implementation, kept verbatim
as an oracle.
"""

import random

import pytest

import app.repository.messages as messages_module
from app.repository import MessageRepository


async def legacy_get_unread_counts(
    name: str | None = None,
    blocked_keys: list[str] | None = None,
    blocked_names: list[str] | None = None,
) -> dict:
    """Get unread counts, mention flags, last-message times/previews, and read boundaries.

    Args:
        name: User's display name for @[name] mention detection. If None, mentions are skipped.
        blocked_keys: Public keys whose messages should be excluded from counts.
        blocked_names: Display names whose messages should be excluded from counts.

    Returns:
        Dict with 'counts', 'mentions', 'last_message_times',
        'last_message_previews', 'last_read_ats', and 'first_unread_ids' keys.
    """
    counts: dict[str, int] = {}
    mention_flags: dict[str, bool] = {}
    last_message_times: dict[str, int] = {}
    last_message_previews: dict[str, str] = {}
    last_message_outgoing: dict[str, bool] = {}
    last_read_ats: dict[str, int | None] = {}
    # id of the oldest unread message per conversation.
    first_unread_ids: dict[str, int | None] = {}

    mention_token = f"@[{name}]" if name else None

    blocked_clause, blocked_params = MessageRepository._build_blocked_incoming_clause(
        "m", blocked_keys, blocked_names
    )
    blocked_sql = f" AND {blocked_clause}" if blocked_clause else ""

    # Last message times for all conversations (including read ones),
    # excluding blocked incoming traffic so refresh matches live WS behavior.
    last_time_clause, last_time_params = MessageRepository._build_blocked_incoming_clause(
        blocked_keys=blocked_keys, blocked_names=blocked_names
    )
    last_time_where_sql = f"WHERE {last_time_clause}" if last_time_clause else ""

    # Single readonly acquisition for all 5 queries — they form one logical
    # snapshot, and holding the lock for the batch is cheaper than acquiring
    # it 5 times.
    async with messages_module.db.readonly() as conn:
        # Channel unreads
        async with conn.execute(
            f"""
            SELECT m.conversation_key,
                   COUNT(*) as unread_count,
                   SUM(CASE
                           WHEN ? <> '' AND INSTR(LOWER(m.text), LOWER(?)) > 0 THEN 1
                           ELSE 0
                       END) > 0 as has_mention
            FROM messages m
            JOIN channels c ON m.conversation_key = c.key
            WHERE m.type = 'CHAN' AND m.outgoing = 0
              AND m.received_at > COALESCE(c.last_read_at, 0)
              AND COALESCE(c.muted, 0) = 0
              {blocked_sql}
            GROUP BY m.conversation_key
            """,
            (mention_token or "", mention_token or "", *blocked_params),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            state_key = f"channel-{row['conversation_key']}"
            counts[state_key] = row["unread_count"]
            if mention_token and row["has_mention"]:
                mention_flags[state_key] = True

        # Contact unreads
        async with conn.execute(
            f"""
            SELECT m.conversation_key,
                   COUNT(*) as unread_count,
                   SUM(CASE
                           WHEN ? <> '' AND INSTR(LOWER(m.text), LOWER(?)) > 0 THEN 1
                           ELSE 0
                       END) > 0 as has_mention
            FROM messages m
            LEFT JOIN contacts ct ON m.conversation_key = ct.public_key
            WHERE m.type = 'PRIV' AND m.outgoing = 0
              AND m.received_at > COALESCE(ct.last_read_at, 0)
              {blocked_sql}
            GROUP BY m.conversation_key
            """,
            (mention_token or "", mention_token or "", *blocked_params),
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            state_key = f"contact-{row['conversation_key']}"
            counts[state_key] = row["unread_count"]
            if mention_token and row["has_mention"]:
                mention_flags[state_key] = True

        async with conn.execute(
            """
            SELECT key, last_read_at
            FROM channels
            """
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            last_read_ats[f"channel-{row['key']}"] = row["last_read_at"]

        async with conn.execute(
            """
            SELECT public_key, last_read_at
            FROM contacts
            """
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            last_read_ats[f"contact-{row['public_key']}"] = row["last_read_at"]

        # Oldest unread message per conversation. ROW_NUMBER rather than
        # MIN(received_at) with a bare id: sender timestamps are whole seconds
        # (a protocol constraint, see AGENTS.md), so several unread messages
        # routinely share the oldest second and SQLite's bare-column rule only
        # promises *a* row holding the minimum. Ordering by (received_at, id)
        # picks the same message the client's own ordering does.
        async with conn.execute(
            f"""
            WITH ranked AS (
                SELECT m.type, m.conversation_key, m.id,
                       ROW_NUMBER() OVER (
                           PARTITION BY m.type, m.conversation_key
                           ORDER BY m.received_at ASC, m.id ASC
                       ) AS rn
                FROM messages m
                LEFT JOIN channels c ON m.type = 'CHAN' AND m.conversation_key = c.key
                LEFT JOIN contacts ct ON m.type = 'PRIV' AND m.conversation_key = ct.public_key
                WHERE m.outgoing = 0
                  AND m.received_at > COALESCE(
                          CASE WHEN m.type = 'CHAN' THEN c.last_read_at ELSE ct.last_read_at END,
                          0
                      )
                  AND (m.type <> 'CHAN' OR COALESCE(c.muted, 0) = 0)
                  {blocked_sql}
            )
            SELECT type, conversation_key, id FROM ranked WHERE rn = 1
            """,
            blocked_params,
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            prefix = "channel" if row["type"] == "CHAN" else "contact"
            first_unread_ids[f"{prefix}-{row['conversation_key']}"] = row["id"]

        # Newest message per conversation. ROW_NUMBER rather than MAX(received_at)
        # with a bare text column: sender timestamps are whole seconds, so several
        # messages routinely share the newest second. Ordering by
        # (received_at DESC, id DESC) is the inverse of first_unread_ids and
        # returns both the timestamp and a short preview in this same 5th query.
        async with conn.execute(
            f"""
            WITH ranked AS (
                SELECT type, conversation_key, received_at, text, outgoing,
                       ROW_NUMBER() OVER (
                           PARTITION BY type, conversation_key
                           ORDER BY received_at DESC, id DESC
                       ) AS rn
                FROM messages
                {last_time_where_sql}
            )
            SELECT type, conversation_key, received_at AS last_message_time,
                   SUBSTR(COALESCE(text, ''), 1, 120) AS last_message_preview,
                   outgoing
            FROM ranked
            WHERE rn = 1
            """,
            last_time_params,
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            prefix = "channel" if row["type"] == "CHAN" else "contact"
            state_key = f"{prefix}-{row['conversation_key']}"
            last_message_times[state_key] = row["last_message_time"]
            last_message_previews[state_key] = row["last_message_preview"] or ""
            last_message_outgoing[state_key] = bool(row["outgoing"])

    # Only include last_read_ats for conversations that actually have messages.
    # Without this filter, every contact heard via advertisement (even without
    # any DMs) bloats the payload — 391KB down to ~46KB on a typical database.
    last_read_ats = {k: v for k, v in last_read_ats.items() if k in last_message_times}

    return {
        "counts": counts,
        "mentions": mention_flags,
        "last_message_times": last_message_times,
        "last_message_previews": last_message_previews,
        "last_message_outgoing": last_message_outgoing,
        "last_read_ats": last_read_ats,
        "first_unread_ids": first_unread_ids,
    }


async def _seed(seed: int) -> None:
    rnd = random.Random(seed)
    channels = [f"{i:032X}" for i in range(6)]
    contacts = [f"{i:02x}" * 32 for i in range(1, 9)]
    names = ["Alice", "Bob", "Carol", "me", None]
    async with messages_module.db.tx() as conn:
        # Channels: some read, some muted; channel 5 has no row (orphan).
        for i, key in enumerate(channels[:5]):
            await conn.execute(
                "INSERT INTO channels (key, name, last_read_at, muted) VALUES (?, ?, ?, ?)",
                (key, f"#c{i}", rnd.choice([None, 0, 1050, 1100]), 1 if i == 3 else 0),
            )
        # Contacts: some read; the last two have no row (prefix / unknown DMs).
        for key in contacts[:6]:
            await conn.execute(
                "INSERT INTO contacts (public_key, name, last_read_at) VALUES (?, ?, ?)",
                (key, f"n-{key[:4]}", rnd.choice([None, 1020, 1080, 2000])),
            )
        for i in range(400):
            # Coarse timestamps so many messages share the same second.
            received = 1000 + rnd.randrange(0, 150, 5)
            if rnd.random() < 0.6:
                conv = rnd.choice(channels)
                sender = rnd.choice(names)
                text = f"{sender}: " + rnd.choice(["hi", "hey @[me] there", "@[ME] caps", "x"])
                await conn.execute(
                    "INSERT INTO messages (type, conversation_key, text, sender_timestamp,"
                    " received_at, outgoing, sender_name, sender_key) VALUES"
                    " ('CHAN', ?, ?, ?, ?, ?, ?, ?)",
                    (
                        conv,
                        text,
                        i,
                        received,
                        1 if rnd.random() < 0.2 else 0,
                        sender,
                        rnd.choice([None, contacts[0], contacts[1]]),
                    ),
                )
            else:
                conv = rnd.choice(contacts + ["abcd12"])
                await conn.execute(
                    "INSERT INTO messages (type, conversation_key, text, sender_timestamp,"
                    " received_at, outgoing, sender_key) VALUES ('PRIV', ?, ?, ?, ?, ?, ?)",
                    (
                        conv,
                        rnd.choice(["dm", "dm @[me]"]) + f" {i}",
                        i,
                        received,
                        1 if rnd.random() < 0.4 else 0,
                        conv,
                    ),
                )


SCENARIOS = [
    {"name": None, "blocked_keys": None, "blocked_names": None},
    {"name": "me", "blocked_keys": None, "blocked_names": None},
    {"name": "me", "blocked_keys": ["01" * 32, "03" * 32], "blocked_names": None},
    {"name": "me", "blocked_keys": None, "blocked_names": ["Alice", "Bob"]},
    {"name": "Carol", "blocked_keys": ["02" * 32], "blocked_names": ["Alice"]},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", [1, 2, 3, 4])
@pytest.mark.parametrize("kwargs", SCENARIOS)
async def test_unread_counts_match_legacy_query(test_db, seed, kwargs):
    await _seed(seed)
    expected = await legacy_get_unread_counts(**kwargs)
    actual = await MessageRepository.get_unread_counts(**kwargs)
    assert actual == expected
    # Sanity: the scenario actually exercises unreads, mentions and orphans.
    assert expected["counts"]
    assert "channel-00000000000000000000000000000005" in expected["last_message_times"]


@pytest.mark.asyncio
async def test_unread_counts_on_empty_database(test_db):
    assert await MessageRepository.get_unread_counts(name="me") == await legacy_get_unread_counts(
        name="me"
    )
