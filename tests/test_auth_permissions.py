"""The role matrix (app/auth/permissions.py): one representative route per router,
the ``kiosk`` channel-only rule on every REST surface that reveals a DM, token
scopes, and per-client WebSocket delivery."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.auth.models import User
from app.auth.permissions import ROLE_PERMISSIONS, has_permission, permissions_of
from app.auth.principal import Principal
from app.auth.ws_events import WsEvent
from app.models import RadioProxyStatus, RawPacketDecryptedInfo
from app.repository import ContactRepository, MessageRepository
from app.repository.channels import ChannelRepository
from app.websocket import _DEFAULT_AUDIENCE, WebSocketManager

pytestmark = pytest.mark.usefixtures("test_db")

CONTACT_KEY = "ab" * 32
CHANNEL_KEY = "CD" * 16


def _principal(role: str, scopes: frozenset[str] = frozenset()) -> Principal:
    user = User(id=3, username=role, role=role, created_at=0, updated_at=0)  # type: ignore[arg-type]
    return Principal(user=user, via="lan", credential="session", scopes=scopes)


def _forbidden(response, permission: str) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "auth_forbidden"
    assert response.json()["detail"]["params"] == {"permission": permission}


async def _seed_messages() -> tuple[int, int]:
    """One channel message and one DM, both mentioning ``hello``."""
    await ChannelRepository.upsert(key=CHANNEL_KEY, name="#club")
    await ContactRepository.upsert(
        {"public_key": CONTACT_KEY, "name": "Alice", "last_contacted": 1_700_000_100}
    )
    chan_id = await MessageRepository.create(
        msg_type="CHAN",
        text="Alice: hello channel",
        received_at=1_700_000_000,
        conversation_key=CHANNEL_KEY,
        sender_timestamp=1_700_000_000,
        sender_name="Alice",
        sender_key=CONTACT_KEY,
    )
    dm_id = await MessageRepository.create(
        msg_type="PRIV",
        text="hello secret",
        received_at=1_700_000_100,
        conversation_key=CONTACT_KEY,
        sender_timestamp=1_700_000_100,
        sender_key=CONTACT_KEY,
    )
    assert chan_id is not None and dm_id is not None
    return chan_id, dm_id


class TestRoleTable:
    def test_roles_grant_what_the_decision_says(self):
        assert ROLE_PERMISSIONS["kiosk"] == {"read"}
        assert ROLE_PERMISSIONS["viewer"] == {"read", "read_private"}
        assert ROLE_PERMISSIONS["member"] == {"read", "read_private", "send"}
        assert permissions_of(_principal("admin")) == {
            "read",
            "read_private",
            "send",
            "repeater_admin",
            "admin",
        }
        assert not has_permission(_principal("member"), "repeater_admin")
        assert permissions_of(None) == frozenset()

    def test_scopes_narrow_a_role_and_never_extend_it(self):
        assert permissions_of(_principal("admin", frozenset({"repeater_admin"}))) == {
            "repeater_admin"
        }
        assert permissions_of(_principal("kiosk", frozenset({"send", "read"}))) == {"read"}
        # An admin scope implies every permission, like the admin role.
        assert has_permission(_principal("admin", frozenset({"admin"})), "send")


class TestRouteMatrix:
    async def test_health_errors_are_admin_only_on_rest(self, sign_in_as, client, monkeypatch):
        from app.routers import health as health_router

        real = health_router.build_health_data

        async def with_errors(*args, **kwargs):
            data = await real(*args, **kwargs)
            data["fanout_statuses"] = {
                "f": {"name": "MQTT", "type": "mqtt", "status": "error", "last_error": "mqtt.lan"}
            }
            return data

        monkeypatch.setattr(health_router, "build_health_data", with_errors)
        sign_in_as("member")
        member = (await client.get("/api/health")).json()
        assert member["fanout_statuses"]["f"]["last_error"] is None
        assert member["fanout_statuses"]["f"]["status"] == "error"
        sign_in_as("admin")
        admin = (await client.get("/api/health")).json()
        assert admin["fanout_statuses"]["f"]["last_error"] == "mqtt.lan"

    async def test_viewer_reads_messages_but_cannot_send(self, sign_in_as, client):
        sign_in_as("viewer")
        assert (await client.get("/api/messages")).status_code == 200
        _forbidden(
            await client.post("/api/messages/channel", json={"channel_key": "x", "text": "t"}),
            "send",
        )
        _forbidden(
            await client.post("/api/messages/direct", json={"destination": "x", "text": "t"}),
            "send",
        )

    async def test_member_sends_but_cannot_touch_settings(self, sign_in_as, client):
        sign_in_as("member")
        # Past the permission check: the body is refused, not the caller.
        response = await client.post("/api/messages/channel", json={})
        assert response.status_code == 422
        _forbidden(await client.patch("/api/settings", json={}), "admin")
        _forbidden(await client.get("/api/settings"), "admin")
        _forbidden(await client.get("/api/fanout"), "admin")
        _forbidden(await client.get("/api/radio/private-key"), "admin")
        _forbidden(
            await client.post("/api/contacts/" + CONTACT_KEY + "/repeater/status"), "repeater_admin"
        )
        assert (await client.post("/api/settings/favorites/toggle", json={})).status_code == 422

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/debug"),
            ("GET", "/api/settings/backup/json"),
            ("POST", "/api/settings/backup/export"),
            ("GET", "/api/updates"),
            ("GET", "/api/community"),
            ("GET", "/api/push/preferences"),
            ("GET", "/api/radio/config"),
            ("GET", "/api/radio/wifi"),
            ("POST", "/api/packets/maintenance"),
        ],
    )
    async def test_admin_surfaces_refuse_a_member(self, sign_in_as, client, method, path):
        sign_in_as("member")
        _forbidden(await client.request(method, path), "admin")

    async def test_repeater_admin_scope_grants_repeaters_and_rooms_not_settings(
        self, sign_in_as, client
    ):
        sign_in_as("admin", scopes=frozenset({"repeater_admin"}))
        for path in ("repeater/status", "room/status"):
            response = await client.post(f"/api/contacts/{CONTACT_KEY}/{path}")
            assert response.status_code != 403, path
        _forbidden(await client.patch("/api/settings", json={}), "admin")
        _forbidden(await client.get("/api/settings"), "admin")

    @pytest.mark.parametrize("credential", ["session", "basic"])
    async def test_an_admin_keeps_full_access(self, sign_in_as, client, credential):
        # The first admin and the account imported from 4.x Basic are admins.
        sign_in_as("admin", credential=credential)
        assert (await client.get("/api/settings")).status_code == 200
        assert (await client.get("/api/fanout")).status_code == 200
        assert (await client.get("/api/auth/me")).status_code != 403


class TestKiosk:
    async def test_kiosk_has_no_profile_and_no_sessions(self, sign_in_as, client):
        sign_in_as("kiosk")
        _forbidden(await client.get("/api/auth/me"), "account")
        _forbidden(await client.patch("/api/auth/me", json={}), "account")
        _forbidden(await client.get("/api/auth/sessions"), "account")
        _forbidden(await client.delete("/api/auth/sessions"), "account")

    async def test_a_scoped_token_never_reaches_the_account_routes(self, sign_in_as, client):
        for role, scopes in [
            ("viewer", {"read"}),
            ("admin", {"repeater_admin"}),
            ("admin", {"admin"}),
        ]:
            sign_in_as(role, scopes=frozenset(scopes))
            _forbidden(await client.get("/api/auth/me"), "account")
            _forbidden(await client.patch("/api/auth/me", json={}), "account")
            _forbidden(await client.get("/api/auth/sessions"), "account")
            _forbidden(await client.delete("/api/auth/sessions"), "account")
        sign_in_as("viewer")
        assert (await client.get("/api/auth/me")).status_code != 403

    async def test_kiosk_is_read_only(self, sign_in_as, client):
        sign_in_as("kiosk")
        _forbidden(await client.post("/api/messages/channel", json={}), "send")
        _forbidden(await client.post(f"/api/channels/{CHANNEL_KEY}/mark-read"), "read_private")
        _forbidden(await client.post("/api/read-state/mark-all-read"), "read_private")
        _forbidden(await client.post("/api/settings/favorites/toggle", json={}), "send")

    async def test_kiosk_never_gets_a_dm_from_the_message_routes(self, sign_in_as, client):
        chan_id, dm_id = await _seed_messages()
        sign_in_as("kiosk")

        listed = (await client.get("/api/messages")).json()
        assert [m["id"] for m in listed] == [chan_id]
        searched = (await client.get("/api/messages", params={"q": "hello"})).json()
        assert [m["id"] for m in searched] == [chan_id]
        since = (await client.get("/api/messages", params={"since_id": 1})).json()
        assert all(m["type"] == "CHAN" for m in since)

        _forbidden(await client.get("/api/messages", params={"type": "PRIV"}), "read_private")
        _forbidden(
            await client.get("/api/messages", params={"conversation_key": CONTACT_KEY}),
            "read_private",
        )
        _forbidden(
            await client.get(f"/api/messages/around/{dm_id}", params={"type": "PRIV"}),
            "read_private",
        )
        around = (await client.get(f"/api/messages/around/{dm_id}")).json()
        assert around["messages"] == []
        around = (await client.get(f"/api/messages/around/{chan_id}")).json()
        assert [m["id"] for m in around["messages"]] == [chan_id]

    async def test_viewer_still_gets_dms(self, sign_in_as, client):
        chan_id, dm_id = await _seed_messages()
        sign_in_as("viewer")
        listed = (await client.get("/api/messages")).json()
        assert {m["id"] for m in listed} == {chan_id, dm_id}
        response = await client.get("/api/messages", params={"conversation_key": CONTACT_KEY})
        assert [m["id"] for m in response.json()] == [dm_id]

    async def test_kiosk_unreads_list_channels_only(self, sign_in_as, client):
        await _seed_messages()
        sign_in_as("viewer")
        full = (await client.get("/api/read-state/unreads")).json()
        assert f"contact-{CONTACT_KEY}" in full["last_message_times"]

        sign_in_as("kiosk")
        unreads = (await client.get("/api/read-state/unreads")).json()
        for field, values in unreads.items():
            assert all(key.startswith("channel-") for key in values), field
        assert f"channel-{CHANNEL_KEY}" in unreads["last_message_times"]

    async def test_kiosk_contacts_carry_no_dm_activity(self, sign_in_as, client):
        await _seed_messages()
        sign_in_as("kiosk")
        [contact] = (await client.get("/api/contacts")).json()
        assert contact["public_key"] == CONTACT_KEY
        assert contact["last_contacted"] is None
        assert contact["last_read_at"] is None

        analytics = (
            await client.get("/api/contacts/analytics", params={"public_key": CONTACT_KEY})
        ).json()
        assert analytics["dm_message_count"] == 0
        assert analytics["includes_direct_messages"] is False
        assert analytics["channel_message_count"] == 1
        assert analytics["contact"]["last_contacted"] is None
        assert sum(b["message_count"] for b in analytics["weekly_activity"]) <= 1

        sign_in_as("viewer")
        analytics = (
            await client.get("/api/contacts/analytics", params={"public_key": CONTACT_KEY})
        ).json()
        assert analytics["dm_message_count"] == 1
        assert analytics["includes_direct_messages"] is True

    async def test_kiosk_sees_a_dm_packet_undecrypted(self, sign_in_as, client, monkeypatch):
        from app.repository.raw_packets import RawPacketRepository
        from app.routers import packets

        packet_id, _ = await RawPacketRepository.create(b"\x09\x00" + b"\x00" * 20, 1_700_000_000)

        async def attach(raw, message_id):
            return True, RawPacketDecryptedInfo(contact_key=CONTACT_KEY, message="secret")

        monkeypatch.setattr(packets, "attach_raw_packet_decrypted_info", attach)
        sign_in_as("viewer")
        detail = (await client.get(f"/api/packets/{packet_id}")).json()
        assert detail["decrypted_info"]["message"] == "secret"

        sign_in_as("kiosk")
        detail = (await client.get(f"/api/packets/{packet_id}")).json()
        assert detail["decrypted"] is False and detail["decrypted_info"] is None
        history = (await client.get("/api/packets/history")).json()
        assert history["items"] and all(i["decrypted_info"] is None for i in history["items"])

    async def test_kiosk_sees_channel_and_control_packets_decrypted(
        self, sign_in_as, client, monkeypatch
    ):
        from app.repository.raw_packets import RawPacketRepository
        from app.routers import packets

        # GROUP_TEXT (0x15) whose sender resolved to a contact, and a REQUEST
        # (0x01) control packet with no decryption.
        chan_id, _ = await RawPacketRepository.create(b"\x15\x00" + b"\x01" * 20, 1_700_000_000)
        control_id, _ = await RawPacketRepository.create(b"\x01\x00" + b"\x02" * 20, 1_700_000_001)

        async def attach(raw, message_id):
            if raw[0] != 0x15:
                return False, None
            return True, RawPacketDecryptedInfo(
                channel_key=CHANNEL_KEY, contact_key=CONTACT_KEY, sender="Alice", message="hi"
            )

        monkeypatch.setattr(packets, "attach_raw_packet_decrypted_info", attach)
        sign_in_as("kiosk")
        detail = (await client.get(f"/api/packets/{chan_id}")).json()
        assert detail["payload_type"] == "GROUP_TEXT"
        assert detail["decrypted"] is True
        assert detail["decrypted_info"]["contact_key"] == CONTACT_KEY
        assert detail["decrypted_info"]["message"] == "hi"
        control = (await client.get(f"/api/packets/{control_id}")).json()
        assert control["payload_type"] == "REQUEST"
        history = (await client.get("/api/packets/history")).json()
        by_id = {item["id"]: item for item in history["items"]}
        assert by_id[chan_id]["decrypted_info"]["message"] == "hi"
        assert control_id in by_id

    async def test_kiosk_reads_muted_and_pending_channels_and_packets(self, sign_in_as, client):
        chan_id, _ = await _seed_messages()
        await ChannelRepository.set_muted(CHANNEL_KEY, True, None)
        sign_in_as("kiosk")
        listed = await client.get("/api/messages", params={"conversation_key": CHANNEL_KEY})
        assert [m["id"] for m in listed.json()] == [chan_id]
        assert (await client.get("/api/channels")).status_code == 200
        assert (await client.get("/api/channels/rejected")).status_code == 200
        assert (await client.get("/api/packets/history")).status_code == 200
        assert (await client.get("/api/statistics")).status_code == 200


def _ws() -> AsyncMock:
    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _sent(ws: AsyncMock) -> list[dict]:
    return [json.loads(call.args[0]) for call in ws.send_text.await_args_list]


class TestWebSocketDelivery:
    async def _connect(self, manager: WebSocketManager, *roles: str) -> list[AsyncMock]:
        sockets = []
        for role in roles:
            ws = _ws()
            await manager.connect(ws, _principal(role) if role != "anonymous" else None)
            sockets.append(ws)
        return sockets

    async def _drain(self, manager: WebSocketManager) -> None:
        """Until every writer has sent its queue (Python 3.11 needs more loop
        iterations per frame than 3.12+), then a few more for the last send."""
        import asyncio

        for _ in range(1000):
            if all(c.queue.empty() for c in manager.active_connections.values()):
                break
            await asyncio.sleep(0)
        for _ in range(20):
            await asyncio.sleep(0)

    async def test_kiosk_gets_channel_messages_but_never_a_dm(self):
        manager = WebSocketManager()
        kiosk, viewer = await self._connect(manager, "kiosk", "viewer")
        base = {"conversation_key": CHANNEL_KEY, "text": "t", "received_at": 1, "outgoing": False}
        manager.broadcast_nowait(WsEvent.MESSAGE, {**base, "id": 1, "type": "CHAN"})
        manager.broadcast_nowait(WsEvent.MESSAGE, {**base, "id": 2, "type": "PRIV"})
        await self._drain(manager)
        assert [f["data"]["id"] for f in _sent(kiosk)] == [1]
        assert [f["data"]["id"] for f in _sent(viewer)] == [1, 2]
        for ws in (kiosk, viewer):
            await manager.disconnect(ws)

    async def test_kiosk_never_gets_a_dm_notification_event(self):
        """MSHL-138: the notification_event frame goes through the same
        per-socket DM filter as the message frames."""
        manager = WebSocketManager()
        kiosk, viewer, admin = await self._connect(manager, "kiosk", "viewer", "admin")

        def frame(n: int, event: str, **extra) -> dict:
            return {
                "version": 1,
                "event": event,
                "event_id": f"id-{n}",
                "occurred_at": 1,
                "dedup_key": f"k{n}",
                "urgent": False,
                "conversation_key": None,
                "conversation_type": None,
                "conversation_open": None,
                "data": {"n": n},
                **extra,
            }

        chan = {"conversation_key": CHANNEL_KEY, "conversation_type": "CHAN"}
        frames = [
            frame(1, "message.channel", **chan, conversation_open=True),
            frame(2, "channel.found", **chan),
            frame(3, "radio.offline"),
            frame(4, "message.dm", conversation_key=CONTACT_KEY, conversation_type="PRIV"),
            # A mention on a private-key channel.
            frame(5, "message.mention", **chan, conversation_open=False),
            frame(6, "message.keyword", conversation_key=CONTACT_KEY, conversation_type="PRIV"),
            # Any PRIV conversation_key, whatever the event.
            frame(7, "contact.first_seen", conversation_key=CONTACT_KEY, conversation_type="PRIV"),
            # A conversation_key without a type fails closed.
            frame(8, "contact.first_seen", conversation_key=CONTACT_KEY),
            frame(9, "notify.channel_failed"),
            frame(10, "not.in.the.catalogue"),
            frame(11, "message.mention", **chan, conversation_open=True),
            # The event id alone marks a DM, whatever the other fields claim.
            frame(12, "message.dm", conversation_type="CHAN"),
        ]
        for f in frames:
            manager.broadcast_nowait(WsEvent.NOTIFICATION_EVENT, f)
        manager.broadcast_nowait(WsEvent.NOTIFICATION_EVENT, "not a dict")
        await self._drain(manager)

        def ids(ws) -> list[int]:
            return [
                f["data"]["data"]["n"]
                for f in _sent(ws)
                if f["type"] == "notification_event" and isinstance(f["data"], dict)
            ]

        assert ids(kiosk) == [1, 2, 3, 11]
        assert ids(viewer) == [1, 2, 3, 4, 5, 6, 7, 8, 11, 12]
        assert ids(admin) == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
        assert all(
            f["type"] != "notification_event" or isinstance(f["data"], dict) for f in _sent(kiosk)
        )
        for ws in (kiosk, viewer, admin):
            await manager.disconnect(ws)

    async def test_the_feed_frames_reach_a_kiosk_without_any_dm(self):
        """MSHL-166: the documented frames of the Feed's four ids reach every role, a
        PRIV-derived frame never reaches a kiosk."""
        from pathlib import Path

        fixtures = json.loads(
            (Path(__file__).parent / "fixtures" / "websocket_events.json").read_text()
        )
        feed = [
            fixtures[f"notification_event_{name}"]["expected_ws_event"]["data"]
            for name in ("radio_offline", "radio_online", "contact_first_seen", "channel_found")
        ]
        dm = {
            **feed[2],
            "event": "message.dm",
            "conversation_key": CONTACT_KEY,
            "conversation_type": "PRIV",
        }
        manager = WebSocketManager()
        kiosk, viewer = await self._connect(manager, "kiosk", "viewer")
        for frame in [*feed, dm]:
            manager.broadcast_nowait(WsEvent.NOTIFICATION_EVENT, frame)
        await self._drain(manager)

        def events(ws) -> list[str]:
            return [f["data"]["event"] for f in _sent(ws) if f["type"] == "notification_event"]

        feed_ids = ["radio.offline", "radio.online", "contact.first_seen", "channel.found"]
        assert events(kiosk) == feed_ids
        assert events(viewer) == [*feed_ids, "message.dm"]
        for ws in (kiosk, viewer):
            await manager.disconnect(ws)

    async def test_a_dm_raw_packet_reaches_a_kiosk_without_its_decryption(self):
        manager = WebSocketManager()
        kiosk, viewer = await self._connect(manager, "kiosk", "viewer")
        packet = {
            "id": 1,
            "observation_id": 1,
            "timestamp": 1,
            "data": "00",
            "payload_type": "TEXT_MESSAGE",
            "decrypted": True,
            "decrypted_info": {"contact_key": CONTACT_KEY, "message": "secret"},
        }
        manager.broadcast_nowait(WsEvent.RAW_PACKET, packet)
        await self._drain(manager)
        [kiosk_frame] = _sent(kiosk)
        assert kiosk_frame["data"]["decrypted"] is False
        assert kiosk_frame["data"]["decrypted_info"] is None
        [viewer_frame] = _sent(viewer)
        assert viewer_frame["data"]["decrypted_info"]["message"] == "secret"
        for ws in (kiosk, viewer):
            await manager.disconnect(ws)

    async def test_channel_and_control_raw_packets_reach_a_kiosk_whole(self):
        manager = WebSocketManager()
        [kiosk] = await self._connect(manager, "kiosk")
        base = {"timestamp": 1, "data": "00"}
        channel = {
            **base,
            "id": 1,
            "observation_id": 1,
            "payload_type": "GROUP_TEXT",
            "decrypted": True,
            # A channel message whose sender resolved to a contact.
            "decrypted_info": {
                "channel_key": CHANNEL_KEY,
                "contact_key": CONTACT_KEY,
                "sender": "Alice",
                "message": "hi",
            },
        }
        control = {**base, "id": 2, "observation_id": 2, "payload_type": "REQUEST"}
        manager.broadcast_nowait(WsEvent.RAW_PACKET, channel)
        manager.broadcast_nowait(WsEvent.RAW_PACKET, control)
        await self._drain(manager)
        first, second = _sent(kiosk)
        assert first["data"]["decrypted"] is True
        assert first["data"]["decrypted_info"]["contact_key"] == CONTACT_KEY
        assert first["data"]["decrypted_info"]["message"] == "hi"
        assert second["data"]["payload_type"] == "REQUEST"
        await manager.disconnect(kiosk)

    async def test_admin_events_reach_admins_and_unknown_types_no_one(self):
        manager = WebSocketManager()
        admin, member, nobody = await self._connect(manager, "admin", "member", "anonymous")
        manager.broadcast_nowait(WsEvent.RADIO_WIFI, {"check": "ok"})
        manager.broadcast_nowait("not_classified_yet", {"x": 1})  # type: ignore[arg-type]
        manager.broadcast_nowait(WsEvent.SUCCESS, {"message": "ok"}, audience="read")
        await self._drain(manager)
        assert [f["type"] for f in _sent(admin)] == ["radio_wifi", "success"]
        assert [f["type"] for f in _sent(member)] == ["success"]
        assert _sent(nobody) == []
        for ws in (admin, member, nobody):
            await manager.disconnect(ws)

    async def test_a_viewer_never_receives_admin_only_events(self):
        manager = WebSocketManager()
        admin, viewer = await self._connect(manager, "admin", "viewer")
        admin_only = [member for member in WsEvent if member.permission == "admin"]
        assert admin_only, "no admin-only event left to test"
        for member in WsEvent:
            # A minimal payload: validation falls back to the raw dict, which is
            # all this test looks at (the type of each frame).
            manager.broadcast_nowait(member, {"check": "ok", "message": "m", "type": "CHAN"})
        await self._drain(manager)
        admin_types = [f["type"] for f in _sent(admin)]
        viewer_types = [f["type"] for f in _sent(viewer)]
        assert admin_types == [member.value for member in WsEvent]
        for member in admin_only:
            assert member.value not in viewer_types
        # Toasts default to admins only; the rest follows the type's permission.
        # A notification_event's audience depends on its event id: this payload
        # has none, so it fails closed to admins (see the dedicated test).
        assert viewer_types == [
            m.value
            for m in WsEvent
            if m.permission != "admin"
            and _DEFAULT_AUDIENCE.get(m) != "admin"
            and m is not WsEvent.NOTIFICATION_EVENT
        ]
        for ws in (admin, viewer):
            await manager.disconnect(ws)

    async def test_three_clients_a_kiosk_gets_channels_never_a_dm(self):
        """Live, historical and outgoing messages, to a kiosk, a viewer and an admin."""
        manager = WebSocketManager()
        kiosk, viewer, admin = await self._connect(manager, "kiosk", "viewer", "admin")
        base = {"conversation_key": CHANNEL_KEY, "text": "t", "received_at": 1}
        frames = [
            {**base, "id": 1, "type": "CHAN", "outgoing": False},
            {**base, "id": 2, "type": "PRIV", "outgoing": False, "conversation_key": CONTACT_KEY},
            {**base, "id": 3, "type": "CHAN", "outgoing": True},
            {**base, "id": 4, "type": "PRIV", "outgoing": True, "conversation_key": CONTACT_KEY},
            {**base, "id": 5, "type": "PRIV", "outgoing": False, "historical": True},
            {**base, "id": 6, "type": "CHAN", "outgoing": False, "historical": True},
            # No type at all: private (fail closed).
            {**base, "id": 7, "outgoing": False},
        ]
        for frame in frames:
            manager.broadcast_nowait(WsEvent.MESSAGE, frame)
        await manager.send_personal(kiosk, WsEvent.MESSAGE, frames[1])
        await self._drain(manager)
        assert [f["data"]["id"] for f in _sent(kiosk)] == [1, 3, 6]
        assert [f["data"]["id"] for f in _sent(viewer)] == [1, 2, 3, 4, 5, 6, 7]
        assert [f["data"]["id"] for f in _sent(admin)] == [1, 2, 3, 4, 5, 6, 7]
        for ws in (kiosk, viewer, admin):
            await manager.disconnect(ws)

    async def test_contact_frames_carry_no_dm_activity_below_read_private(self):
        manager = WebSocketManager()
        kiosk, viewer = await self._connect(manager, "kiosk", "viewer")
        contact = {
            "public_key": CONTACT_KEY,
            "name": "Bob",
            "last_contacted": 123,
            "last_read_at": 456,
            "last_seen": 789,
        }
        manager.broadcast_nowait(WsEvent.CONTACT, contact)
        manager.broadcast_nowait(
            WsEvent.CONTACT_RESOLVED, {"previous_public_key": "ab", "contact": contact}
        )
        await manager.send_personal(kiosk, WsEvent.CONTACT, contact)
        await self._drain(manager)
        kiosk_frames = _sent(kiosk)
        assert [f["type"] for f in kiosk_frames] == ["contact", "contact_resolved", "contact"]
        for seen in (
            kiosk_frames[0]["data"],
            kiosk_frames[1]["data"]["contact"],
            kiosk_frames[2]["data"],
        ):
            assert seen["last_contacted"] is None and seen["last_read_at"] is None
            assert seen["last_seen"] == 789 and seen["name"] == "Bob"
        full, resolved = _sent(viewer)
        assert full["data"]["last_contacted"] == 123
        assert resolved["data"]["contact"]["last_read_at"] == 456
        for ws in (kiosk, viewer):
            await manager.disconnect(ws)

    async def test_health_errors_and_plugin_status_reach_admins_only(self):
        manager = WebSocketManager()
        admin, member, kiosk = await self._connect(manager, "admin", "member", "kiosk")
        health = {
            "status": "ok",
            "radio_connected": True,
            "connection_info": None,
            "database_size_mb": 1.0,
            "oldest_undecrypted_timestamp": None,
            "fanout_statuses": {
                "f": {
                    "name": "MQTT",
                    "type": "mqtt",
                    "status": "error",
                    "last_error": "mqtt.lan:1883",
                }
            },
            "notification_statuses": {
                "legacy:webhook": {
                    "status": "error",
                    "last_error": "timeout (hook.lan)",
                    "last_ok_at": 1,
                }
            },
            "plugin_statuses": {"p": {"instance_id": "p", "status": "error", "last_error": "boom"}},
            "radio_proxy": RadioProxyStatus(
                enabled=True, bind="0.0.0.0", port=5000, max_clients=4, last_error="EADDRINUSE"
            ),
        }
        manager.broadcast_nowait(WsEvent.HEALTH, health)
        manager.broadcast_nowait(WsEvent.PLUGIN_STATUS, {"instance_id": "p", "last_error": "boom"})
        await self._drain(manager)
        [admin_health, admin_plugin] = _sent(admin)
        assert admin_plugin["type"] == "plugin_status"
        assert admin_health["data"]["fanout_statuses"]["f"]["last_error"] == "mqtt.lan:1883"
        assert admin_health["data"]["radio_proxy"]["last_error"] == "EADDRINUSE"
        for ws in (member, kiosk):
            [frame] = _sent(ws)
            data = frame["data"]
            assert frame["type"] == "health"
            assert data["fanout_statuses"]["f"] == {
                "name": "MQTT",
                "type": "mqtt",
                "status": "error",
                "last_error": None,
            }
            assert data["notification_statuses"]["legacy:webhook"]["last_error"] is None
            assert data["notification_statuses"]["legacy:webhook"]["status"] == "error"
            assert data["plugin_statuses"]["p"]["last_error"] is None
            assert data["plugin_statuses"]["p"]["status"] == "error"
            assert data["radio_proxy"]["last_error"] is None
            for secret in ("mqtt.lan", "hook.lan", "boom", "EADDRINUSE"):
                assert secret not in json.dumps(frame)
        for ws in (admin, member, kiosk):
            await manager.disconnect(ws)

    async def test_toasts_are_admin_only_unless_the_caller_lowers_them(self, monkeypatch):
        from app import websocket as ws_module

        manager = WebSocketManager()
        monkeypatch.setattr(ws_module, "ws_manager", manager)
        admin, member, viewer, kiosk = await self._connect(
            manager, "admin", "member", "viewer", "kiosk"
        )
        ws_module.broadcast_success("MQTT connected", "mqtt.lan:1883", code="mqtt_connected")
        ws_module.broadcast_error("Path hash mode restore failed", audience="send")
        ws_module.broadcast_success(
            "Historical decrypt complete for Bob",
            code="historical_decrypt_complete",
            audience="read_private",
        )
        ws_module.broadcast_success("Bulk historical decrypt complete", audience="read")
        await self._drain(manager)

        def messages(ws):
            return [f["data"]["message"] for f in _sent(ws)]

        assert messages(admin) == [
            "MQTT connected",
            "Path hash mode restore failed",
            "Historical decrypt complete for Bob",
            "Bulk historical decrypt complete",
        ]
        assert messages(member) == messages(admin)[1:]
        assert messages(viewer) == messages(admin)[2:]
        assert messages(kiosk) == ["Bulk historical decrypt complete"]
        for ws in (admin, member, viewer, kiosk):
            await manager.disconnect(ws)

    async def test_a_dm_ack_never_reaches_a_kiosk(self):
        from app.services.messages import broadcast_message_acked, increment_ack_and_broadcast

        manager = WebSocketManager()
        kiosk, viewer = await self._connect(manager, "kiosk", "viewer")

        def broadcast_fn(event_type, data, *, audience=None):
            manager.broadcast_nowait(event_type, data, audience=audience)

        for conversation_type, message_id in (("PRIV", 1), ("CHAN", 2), (None, 3)):
            broadcast_message_acked(
                message_id=message_id,
                ack_count=1,
                paths=None,
                packet_id=None,
                broadcast_fn=broadcast_fn,
                packet_hash="aa",
                conversation_type=conversation_type,
            )
        message = await MessageRepository.create(
            msg_type="PRIV",
            text="secret",
            conversation_key=CONTACT_KEY,
            sender_timestamp=1,
            received_at=1,
            outgoing=True,
        )
        assert message is not None
        await increment_ack_and_broadcast(message_id=message, broadcast_fn=broadcast_fn)
        await self._drain(manager)
        assert [f["data"]["message_id"] for f in _sent(kiosk)] == [2]
        assert [f["data"]["message_id"] for f in _sent(viewer)] == [1, 2, 3, message]
        for ws in (kiosk, viewer):
            await manager.disconnect(ws)

    async def test_the_websocket_route_needs_read(self, monkeypatch):
        from starlette.testclient import TestClient
        from starlette.websockets import WebSocketDisconnect

        from app.auth import middleware
        from app.main import app

        principal = _principal("admin", frozenset({"repeater_admin"}))

        async def resolve(scope, headers, via, secure):
            result = middleware._Resolution()
            result.principal = principal
            return result

        monkeypatch.setattr(middleware, "resolve", resolve)
        with TestClient(app) as test_client:
            with pytest.raises(WebSocketDisconnect) as refused:
                with test_client.websocket_connect("/api/ws"):
                    pass
        assert refused.value.code == 1008
