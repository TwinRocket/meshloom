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
from app.models import CommunityHashtagPut, CommunityIataBindRequest, CommunityUpdate
from app.repository.fanout import FanoutConfigRepository
from app.routers.community import (
    get_community,
    get_community_stats,
    get_iata_hashtags,
    patch_community,
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
    get_community_effective,
    mint_stats_jwt,
    seed_community_from_env,
    stats_request,
    unwrap_directory_envelope,
    update_community,
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
    async def test_stats_unavailable_is_500_not_empty(self, test_db):
        await update_community(enabled=True, iata="CDG")
        with patch(
            "app.services.meshloom_community.stats_json",
            new=AsyncMock(
                return_value={"status": "unavailable", "freshness": {}, "sources": [], "data": {}}
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await get_directory_node_reach("ab" * 32)
        assert exc.value.status_code == 500
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
            locked=False,
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
            locked=False,
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
            locked=False,
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
            locked=False,
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


class TestPublisherRequiresIata:
    def test_not_configured_without_iata(self):
        pub = MeshloomStatsPublisher()
        pub._settings = _state_to_settings(
            CommunityEffective(
                enabled=True,
                locked=False,
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
                locked=False,
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
        with patch(
            "app.routers.community.stats_json",
            new=AsyncMock(return_value=payload),
        ) as stats:
            result = await put_me_iata(CommunityIataBindRequest(iata="lys"))
        assert stats.await_args.kwargs["iata"] == "lys"
        assert result.iata == "LYS"
        state = await get_community_effective()
        assert state.iata == "LYS"


class TestCommunityStatusAndProxies:
    @pytest.mark.asyncio
    async def test_get_status_when_off(self, test_db):
        status = await get_community()
        assert status.enabled is False
        assert status.publisher_configured is False
        assert status.broker_host == DEFAULT_BROKER_HOST
        assert status.api_base == DEFAULT_API_BASE

    @pytest.mark.asyncio
    async def test_stats_proxy_forbidden_when_off(self, test_db):
        with pytest.raises(HTTPException) as exc:
            await get_community_stats()
        assert exc.value.status_code == 403


class TestCommunityHashtagProxies:
    @pytest.mark.asyncio
    async def test_get_iata_hashtags_is_anonymous(self):
        payload = {"hashtags": [{"name": "meshcore", "hash_byte": "d9"}]}
        with patch(
            "app.routers.community.stats_json",
            new=AsyncMock(return_value=payload),
        ) as stats:
            result = await get_iata_hashtags("cdg")

        stats.assert_awaited_once_with("GET", "/v1/iata/CDG/hashtags", auth=False)
        assert result.hashtags[0].name == "meshcore"
        assert result.hashtags[0].hash_byte == "d9"

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


class TestCommunityLocked:
    @pytest.mark.asyncio
    async def test_locked_env_rejects_enable(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY_LOCKED", "1")
        with pytest.raises(HTTPException) as exc:
            await patch_community(CommunityUpdate(enabled=True, iata="CDG"))
        assert exc.value.status_code == 403
        state = await get_community_effective()
        assert state.enabled is False


class TestExistingDbDoesNotAutoEnable:
    @pytest.mark.asyncio
    async def test_existing_install_stays_off(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY", "1")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_IATA", "CDG")
        await seed_community_from_env(new_install=False)
        state = await get_community_effective()
        assert state.enabled is False

    @pytest.mark.asyncio
    async def test_new_install_defaults_on(self, test_db, monkeypatch):
        monkeypatch.delenv("MESHLOOM_COMMUNITY", raising=False)
        await seed_community_from_env(new_install=True)
        state = await get_community_effective()
        assert state.enabled is True

    @pytest.mark.asyncio
    async def test_new_install_can_seed_enable(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY", "1")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_IATA", "lyo")
        await seed_community_from_env(new_install=True)
        state = await get_community_effective()
        assert state.enabled is True
        assert state.iata == "LYO"

    @pytest.mark.asyncio
    async def test_new_install_can_seed_off(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY", "0")
        await seed_community_from_env(new_install=True)
        state = await get_community_effective()
        assert state.enabled is False


class TestKeepaliveAndJwtKwargs:
    def test_keepalive_is_30(self):
        private_key, public_key = _make_test_keys()
        pub = MeshloomStatsPublisher()
        settings = _state_to_settings(
            CommunityEffective(
                enabled=True,
                locked=False,
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

    def test_unavailable_is_500(self):
        with pytest.raises(HTTPException) as exc:
            unwrap_directory_envelope(
                {"status": "unavailable", "freshness": {}, "sources": [], "data": {"nodes": []}}
            )
        assert exc.value.status_code == 500

    def test_defaults_are_official_meshloom_hosts(self):
        assert DEFAULT_BROKER_HOST == "mqtt.meshloom.app"
        assert DEFAULT_API_BASE == "https://api.meshloom.app"
        assert DEFAULT_WEBSOCKET_PATH == "/mqtt"


class TestOfficialHostDefaults:
    @pytest.mark.asyncio
    async def test_env_overrides_official_defaults(self, test_db, monkeypatch):
        monkeypatch.setenv("MESHLOOM_COMMUNITY_BROKER_HOST", "mqtt.internal.test")
        monkeypatch.setenv("MESHLOOM_COMMUNITY_API_BASE", "https://api.internal.test")
        state = await get_community_effective()
        assert state.broker_host == "mqtt.internal.test"
        assert state.api_base == "https://api.internal.test"
        assert state.mqtt_audience == "mqtt.internal.test"
        assert state.api_audience == "api.internal.test"

    @pytest.mark.asyncio
    async def test_placeholder_row_uses_official_defaults(self, test_db):
        await update_community(
            broker_host="mqtt.example.invalid",
            api_base="https://api.example.invalid",
        )
        state = await get_community_effective()
        assert state.broker_host == DEFAULT_BROKER_HOST
        assert state.api_base == DEFAULT_API_BASE
        assert state.mqtt_audience == "mqtt.meshloom.app"
        assert state.api_audience == "api.meshloom.app"


class TestAirportSearch:
    @pytest.mark.asyncio
    async def test_short_query_is_empty(self):
        from app.services.meshloom_community import search_community_airports

        assert await search_community_airports("L") == []

    @pytest.mark.asyncio
    async def test_parses_fx_port_hits(self, monkeypatch):
        from app.services import meshloom_community

        response = MagicMock()
        response.status_code = 200
        response.json.return_value = [
            {
                "ap": "LYS",
                "airportname": "Lyon-Saint-Exupéry",
                "cityonly": "Lyon",
                "country": "France",
                "shortdisplayname": "Lyon, France (LYS)",
            },
            {"ap": "XX", "airportname": "too-short"},
        ]
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(
            meshloom_community.httpx, "AsyncClient", MagicMock(return_value=mock_client)
        )

        hits = await meshloom_community.search_community_airports("Lyon", locale="fr")
        assert len(hits) == 1
        assert hits[0].iata == "LYS"
        assert hits[0].name == "Lyon-Saint-Exupéry"
        mock_client.get.assert_awaited()
        assert mock_client.get.await_args.kwargs["params"]["locale"] == "fr"
