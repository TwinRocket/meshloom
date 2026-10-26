"""Watched nodes: ``contact.node_seen`` / ``contact.node_back`` (MSHL-129)."""

from __future__ import annotations

import pytest

from app.notifications import events as ev
from app.notifications import render
from app.notifications import watched_nodes as wn
from app.notifications.dispatcher import notification_dispatcher
from app.repository.notification_channels import NotificationChannelRepository
from app.repository.notification_channels import NotificationJournalRepository as Journal

KEY = "ab" * 32
OTHER = "cd" * 32
T0 = 1_800_000_000


def _node(key=KEY, **over):
    return {
        "public_key": key,
        "channels": ["k"],
        "cooldown_s": 3600,
        "silence_s": 86_400,
    } | over


@pytest.fixture
def emitted(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def fake_emit(event, *, payload=None, targets=None, **_kw):
        seen.append({"event": event.event, "payload": payload, "targets": list(targets or [])})
        return len(list(targets or []))

    monkeypatch.setattr("app.notifications.dispatcher.emit", fake_emit)
    return seen


@pytest.fixture
async def clock(test_db):
    now = [float(T0)]
    notification_dispatcher()._dedup._wall = lambda: now[0]
    return now


async def _watch(*nodes):
    rules = await NotificationChannelRepository.get_rules()
    rules["watched_nodes"] = list(nodes)
    await NotificationChannelRepository.set_rules(rules)


async def _hear(key=KEY, **kw):
    await wn.observe_sighting(key, name=kw.pop("name", "Hilltop"), contact_type=2, **kw)


# --- validation ----------------------------------------------------------------------


def test_defaults_and_normalisation():
    stored = wn.clean_entry({"public_key": KEY.upper()})
    assert stored == {
        "public_key": KEY,
        "channels": [],
        "cooldown_s": 3600,
        "silence_s": 86_400,
    }


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"public_key": "abc"}, "public_key"),
        ({"public_key": "zz" * 32}, "public_key"),
        ({"channels": "k"}, "channels"),
        ({"cooldown_s": -1}, "cooldown_s"),
        ({"cooldown_s": 0}, "cooldown_s"),
        ({"cooldown_s": wn.MIN_COOLDOWN_S - 1}, "cooldown_s"),
        ({"cooldown_s": True}, "cooldown_s"),
        ({"cooldown_s": wn.MAX_COOLDOWN_S + 1}, "cooldown_s"),
        ({"silence_s": 10}, "silence_s"),
        ({"silence_s": wn.MAX_SILENCE_S + 1}, "silence_s"),
    ],
)
def test_invalid_entries(over, field):
    with pytest.raises(wn.WatchedNodeError) as err:
        wn.clean_entry(_node(**over))
    assert err.value.args[0] == field


def test_the_minimum_cooldown_is_accepted():
    assert wn.clean_entry(_node(cooldown_s=wn.MIN_COOLDOWN_S))["cooldown_s"] == 60


def test_the_limit_is_50_and_keys_are_unique():
    fifty = [{"public_key": f"{i:064x}"} for i in range(50)]
    assert len(wn.clean_entries(fifty)) == 50
    with pytest.raises(wn.WatchedNodeError):
        wn.clean_entries([*fifty, {"public_key": f"{50:064x}"}])
    with pytest.raises(wn.WatchedNodeError):
        wn.clean_entries([{"public_key": KEY}, {"public_key": KEY.upper()}])


# --- the service (fake clock) --------------------------------------------------------


@pytest.mark.asyncio
async def test_a_watched_node_notifies_and_the_cooldown_holds(clock, emitted):
    await _watch(_node())
    await _hear(now=T0)
    assert [e["event"] for e in emitted] == ["contact.node_seen"]
    assert emitted[0]["targets"] == ["k"]
    assert emitted[0]["payload"]["name"] == "Hilltop"
    clock[0] += 3599
    await _hear(now=T0 + 3599)
    assert len(emitted) == 1
    clock[0] += 2
    await _hear(now=T0 + 3601)
    assert [e["event"] for e in emitted] == ["contact.node_seen"] * 2


