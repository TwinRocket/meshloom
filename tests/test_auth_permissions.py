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
from app.models import RawPacketDecryptedInfo
from app.repository import ContactRepository, MessageRepository
from app.repository.channels import ChannelRepository
from app.websocket import WebSocketManager

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
        import asyncio

        for _ in range(20):
            await asyncio.sleep(0)

    async def test_kiosk_gets_channel_messages_but_never_a_dm(self):
        manager = WebSocketManager()
        kiosk, viewer = await self._connect(manager, "kiosk", "viewer")
        base = {"conversation_key": CHANNEL_KEY, "text": "t", "received_at": 1, "outgoing": False}
        manager.broadcast_nowait("message", {**base, "id": 1, "type": "CHAN"})
        manager.broadcast_nowait("message", {**base, "id": 2, "type": "PRIV"})
        await self._drain(manager)
        assert [f["data"]["id"] for f in _sent(kiosk)] == [1]
        assert [f["data"]["id"] for f in _sent(viewer)] == [1, 2]
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
        manager.broadcast_nowait("raw_packet", packet)
        await self._drain(manager)
        [kiosk_frame] = _sent(kiosk)
        assert kiosk_frame["data"]["decrypted"] is False
        assert kiosk_frame["data"]["decrypted_info"] is None
        [viewer_frame] = _sent(viewer)
        assert viewer_frame["data"]["decrypted_info"]["message"] == "secret"
        for ws in (kiosk, viewer):
            await manager.disconnect(ws)

    async def test_admin_events_and_unknown_types_reach_admins_only(self):
        manager = WebSocketManager()
        admin, member, nobody = await self._connect(manager, "admin", "member", "anonymous")
        manager.broadcast_nowait("radio_wifi", {"check": "ok"})
        manager.broadcast_nowait("not_classified_yet", {"x": 1})
        manager.broadcast_nowait("success", {"message": "ok"})
        await self._drain(manager)
        assert [f["type"] for f in _sent(admin)] == ["radio_wifi", "not_classified_yet", "success"]
        assert [f["type"] for f in _sent(member)] == ["success"]
        assert _sent(nobody) == []
        for ws in (admin, member, nobody):
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
