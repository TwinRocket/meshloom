"""The feed's event ids come from the Notifications catalogue, never from the feed itself."""

from __future__ import annotations

import re
from pathlib import Path

from app.notifications.events import EVENT_IDS

FEED_EVENTS_TS = Path(__file__).parent.parent / "frontend" / "src" / "utils" / "feedEvents.ts"


def _ts_array(name: str) -> list[str]:
    source = FEED_EVENTS_TS.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.S)
    assert match, f"{name} not found in feedEvents.ts"
    return re.findall(r"'([^']+)'", match.group(1))


def test_notification_ids_exist_in_the_catalogue() -> None:
    ids = _ts_array("NOTIFICATION_EVENT_IDS")
    assert ids, "the feed consumes at least one Notifications id"
    assert set(ids) <= set(EVENT_IDS)


async def test_notification_ids_are_served_by_the_catalogue_api(test_db, client) -> None:
    """MSHL-166: the ids the Feed reads from ``notification_event`` frames are the ones
    ``GET /api/notifications/events`` lists, and every role that reads may get them
    (``read`` audience: no ``notify.*``, no conversation)."""
    from app.websocket import _notification_event_audience

    body = (await client.get("/api/notifications/events")).json()
    served = {event["id"] for event in body["events"]}
    ids = _ts_array("NOTIFICATION_EVENT_IDS")
    assert set(ids) <= served
    for event_id in ids:
        assert not event_id.startswith(("message.", "notify."))
        assert _notification_event_audience({"event": event_id}) == "read"


def test_feed_only_ids_do_not_pretend_to_be_notifications() -> None:
    feed_only = _ts_array("FEED_ONLY_EVENT_IDS")
    assert feed_only
    assert not set(feed_only) & set(EVENT_IDS)