@pytest.mark.asyncio
async def test_back_after_the_silence_threshold(clock, emitted):
    await _watch(_node(cooldown_s=60, silence_s=3600))
    await _hear(now=T0)
    clock[0] += 3599
    await _hear(now=T0 + 3599)  # one second short of the threshold
    assert [e["event"] for e in emitted] == ["contact.node_seen", "contact.node_seen"]
    # The silence is counted from the latest sighting, not the last notification.
    clock[0] += 3600
    await _hear(now=T0 + 3599 + 3600)
    assert emitted[-1]["event"] == "contact.node_back"
    assert emitted[-1]["payload"]["silent_s"] == 3600
    # After a "back", the next sighting inside the cooldown is silent.
    clock[0] += 5
    await _hear(now=T0 + 3599 + 3605)
    assert len(emitted) == 3


@pytest.mark.asyncio
async def test_a_back_does_not_double_with_a_seen(clock, emitted):
    await _watch(_node(cooldown_s=600, silence_s=3600))
    await _hear(now=T0)
    clock[0] += 7200
    await _hear(now=T0 + 7200)
    await _hear(now=T0 + 7201)
    assert [e["event"] for e in emitted] == ["contact.node_seen", "contact.node_back"]


@pytest.mark.asyncio
async def test_the_contacts_last_seen_stands_in_for_a_first_sighting(clock, emitted):
    await _watch(_node(silence_s=3600))
    await _hear(now=T0, previous_seen=T0 - 5 * 86_400)
    assert emitted[0]["event"] == "contact.node_back"
    assert emitted[0]["payload"]["silent_s"] == 5 * 86_400


@pytest.mark.asyncio
async def test_an_unknown_node_is_ignored_and_stores_nothing(clock, emitted, test_db):
    await _watch(_node())
    await _hear(OTHER, now=T0)
    assert emitted == []
    assert await Journal.state_get(f"{wn.HEARD_PREFIX}{OTHER}", T0) is None


@pytest.mark.asyncio
async def test_removal_stops_the_events_and_clears_the_state(clock, emitted, client):
    await _watch(_node(cooldown_s=0))
    await _hear(now=T0)
    assert len(emitted) == 1
    assert await Journal.state_get(f"{wn.HEARD_PREFIX}{KEY}", T0) == str(T0)
    response = await client.patch("/api/notifications/rules", json={"watched_nodes": []})
    assert response.status_code == 200
    await _hear(now=T0 + 10)
    assert len(emitted) == 1
    assert await Journal.state_get(f"{wn.HEARD_PREFIX}{KEY}", T0 + 10) is None


@pytest.mark.asyncio
async def test_no_channel_no_event_and_no_cooldown(clock, emitted):
    await _watch(_node(channels=[]))
    await _hear(now=T0)
    assert emitted == []
    await _watch(_node())
    await _hear(now=T0 + 1)
    assert len(emitted) == 1


@pytest.mark.asyncio
async def test_timestamps_and_cooldown_survive_a_restart(clock, emitted, test_db):
    from app.notifications.dispatcher import Dispatcher

    await _watch(_node(cooldown_s=3600, silence_s=7200))
    await _hear(now=T0)
    await notification_dispatcher().flush_counters()
    # A new process: a fresh dispatcher reads the stored cooldown, the sighting is in the table.
    fresh = Dispatcher(wall_clock=lambda: float(T0 + 100))
    assert await fresh.claim_cooldown((f"{wn.COOLDOWN_KEY}{KEY}", 3600)) is False
    assert await Journal.state_get(f"{wn.HEARD_PREFIX}{KEY}", T0 + 100) == str(T0)
    # After the silence threshold, the stored sighting makes it a "back".
    clock[0] += 7200 + 3600
    await _hear(now=T0 + 7200 + 3600)
    assert emitted[-1]["event"] == "contact.node_back"
    assert emitted[-1]["payload"]["silent_s"] == 7200 + 3600


