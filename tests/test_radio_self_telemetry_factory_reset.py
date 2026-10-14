"""Tests for the radio self-telemetry and factory-reset endpoints."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType
from meshcore.events import Event

from app.routers.radio import radio_manager


def _mc(telemetry=None, battery=None, send=None, name="Alpha"):
    mc = MagicMock()
    mc.self_info = {"name": name, "public_key": "ab" * 32}
    mc.commands.get_self_telemetry = AsyncMock(return_value=telemetry)
    mc.commands.get_bat = AsyncMock(return_value=battery)
    mc.commands.send = AsyncMock(return_value=send)
    return mc


async def _call(client, mc, method, url, **kw):
    @asynccontextmanager
    async def _op(*_a, **_kw):
        yield mc

    with (
        patch.object(radio_manager, "require_connected", return_value=mc),
        patch.object(radio_manager, "radio_operation", _op),
    ):
        return await client.request(method, url, **kw)


LPP = Event(
    EventType.TELEMETRY_RESPONSE,
    {"pubkey_pre": "ab", "lpp": [{"channel": 1, "type": "voltage", "value": 4.1}]},
)


class TestSelfTelemetry:
    @pytest.mark.asyncio
    async def test_full(self, client):
        bat = Event(EventType.BATTERY, {"level": 4100, "used_kb": 10, "total_kb": 100})
        r = await _call(client, _mc(LPP, bat), "GET", "/api/radio/self-telemetry")
        assert r.status_code == 200
        assert r.json() == {
            "sensors": [{"channel": 1, "type_name": "voltage", "value": 4.1}],
            "battery_mv": 4100,
            "storage_used_kb": 10,
            "storage_total_kb": 100,
        }

    @pytest.mark.asyncio
    async def test_telemetry_error(self, client):
        bat = Event(EventType.BATTERY, {"level": 4000})
        r = await _call(
            client, _mc(Event(EventType.ERROR, {}), bat), "GET", "/api/radio/self-telemetry"
        )
        assert r.json()["sensors"] == []
        assert r.json()["battery_mv"] == 4000

    @pytest.mark.asyncio
    async def test_battery_error(self, client):
        r = await _call(
            client, _mc(LPP, Event(EventType.ERROR, {})), "GET", "/api/radio/self-telemetry"
        )
        body = r.json()
        assert len(body["sensors"]) == 1
        assert body["battery_mv"] is None
        assert body["storage_used_kb"] is None
        assert body["storage_total_kb"] is None

    @pytest.mark.asyncio
    async def test_battery_without_storage(self, client):
        bat = Event(EventType.BATTERY, {"level": 3900})
        r = await _call(client, _mc(None, bat), "GET", "/api/radio/self-telemetry")
        assert r.json() == {
            "sensors": [],
            "battery_mv": 3900,
            "storage_used_kb": None,
            "storage_total_kb": None,
        }


class TestFactoryReset:
    @pytest.mark.asyncio
    async def test_name_mismatch(self, client):
        mc = _mc()
        r = await _call(
            client, mc, "POST", "/api/radio/factory-reset", json={"confirm_name": "alpha "}
        )
        assert r.status_code == 400
        assert r.json()["detail"] == "Confirmation does not match the radio name"
        mc.commands.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_radio_name(self, client):
        mc = _mc(name="")
        r = await _call(client, mc, "POST", "/api/radio/factory-reset", json={"confirm_name": ""})
        assert r.status_code == 400
        mc.commands.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_ok(self, client):
        mc = _mc(send=Event(EventType.OK, {}))
        r = await _call(
            client, mc, "POST", "/api/radio/factory-reset", json={"confirm_name": "Alpha"}
        )
        assert r.status_code == 200
        assert r.json()["status"] == "reset_sent"
        assert mc.commands.send.await_args.args[0] == b"\x33reset"

    @pytest.mark.asyncio
    async def test_no_reply(self, client):
        mc = _mc(send=None)
        r = await _call(
            client, mc, "POST", "/api/radio/factory-reset", json={"confirm_name": "Alpha"}
        )
        assert r.status_code == 200
        assert r.json()["status"] == "reset_sent"

    @pytest.mark.asyncio
    async def test_connection_drop(self, client):
        mc = _mc()
        mc.commands.send = AsyncMock(side_effect=ConnectionResetError())
        r = await _call(
            client, mc, "POST", "/api/radio/factory-reset", json={"confirm_name": "Alpha"}
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_error(self, client):
        mc = _mc(send=Event(EventType.ERROR, {}))
        r = await _call(
            client, mc, "POST", "/api/radio/factory-reset", json={"confirm_name": "Alpha"}
        )
        assert r.status_code == 422
        assert r.json()["detail"] == "Radio refused the factory reset"
