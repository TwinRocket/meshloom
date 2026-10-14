"""Tests for the firmware custom-vars radio endpoints."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from meshcore import EventType
from meshcore.events import Event

from app.routers.radio import radio_manager


def _mc(get_events, set_event=None):
    mc = MagicMock()
    mc.commands.get_custom_vars = AsyncMock(side_effect=get_events)
    mc.commands.set_custom_var = AsyncMock(return_value=set_event)
    return mc


def _patched(mc):
    @asynccontextmanager
    async def _op(*_a, **_kw):
        yield mc

    return (
        patch.object(radio_manager, "require_connected", return_value=mc),
        patch.object(radio_manager, "radio_operation", _op),
    )


async def _call(client, mc, method, url, **kw):
    p1, p2 = _patched(mc)
    with p1, p2:
        return await client.request(method, url, **kw)


def _vars(d):
    return Event(EventType.CUSTOM_VARS, d)


class TestCustomVars:
    @pytest.mark.asyncio
    async def test_get_supported(self, client):
        mc = _mc([_vars({"gps": "1"})])
        r = await _call(client, mc, "GET", "/api/radio/custom-vars")
        assert r.status_code == 200
        assert r.json() == {"supported": True, "vars": {"gps": "1"}}

    @pytest.mark.asyncio
    async def test_get_unsupported_on_error(self, client):
        mc = _mc([Event(EventType.ERROR, {})])
        r = await _call(client, mc, "GET", "/api/radio/custom-vars")
        assert r.json() == {"supported": False, "vars": {}}

    @pytest.mark.asyncio
    async def test_get_unsupported_on_none(self, client):
        mc = _mc([None])
        r = await _call(client, mc, "GET", "/api/radio/custom-vars")
        assert r.json() == {"supported": False, "vars": {}}

    @pytest.mark.asyncio
    async def test_get_empty(self, client):
        mc = _mc([_vars({})])
        r = await _call(client, mc, "GET", "/api/radio/custom-vars")
        assert r.json() == {"supported": True, "vars": {}}

    @pytest.mark.asyncio
    async def test_put_ok_rereads(self, client):
        mc = _mc([_vars({"gps": "0"})], Event(EventType.OK, {}))
        r = await _call(client, mc, "PUT", "/api/radio/custom-vars/gps", json={"value": "0"})
        assert r.status_code == 200
        assert r.json() == {"supported": True, "vars": {"gps": "0"}}
        mc.commands.set_custom_var.assert_awaited_once_with("gps", "0")
        mc.commands.get_custom_vars.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_put_radio_error(self, client):
        mc = _mc([_vars({})], Event(EventType.ERROR, {}))
        r = await _call(client, mc, "PUT", "/api/radio/custom-vars/gps", json={"value": "1"})
        assert r.status_code == 422
        assert r.json()["detail"] == "Radio rejected setting gps"

    @pytest.mark.asyncio
    async def test_put_none_is_rejected(self, client):
        mc = _mc([_vars({})], None)
        r = await _call(client, mc, "PUT", "/api/radio/custom-vars/gps", json={"value": "1"})
        assert r.status_code == 422

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", ["GPS", "a-b", "x" * 33, "a.b"])
    async def test_put_invalid_name(self, client, name):
        mc = _mc([_vars({})], Event(EventType.OK, {}))
        r = await _call(client, mc, "PUT", f"/api/radio/custom-vars/{name}", json={"value": "1"})
        assert r.status_code == 400
        mc.commands.set_custom_var.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["a:b", "a,b", "x" * 65, "", "café", "a\nb"])
    async def test_put_invalid_value(self, client, value):
        mc = _mc([_vars({})], Event(EventType.OK, {}))
        r = await _call(client, mc, "PUT", "/api/radio/custom-vars/gps", json={"value": value})
        assert r.status_code == 422
        mc.commands.set_custom_var.assert_not_awaited()
