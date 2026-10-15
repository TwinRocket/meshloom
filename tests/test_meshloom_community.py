"""Phase 3 Meshloom Stats community backend."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.fanout.community_mqtt import (
    _DEFAULT_BROKER,
    CommunityMqttPublisher,
    _generate_jwt_token,
    reset_shared_radio_stats_cache,
)
from app.fanout.manager import FanoutManager, is_reserved_fanout_id
from app.fanout.meshloom_stats import MeshloomStatsPublisher, _state_to_settings
from app.models import CommunityHashtagPut, CommunityIataBindRequest
from app.repository.fanout import FanoutConfigRepository
from app.routers.community import (
    get_community,
    get_community_stats,
    get_hashtags,
    put_me_hashtags,
    put_me_iata,
)
from app.routers.fanout import delete_fanout_config, list_fanout_configs, update_fanout_config
from app.services.directory import get_directory_node_reach, list_directory_map_nodes
from app.services.meshloom_community import (
    DEFAULT_API_BASE,
    DEFAULT_BROKER_HOST,
    DEFAULT_WEBSOCKET_PATH,
    MQTT_KEEPALIVE_SECONDS,
    SYSTEM_MESHLOOM_STATS_ID,
    CommunityEffective,
    _json_or_http_error,
    get_community_effective,
    mint_stats_jwt,
    reset_stats_client_for_tests,
    sample_quota_blocked,
    stats_request,
    unwrap_directory_envelope,
    update_community,
    upload_hashtag_sample,
)


def _jwt_payload(token: str) -> dict:
    import base64

    payload_b64 = token.split(".")[1]
    padded = payload_b64 + "=" * (4 - len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def _make_test_keys() -> tuple[bytes, bytes]:
    import hashlib
    import os

    import nacl.bindings

    seed = os.urandom(32)
    expanded = hashlib.sha512(seed).digest()
    scalar = bytearray(expanded[:32])
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    private_key = bytes(scalar) + expanded[32:]
    public_key = nacl.bindings.crypto_scalarmult_ed25519_base_noclamp(bytes(scalar))
    return private_key, public_key


def _mock_radio_operation(mc_mock):
    @asynccontextmanager
    async def _op(*_args, **_kwargs):
        yield mc_mock

    return _op


class TestFanoutHidesSystemPublisher:
    @pytest.mark.asyncio
    async def test_list_omits_system_module(self, test_db):
        await FanoutConfigRepository.create(
            config_type="mqtt_private",
            name="Private",
            config={"broker_host": "localhost", "broker_port": 1883},
            scope={"messages": "all", "raw_packets": "none"},
            enabled=False,
        )
        listed = await list_fanout_configs()
        assert all(row["id"] != SYSTEM_MESHLOOM_STATS_ID for row in listed)
        assert all(not is_reserved_fanout_id(row["id"]) for row in listed)

    def test_get_statuses_omits_system_id(self):
        manager = FanoutManager()
        mod = MagicMock()
        mod.status = "connected"
        mod.last_error = None
        manager._modules[SYSTEM_MESHLOOM_STATS_ID] = (mod, {})
        with patch(
            "app.repository.fanout._configs_cache",
            {SYSTEM_MESHLOOM_STATS_ID: {"name": "Meshloom Stats", "type": "system"}},
        ):
            assert SYSTEM_MESHLOOM_STATS_ID not in manager.get_statuses()

    @pytest.mark.asyncio
    async def test_patch_reserved_id_is_403(self, test_db):
        from app.routers.fanout import FanoutConfigUpdate

        with pytest.raises(HTTPException) as exc:
            await update_fanout_config(SYSTEM_MESHLOOM_STATS_ID, FanoutConfigUpdate(enabled=False))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_delete_reserved_id_is_403(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await delete_fanout_config(SYSTEM_MESHLOOM_STATS_ID)
        assert exc.value.status_code == 403


class TestCommunityOffHasNoDirectory:
    @pytest.mark.asyncio
    async def test_off_does_not_call_stats_and_serves_nothing(self, test_db):
        from app.services.directory import reset_directory_nodes_cache

        reset_directory_nodes_cache()
        with patch("app.services.meshloom_community.stats_request", new=AsyncMock()) as stats_req:
            result = await list_directory_map_nodes()
        assert result.nodes == []
        stats_req.assert_not_called()


class TestCommunityOnDirectoryUnavailable:
    @pytest.mark.asyncio
    async def test_stats_unavailable_is_503_not_empty(self, test_db):
        await update_community(enabled=True, iata="CDG")
        with patch(
            "app.services.meshloom_community.stats_json",
            new=AsyncMock(
                return_value={"status": "unavailable", "freshness": {}, "sources": [], "data": {}}
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_directory_node_reach("ab" * 32)
        assert exc.value.status_code == 503
        assert "unavailable" in str(exc.value.detail).lower()


class TestJwtIataClaim:
    def test_letsmesh_token_omits_iata(self):
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(private_key, public_key)
        payload = _jwt_payload(token)
        assert "iata" not in payload
        assert payload["aud"] == _DEFAULT_BROKER

    def test_stats_mqtt_token_includes_iata(self):
        private_key, public_key = _make_test_keys()
        token = _generate_jwt_token(
            private_key, public_key, audience="mqtt.example.invalid", iata="CDG"
        )
        payload = _jwt_payload(token)
        assert payload["iata"] == "CDG"
        assert payload["aud"] == "mqtt.example.invalid"

    def test_mint_stats_jwt_requires_iata_when_asked(self):
        private_key, public_key = _make_test_keys()
        with (
            patch("app.keystore.get_private_key", return_value=private_key),
            patch("app.keystore.get_public_key", return_value=public_key),
        ):
            with pytest.raises(HTTPException) as exc:
                mint_stats_jwt(audience="api.example.invalid", iata="", require_iata=True)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_directory_auth_does_not_require_iata(self):
        state = CommunityEffective(
            enabled=True,
            iata="",
            broker_host=DEFAULT_BROKER_HOST,
            api_base=DEFAULT_API_BASE,
            api_audience="api.example.invalid",
            mqtt_audience="mqtt.example.invalid",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "app.services.meshloom_community.get_community_effective",
                new=AsyncMock(return_value=state),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", return_value="tok") as mint,
            patch("app.services.meshloom_community.httpx.AsyncClient", return_value=mock_client),
        ):
            await stats_request("GET", "/v1/directory/nodes", auth=True)
        mint.assert_called_once()
        assert mint.call_args.kwargs["require_iata"] is False
        assert mint.call_args.kwargs["iata"] == ""

    @pytest.mark.asyncio
    async def test_me_auth_requires_iata(self):
        state = CommunityEffective(
            enabled=True,
            iata="CDG",
            broker_host=DEFAULT_BROKER_HOST,
            api_base=DEFAULT_API_BASE,
            api_audience="api.example.invalid",
            mqtt_audience="mqtt.example.invalid",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "app.services.meshloom_community.get_community_effective",
                new=AsyncMock(return_value=state),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", return_value="tok") as mint,
            patch("app.services.meshloom_community.httpx.AsyncClient", return_value=mock_client),
        ):
            await stats_request("GET", "/v1/me/stats", auth=True)
        mint.assert_called_once()
        assert mint.call_args.kwargs["require_iata"] is True
        assert mint.call_args.kwargs["iata"] == "CDG"

    @pytest.mark.asyncio
    async def test_first_iata_bind_mints_with_requested_code(self):
        state = CommunityEffective(
            enabled=True,
            iata="",
            broker_host=DEFAULT_BROKER_HOST,
            api_base=DEFAULT_API_BASE,
            api_audience="api.example.invalid",
            mqtt_audience="mqtt.example.invalid",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "app.services.meshloom_community.get_community_effective",
                new=AsyncMock(return_value=state),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", return_value="tok") as mint,
            patch("app.services.meshloom_community.httpx.AsyncClient", return_value=mock_client),
        ):
            await stats_request("PUT", "/v1/me/iata", auth=True, iata="lys")
        mint.assert_called_once()
        assert mint.call_args.kwargs["require_iata"] is True
        assert mint.call_args.kwargs["iata"] == "LYS"

    @pytest.mark.asyncio
    async def test_hashtag_sample_auth_requires_iata(self):
        state = CommunityEffective(
            enabled=True,
            iata="BOD",
            broker_host=DEFAULT_BROKER_HOST,
            api_base=DEFAULT_API_BASE,
            api_audience="api.example.invalid",
            mqtt_audience="mqtt.example.invalid",
        )
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        with (
            patch(
                "app.services.meshloom_community.get_community_effective",
                new=AsyncMock(return_value=state),
            ),
            patch("app.services.meshloom_community.mint_stats_jwt", return_value="tok") as mint,
            patch("app.services.meshloom_community.httpx.AsyncClient", return_value=mock_client),
        ):
            await stats_request("POST", "/v1/hashtags/samples", auth=True)
        mint.assert_called_once()
        assert mint.call_args.kwargs["require_iata"] is True
        assert mint.call_args.kwargs["iata"] == "BOD"


def _stats_response(status_code: int, detail: object | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    if detail is None:
        response.json.side_effect = ValueError("not json")
    else:
        response.json.return_value = {"detail": detail}
    return response


class TestStats429Mapping:
    def test_sample_quota_is_not_iata_cap(self):
        with pytest.raises(HTTPException) as exc:
            _json_or_http_error(
                _stats_response(429, "hashtag sample quota"),
                path="/v1/hashtags/samples",
            )
        assert exc.value.status_code == 429
        assert exc.value.detail == "Community hashtag sample quota reached"

    def test_iata_cap_keeps_existing_message(self):
        with pytest.raises(HTTPException) as exc:
            _json_or_http_error(
                _stats_response(429, "IATA change cap exceeded"),
                path="/v1/me/iata",
            )
        assert exc.value.detail == "Community IATA change cap reached"

    def test_unknown_429_is_generic_not_iata(self):
        with pytest.raises(HTTPException) as exc:
            _json_or_http_error(
                _stats_response(429, "public stats rate limit"),
                path="/v1/community/stats",
            )
        assert exc.value.detail == "Community rate limit reached"

    def test_sample_path_fallback_when_body_missing(self):
        with pytest.raises(HTTPException) as exc:
            _json_or_http_error(_stats_response(429), path="/v1/hashtags/samples")
        assert exc.value.detail == "Community hashtag sample quota reached"


class TestSampleQuotaBackoff:
    @pytest.mark.asyncio
    async def test_sample_429_skips_later_uploads_without_http(self):
        reset_stats_client_for_tests()
        with patch(
            "app.services.meshloom_community.stats_json",
            new=AsyncMock(
                side_effect=HTTPException(
                    status_code=429, detail="Community hashtag sample quota reached"
                )
            ),
        ) as stats:
            assert await upload_hashtag_sample("ab", "ab001122") is False
            assert sample_quota_blocked()
            assert await upload_hashtag_sample("cd", "cd001122") is False
        assert stats.await_count == 1


class TestPublisherRequiresIata:
    def test_not_configured_without_iata(self):
        pub = MeshloomStatsPublisher()
        pub._settings = _state_to_settings(
            CommunityEffective(
                enabled=True,
                iata="",
                broker_host=DEFAULT_BROKER_HOST,
                api_base=DEFAULT_API_BASE,
                api_audience="api.example.invalid",
                mqtt_audience="mqtt.example.invalid",
            )
        )
        with (
            patch("app.keystore.get_public_key", return_value=b"\x01" * 32),
            patch("app.keystore.has_private_key", return_value=True),
        ):
            assert pub._is_configured() is False

    def test_configured_with_iata_and_keys(self):
        pub = MeshloomStatsPublisher()
        pub._settings = _state_to_settings(
            CommunityEffective(
                enabled=True,
                iata="CDG",
                broker_host=DEFAULT_BROKER_HOST,
                api_base=DEFAULT_API_BASE,
                api_audience="api.example.invalid",
                mqtt_audience="mqtt.example.invalid",
            )
        )
        with (
            patch("app.keystore.get_public_key", return_value=b"\x01" * 32),
            patch("app.keystore.has_private_key", return_value=True),
        ):
            assert pub._is_configured() is True


class TestFirstIataBind:
    @pytest.mark.asyncio
    async def test_put_me_iata_forwards_requested_code(self, test_db):
        await update_community(enabled=True)
        payload = {
            "iata": "LYS",
            "concordance": "unknown",
            "honored_for_buckets": False,
            "distance_km": None,
        }
        with (
            patch(
                "app.services.meshloom_community.stats_json",
                new=AsyncMock(return_value=payload),
            ) as stats,
            patch("app.services.observer_position.radio_self_info", return_value=None),
        ):
            result = await put_me_iata(CommunityIataBindRequest(iata="lys"))
        assert stats.await_args.kwargs["iata"] == "lys"
        # No radio position: nothing about a position goes out.
        assert stats.await_args.kwargs["json_body"] == {"iata": "LYS"}
        assert result.iata == "LYS"
        state = await get_community_effective()
        assert state.iata == "LYS"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("policy", "expected"),
        [
            (0, {"lat": 45.76, "lon": 4.84, "position_precision": "rounded"}),
            (None, {"lat": 45.76, "lon": 4.84, "position_precision": "rounded"}),
            (1, {"lat": 45.764043, "lon": 4.835659, "position_precision": "exact"}),
        ],
    )
    async def test_bind_sends_only_the_publishable_position(self, test_db, policy, expected):
        await update_community(enabled=True, iata="LYS")
        payload = {"iata": "LYS", "concordance": "ok", "honored_for_buckets": True}
        info = {"adv_lat": 45.76404321, "adv_lon": 4.83565912, "adv_loc_policy": policy}
        with (
            patch(
                "app.services.meshloom_community.stats_json",
                new=AsyncMock(return_value=payload),
            ) as stats,
            patch("app.services.observer_position.radio_self_info", return_value=info),
        ):
            # A client cannot supply its own position any more: lat/lon are ignored.
            await put_me_iata(
                CommunityIataBindRequest.model_validate({"iata": "LYS", "lat": 1.5, "lon": 2.5})
            )
        assert stats.await_args.kwargs["json_body"] == {"iata": "LYS", **expected}

    def test_bind_request_has_no_position_fields(self):
        assert set(CommunityIataBindRequest.model_fields) == {"iata", "enable"}


class TestCommunityStatusAndProxies:
    @pytest.mark.asyncio
    async def test_get_status_when_off(self, test_db):
        status = await get_community()
        assert status.enabled is False
        assert status.publisher_configured is False
        assert status.state == "off"
        # The Community addresses are neither settable nor shown.
        assert set(status.model_dump()) == {
            "enabled",
            "iata",
            "publisher_configured",
            "publisher_connected",
            "state",
            "opt_out_pending",
        }
        assert status.opt_out_pending is None

    @pytest.mark.asyncio
    async def test_stats_proxy_forbidden_when_off(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await get_community_stats()
        assert exc.value.status_code == 403


class TestCommunityHashtagProxies:
    @pytest.mark.asyncio
    async def test_get_hashtags_is_anonymous_and_global(self):
        payload = {"hashtags": [{"name": "meshcore", "hash_byte": "d9"}]}
        with patch(
            "app.routers.community.stats_json",
            new=AsyncMock(return_value=payload),
        ) as stats:
            result = await get_hashtags()

        stats.assert_awaited_once_with("GET", "/v1/hashtags", auth=False)
        assert result.hashtags[0].name == "meshcore"
        assert result.hashtags[0].hash_byte == "d9"

    def test_per_iata_hashtags_route_is_removed(self):
        from app.main import app

        paths = app.openapi()["paths"]
        assert "/api/community/hashtags" in paths
        assert "/api/community/iata/{code}/hashtags" not in paths

    @pytest.mark.asyncio
    async def test_old_per_iata_hashtags_path_is_404_without_calling_community(self, client):
        """A browser tab still running a 4.13.2-or-older frontend asks this path.

        It gets a plain 404 (its finder swallows the error) and Community is
        never contacted, so its 410 tombstone sees no traffic from new servers.
        """
        stats = AsyncMock()
        with patch("app.routers.community.stats_json", new=stats):
            async with client:
                response = await client.get("/api/community/iata/LYS/hashtags")

        assert response.status_code == 404
        stats.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_restart_route_replaces_the_french_path(self, client):
        """``/live/restart`` reaches the handler; the old ``/live/relancer`` path is gone."""
        from app.main import app

        status = {"session_id": None, "close_code": None, "opted_out": False, "connected": True}
        restart = AsyncMock(return_value=status)
        with patch("app.routers.community.restart_live", new=restart):
            async with client:
                response = await client.post("/api/community/live/restart")
                gone = await client.post("/api/community/live/relancer")

        assert response.status_code == 200
        assert response.json()["connected"] is True
        restart.assert_awaited_once_with()
        assert gone.status_code in (404, 405)
        paths = app.openapi()["paths"]
        assert "/api/community/live/restart" in paths
        assert "/api/community/live/relancer" not in paths

    @pytest.mark.asyncio
    async def test_put_me_hashtags_is_authenticated(self):
        payload = {"hashtags": [{"name": "meshcore", "hash_byte": "d9"}]}
        with patch(
            "app.routers.community.stats_json",
            new=AsyncMock(return_value=payload),
        ) as stats:
            result = await put_me_hashtags(CommunityHashtagPut(names=["meshcore"]))

        stats.assert_awaited_once_with(
            "PUT",
            "/v1/me/hashtags",
            auth=True,
            json_body={"names": ["meshcore"]},
        )
        assert result.hashtags[0].name == "meshcore"


class TestKeepaliveAndJwtKwargs:
    def test_keepalive_is_30(self):
        private_key, public_key = _make_test_keys()
        pub = MeshloomStatsPublisher()
        settings = _state_to_settings(
            CommunityEffective(
                enabled=True,
                iata="CDG",
                broker_host=DEFAULT_BROKER_HOST,
                api_base=DEFAULT_API_BASE,
                api_audience="api.example.invalid",
                mqtt_audience="mqtt.example.invalid",
            )
        )
        with (
            patch("app.keystore.get_private_key", return_value=private_key),
            patch("app.keystore.get_public_key", return_value=public_key),
            patch(
                "app.services.radio_runtime.radio_runtime",
                SimpleNamespace(meshcore=None),
            ),
        ):
            kwargs = pub._build_client_kwargs(settings)
        assert kwargs["keepalive"] == MQTT_KEEPALIVE_SECONDS
        assert kwargs["keepalive"] == 30
        assert kwargs["websocket_path"] == DEFAULT_WEBSOCKET_PATH
        assert kwargs["websocket_path"] == "/mqtt"
        payload = _jwt_payload(kwargs["password"])
        assert payload["iata"] == "CDG"
        assert payload["aud"] == "mqtt.example.invalid"


class TestSharedRadioStatsCache:
    @pytest.mark.asyncio
    async def test_second_publisher_does_not_call_get_stats(self):
        from meshcore.events import Event, EventType

        reset_shared_radio_stats_cache()
        letsmesh = CommunityMqttPublisher()
        stats_pub = MeshloomStatsPublisher()
        mc_mock = MagicMock()
        mc_mock.commands.get_stats_core = AsyncMock(
            return_value=Event(EventType.STATS_CORE, {"battery_mv": 4200})
        )
        mc_mock.commands.get_stats_radio = AsyncMock(
            return_value=Event(EventType.STATS_RADIO, {"noise_floor": -120})
        )
        with patch("app.radio.radio_manager") as mock_rm:
            mock_rm.radio_operation = _mock_radio_operation(mc_mock)
            first = await letsmesh._fetch_stats()
            second = await stats_pub._fetch_stats()
        assert first is not None
        assert second == first
        assert mc_mock.commands.get_stats_core.await_count == 1
        assert mc_mock.commands.get_stats_radio.await_count == 1
        reset_shared_radio_stats_cache()


class TestEnvelopeMapping:
    def test_complete_returns_data(self):
        data = unwrap_directory_envelope(
            {"status": "complete", "freshness": {}, "sources": [], "data": {"nodes": []}}
        )
        assert data == {"nodes": []}

    def test_unavailable_is_503(self):
        with pytest.raises(HTTPException) as exc:
            unwrap_directory_envelope(
                {"status": "unavailable", "freshness": {}, "sources": [], "data": {"nodes": []}}
            )
        assert exc.value.status_code == 503

    def test_malformed_envelope_is_502(self):
        with pytest.raises(HTTPException) as exc:
            unwrap_directory_envelope({"status": "weird"})
        assert exc.value.status_code == 502

    def test_defaults_are_official_meshloom_hosts(self):
        assert DEFAULT_BROKER_HOST == "mqtt.meshloom.app"
        assert DEFAULT_API_BASE == "https://api.meshloom.app"
        assert DEFAULT_WEBSOCKET_PATH == "/mqtt"


class TestOfficialHostDefaults:
    @pytest.mark.asyncio
    async def test_env_no_longer_overrides_the_addresses(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY_BROKER_HOST", "mqtt.internal.test")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_API_BASE", "https://api.internal.test")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_IATA", "CDG")
        state = await get_community_effective()
        assert state.broker_host == "mqtt.meshloom.app"
        assert state.api_base == "https://api.meshloom.app"
        assert state.iata == ""

    @pytest.mark.asyncio
    async def test_constants_are_read_at_call_time(self, test_db, monkeypatch):
        from app.services import meshloom_community

        monkeypatch.setattr(meshloom_community, "DEFAULT_BROKER_HOST", "mqtt.internal.test")
        monkeypatch.setattr(meshloom_community, "DEFAULT_API_BASE", "https://api.internal.test/")
        state = await get_community_effective()
        assert state.broker_host == "mqtt.internal.test"
        assert state.api_base == "https://api.internal.test"
        assert state.mqtt_audience == "mqtt.internal.test"
        assert state.api_audience == "api.internal.test"
        assert _state_to_settings(state).community_mqtt_broker_host == "mqtt.internal.test"

    def test_addresses_cannot_be_set(self):
        import inspect

        from app.models import CommunityUpdate

        assert set(CommunityUpdate.model_fields) == {"enabled", "iata", "opt_out_reason"}
        params = inspect.signature(update_community).parameters
        assert "broker_host" not in params and "api_base" not in params
        body = CommunityUpdate.model_validate({"enabled": True, "broker_host": "mqtt.evil.test"})
        assert "broker_host" not in body.model_dump()


class TestAirportLookup:
    """``GET /api/community/airports``: the bundled list, never a request."""

    def test_places_a_code_without_any_request(self, monkeypatch):
        from app.services import meshloom_community

        client_cls = MagicMock()
        monkeypatch.setattr(meshloom_community.httpx, "AsyncClient", client_cls)

        (hit,) = meshloom_community.lookup_airport(" cdg ")
        assert hit.iata == "CDG"
        assert hit.lat == pytest.approx(49.0128, abs=0.01)
        assert hit.lon == pytest.approx(2.55, abs=0.01)
        # A metropolitan area code is placed too (Live regions), at its airports' mean.
        assert meshloom_community.lookup_airport("PAR")
        assert meshloom_community.lookup_airport("Lyon") == []
        assert meshloom_community.lookup_airport("L") == []
        assert meshloom_community.lookup_airport("ZZZ") == []
        client_cls.assert_not_called()

    def test_retired_airport_search_is_gone(self):
        from app.services import meshloom_community

        assert not hasattr(meshloom_community, "AIRPORT_SEARCH_URL")
        assert not hasattr(meshloom_community, "search_community_airports")