@pytest.mark.asyncio
async def test_a_failing_emit_never_raises(clock, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr("app.notifications.dispatcher.emit", boom)
    await _watch(_node())
    await _hear(now=T0)


# --- the API --------------------------------------------------------------------------


async def test_api_roundtrip_and_limits(test_db, client):
    async def patch(nodes):
        return await client.patch("/api/notifications/rules", json={"watched_nodes": nodes})

    ok = await patch([{"public_key": KEY.upper(), "channels": []}])
    assert ok.status_code == 200
    assert ok.json()["watched_nodes"] == [
        {"public_key": KEY, "channels": [], "cooldown_s": 3600, "silence_s": 86_400}
    ]
    for bad in (
        [{"public_key": KEY, "cooldown_s": 0}],
        [{"public_key": "nope"}],
        [{"public_key": KEY, "channels": ["missing"]}],
        [{"public_key": KEY}, {"public_key": KEY}],
        [{"public_key": f"{i:064x}"} for i in range(51)],
    ):
        response = await patch(bad)
        assert response.status_code == 400, bad
        assert response.json()["detail"]["code"] == "invalid_config"
    assert len((await client.get("/api/notifications/rules")).json()["watched_nodes"]) == 1
    push = await patch([{"public_key": KEY, "channels": ["builtin:push"]}])
    assert push.status_code == 200
    assert push.json()["watched_nodes"][0]["channels"] == ["builtin:push"]
    fifty = await patch([{"public_key": f"{i:064x}"} for i in range(50)])
    assert fifty.status_code == 200 and len(fifty.json()["watched_nodes"]) == 50


async def test_the_events_are_in_the_catalogue_but_not_configurable(test_db, client):
    events = {e["id"]: e for e in (await client.get("/api/notifications/events")).json()["events"]}
    for event_id in ("contact.node_seen", "contact.node_back"):
        assert events[event_id]["configurable"] is False
        assert events[event_id]["urgent"] is False
    refused = await client.patch(
        "/api/notifications/rules", json={"events": {"contact.node_back": {"channels": []}}}
    )
    assert refused.status_code == 400


# --- events and copy -------------------------------------------------------------------


def test_event_roundtrip_and_dedup_per_sighting():
    payload = {
        "event": "node_back",
        "public_key": KEY,
        "name": "Hilltop",
        "contact_type": 2,
        "silent_s": 176_400,
        "heard_at": T0,
    }
    event = ev.event_from_payload(payload)
    assert isinstance(event, ev.NodeBackEvent) and event.event == "contact.node_back"
    assert event.urgent is False
    assert event.to_payload() == payload
    later = ev.event_from_payload(payload | {"heard_at": T0 + 1})
    assert later.dedup_key != event.dedup_key
    seen = ev.event_from_payload(payload | {"event": "node_seen"})
    assert isinstance(seen, ev.NodeSeenEvent) and not isinstance(seen, ev.NodeBackEvent)


def test_titles():
    base = {"event": "node_back", "public_key": KEY, "name": "Hilltop", "silent_s": 2 * 86_400 + 5}
    event = ev.event_from_payload(base)
    assert render.render(event, "fr").title == "Hilltop est de retour après 2 j"
    assert render.render(event, "en").title == "Hilltop is back after 2 d"
    assert "5 h" in render.render(ev.event_from_payload(base | {"silent_s": 5 * 3600}), "en").title
    seen = ev.event_from_payload(base | {"event": "node_seen"})
    assert render.render(seen, "en").title == "Hilltop heard"
    assert render.render(seen, "fr").title == "Hilltop entendu"
    assert render.render(seen, "fr").url_hash == f"#contact/{KEY}"
    nameless = ev.event_from_payload(base | {"name": ""})
    assert render.render(nameless, "en").title.startswith(KEY[:12])


# --- the advert path ------------------------------------------------------------------


async def test_an_advert_from_a_watched_node_goes_through_the_service(clock, emitted, monkeypatch):
    from app.radio import radio_manager

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    await _watch(_node(cooldown_s=60, silence_s=3600))
    await _advert(OTHER, T0)  # not watched
    assert emitted == []
    await _advert(KEY, T0)
    clock[0] += 7200
    await _advert(KEY, T0 + 7200)
    assert [e["event"] for e in emitted] == ["contact.node_seen", "contact.node_back"]
    assert emitted[0]["payload"]["name"] == "Hilltop"


async def _advert(key, at):
    from unittest.mock import MagicMock, patch

    from app.decoder import ParsedAdvertisement
    from app.packet_processor import _process_advertisement

    with (
        patch("app.packet_processor.broadcast_event"),
        patch("app.packet_processor.parse_advertisement") as parse,
        patch("app.packet_processor.verify_advert_signature", return_value=True),
        patch("app.push.first_seen.maybe_notify_contact_first_seen"),
    ):
        parse.return_value = ParsedAdvertisement(
            public_key=key, name="Hilltop", timestamp=at, lat=None, lon=None, device_role=2
        )
        await _process_advertisement(
            b"", timestamp=at, packet_info=MagicMock(path_length=0, path=b"", payload=b"")
        )


async def test_the_advert_path_passes_last_seen_read_before_the_upsert(clock, emitted, monkeypatch):
    """MSHL-401: with no stored sighting, the contact's ``last_seen`` as it was BEFORE the
    advert upserted it decides "back"; read after the upsert it would be the advert's own time
    and the silence would always be zero."""
    from app.models import ContactUpsert
    from app.radio import radio_manager
    from app.repository.contacts import ContactRepository

    monkeypatch.setattr(radio_manager, "_setup_complete", True)
    await ContactRepository.upsert(
        ContactUpsert(public_key=KEY, name="Hilltop", type=2, last_seen=T0 - 5 * 86_400)
    )
    await _watch(_node(silence_s=86_400))
    await _advert(KEY, T0)
    assert [e["event"] for e in emitted] == ["contact.node_back"]
    assert emitted[0]["payload"]["silent_s"] == 5 * 86_400


async def test_a_fallback_direct_message_counts_as_a_sighting(clock, emitted, test_db):
    """MSHL-401: a DM stored by the CONTACT_MSG_RECV fallback is a sighting, as on the
    packet-processor path; a duplicate is not (a CLI response is, MSHL-408)."""
    from unittest.mock import patch

    from app.event_handlers import on_contact_message
    from app.models import ContactUpsert
    from app.repository.contacts import ContactRepository

    await ContactRepository.upsert(
        ContactUpsert(public_key=KEY, name="Hilltop", type=1, last_seen=T0 - 5 * 86_400)
    )
    await _watch(_node(silence_s=86_400))

    class Event:
        def __init__(self, **over):
            self.payload = {
                "public_key": KEY,
                "text": "hello",
                "txt_type": 0,
                "sender_timestamp": T0,
            } | over

    with (
        patch("app.event_handlers.broadcast_event"),
        patch("app.event_handlers.time.time", return_value=float(T0)),
    ):
        await on_contact_message(Event(txt_type=1))  # CLI response: not stored, still heard
        assert [e["event"] for e in emitted] == ["contact.node_back"]
        await on_contact_message(Event())  # stored: a sighting, inside the cooldown
        await on_contact_message(Event())  # exact duplicate: not stored, not counted
        # The cooldown would hide a second emit, so check the stored sighting directly.
        stamp = await Journal.state_get(f"{wn.HEARD_PREFIX}{KEY}", T0 + 1)
        assert stamp == str(T0)
        clock[0] += 10_000
        with patch("app.event_handlers.time.time", return_value=float(T0 + 10_000)):
            await on_contact_message(Event())  # still a duplicate
        assert await Journal.state_get(f"{wn.HEARD_PREFIX}{KEY}", T0 + 10_001) == str(T0)
    assert len(emitted) == 1


async def _packet_dm(at: int, raw: bytes, *, flags=0, outgoing=False, text="hello"):
    """Receive one DM from KEY through the packet-processor path (decryption mocked)."""
    from unittest.mock import MagicMock, patch

    from app.decoder import DecryptedDirectMessage
    from app.packet_processor import _process_direct_message
    from app.repository.raw_packets import RawPacketRepository

    our_pub = bytes([0xFA]) + b"\x01" * 31
    first = int(KEY[:2], 16)
    dest, src = (first, 0xFA) if outgoing else (0xFA, first)
    decrypted = DecryptedDirectMessage(
        timestamp=T0, flags=flags, message=text, dest_hash=f"{dest:02x}", src_hash=f"{src:02x}"
    )
    packet_info = MagicMock()
    packet_info.payload = bytes([dest, src, 0, 0]) + b"\x00" * 20
    packet_info.path = b""
    packet_info.path_length = 0
    pkt_id, _ = await RawPacketRepository.create(raw, at)
    with (
        patch("app.packet_processor.has_private_key", return_value=True),
        patch("app.packet_processor.get_private_key", return_value=b"\x00" * 32),
        patch("app.packet_processor.get_public_key", return_value=our_pub),
        patch("app.packet_processor.try_decrypt_dm", return_value=decrypted),
        patch("app.packet_processor.broadcast_event"),
    ):
        return await _process_direct_message(b"\x00" * 40, pkt_id, at, packet_info)


async def _fallback_dm(at: int, *, txt_type=0, text="hello"):
    from unittest.mock import patch

    from app.event_handlers import on_contact_message

    class Event:
        payload = {"public_key": KEY, "text": text, "txt_type": txt_type, "sender_timestamp": T0}

    with (
        patch("app.event_handlers.broadcast_event"),
        patch("app.event_handlers.time.time", return_value=float(at)),
    ):
        await on_contact_message(Event())


async def _heard() -> str | None:
    return await Journal.state_get(f"{wn.HEARD_PREFIX}{KEY}", T0 + 100_000)


async def _watched_contact(type_: int):
    from app.models import ContactUpsert
    from app.repository.contacts import ContactRepository

    wn._UNSTORED_SEEN.clear()
    await ContactRepository.upsert(
        ContactUpsert(public_key=KEY, name="Hilltop", type=type_, last_seen=T0 - 5 * 86_400)
    )
    await _watch(_node(silence_s=86_400))


async def test_a_packet_path_direct_message_counts_once_per_stored_message(clock, emitted, test_db):
    """MSHL-408: the packet-processor path records a sighting for the stored DM only, not for each
    duplicate reception heard via another repeater (same semantics as the fallback path)."""
    await _watched_contact(1)
    assert await _packet_dm(T0, b"dm-reception-1") is not None
    assert [e["event"] for e in emitted] == ["contact.node_back"]
    assert await _heard() == str(T0)

    # The same DM heard again later via another repeater: not a new sighting. The cooldown would
    # hide a second emit, so check the stored sighting directly.
    clock[0] += 10_000
    await _packet_dm(T0 + 10_000, b"dm-reception-2")
    assert await _heard() == str(T0)
    assert len(emitted) == 1


async def test_an_outgoing_echo_is_not_a_sighting(clock, emitted, test_db):
    """MSHL-408: our own DM heard back (outgoing) says nothing about the node."""
    await _watched_contact(1)
    assert await _packet_dm(T0, b"echo-1", outgoing=True) is not None
    assert emitted == []
    assert await _heard() is None


@pytest.mark.parametrize("kind", ["repeater", "cli"])
async def test_a_decoded_but_unstored_dm_counts_once_on_the_packet_path(
    clock, emitted, test_db, kind
):
    """MSHL-408: a watched repeater's reply or a CLI response is never stored but the node was
    heard; several receptions of it count once."""
    await _watched_contact(2 if kind == "repeater" else 1)
    flags = 4 if kind == "cli" else 0
    await _packet_dm(T0, b"un-1", flags=flags)
    assert [e["event"] for e in emitted] == ["contact.node_back"]
    assert await _heard() == str(T0)
    clock[0] += 10_000
    await _packet_dm(T0 + 10_000, b"un-2", flags=flags)
    assert await _heard() == str(T0)
    assert len(emitted) == 1


@pytest.mark.parametrize("kind", ["repeater", "cli"])
async def test_a_decoded_but_unstored_dm_counts_once_on_the_fallback_lane(
    clock, emitted, test_db, kind
):
    await _watched_contact(2 if kind == "repeater" else 1)
    txt_type = 1 if kind == "cli" else 0
    await _fallback_dm(T0, txt_type=txt_type)
    assert [e["event"] for e in emitted] == ["contact.node_back"]
    assert await _heard() == str(T0)
    clock[0] += 10_000
    await _fallback_dm(T0 + 10_000, txt_type=txt_type)
    assert await _heard() == str(T0)
    assert len(emitted) == 1


async def test_an_unstored_dm_seen_on_both_paths_counts_once(clock, emitted, test_db):
    await _watched_contact(2)
    await _packet_dm(T0, b"both-1")
    clock[0] += 10_000
    await _fallback_dm(T0 + 10_000)
    assert await _heard() == str(T0)


def test_claim_unstored_dm_is_bounded_and_evicts_the_oldest():
    """MSHL-408: the memory of unstored DMs never exceeds its bound; the oldest key goes first."""
    wn._UNSTORED_SEEN.clear()
    cap = wn._UNSTORED_SEEN_MAX
    for i in range(cap + 10):
        assert wn.claim_unstored_dm(KEY, i, "x") is True
    assert len(wn._UNSTORED_SEEN) == cap
    # The oldest ten were evicted: they claim again; the newest are still remembered.
    assert wn.claim_unstored_dm(KEY, cap + 9, "x") is False
    assert wn.claim_unstored_dm(KEY, 0, "x") is True
    wn._UNSTORED_SEEN.clear()


def test_claim_unstored_dm_ignores_the_case_of_the_key():
    wn._UNSTORED_SEEN.clear()
    assert wn.claim_unstored_dm(KEY.upper(), T0, "hi") is True
    assert wn.claim_unstored_dm(KEY.lower(), T0, "hi") is False
    wn._UNSTORED_SEEN.clear()


def test_claim_unstored_dm_text_is_part_of_the_key():
    """Two different messages from one sender in the same second each count."""
    wn._UNSTORED_SEEN.clear()
    assert wn.claim_unstored_dm(KEY, T0, "one") is True
    assert wn.claim_unstored_dm(KEY, T0, "two") is True
    assert wn.claim_unstored_dm(KEY, T0, "one") is False
    wn._UNSTORED_SEEN.clear()


def test_effective_cooldown_clamps_legacy_values():
    assert wn.effective_cooldown(0) == 60
    assert wn.effective_cooldown(5) == 60
    assert wn.effective_cooldown("x") == 60
    assert wn.effective_cooldown(True) == 60
    assert wn.effective_cooldown(600) == 600
    assert wn.effective_cooldown(10**9) == wn.MAX_COOLDOWN_S


async def test_a_legacy_zero_cooldown_still_holds_for_the_minimum(clock, emitted):
    await _watch(_node(cooldown_s=0))
    await _hear(now=T0)
    clock[0] += 30
    await _hear(now=T0 + 30)
    assert len(emitted) == 1
    clock[0] += 31
    await _hear(now=T0 + 61)
    assert len(emitted) == 2


async def test_legacy_rows_are_returned_normalised_and_can_be_sent_back(test_db, client):
    await _watch(_node(cooldown_s=0), _node(OTHER, cooldown_s="x"))
    rows = (await client.get("/api/notifications/rules")).json()["watched_nodes"]
    assert [r["cooldown_s"] for r in rows] == [60, 60]
    rows = [{**r, "channels": []} for r in rows]
    again = await client.patch("/api/notifications/rules", json={"watched_nodes": rows})
    assert again.status_code == 200


async def test_a_stored_row_without_cooldown_keeps_the_default(clock, emitted, test_db):
    row = {"public_key": KEY, "channels": ["k"], "silence_s": 86_400}
    assert wn.normalize_stored([row]) == [row]  # read path: absent stays absent
    await _watch(row)
    await _hear(now=T0)
    clock[0] += 61
    await _hear(now=T0 + 61)
    assert len(emitted) == 1  # the 3600 s default holds, not the 60 s minimum
