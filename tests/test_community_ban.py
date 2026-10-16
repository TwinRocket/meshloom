"""Community ban (403 ``banned``): no retry storm, a clear state for the UI."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import aiomqtt
import httpx
import pytest
from aiomqtt.exceptions import MqttConnectError
from fastapi import HTTPException
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from app.fanout.meshloom_stats import (
    BANNED_RECHECK_INTERVAL_S,
    MeshloomStatsPublisher,
    is_auth_refusal,
)
from app.fanout.mqtt import MqttPublisher
from app.models import CommunityLiveStatus
from app.routers.community import get_community
from app.services import community_ban
from app.services.community_ban import (
    BAN_PROBE_MIN_INTERVAL_S,
    BANNED_HOLD_S,
    BANNED_REFUSED_ROUTES,
    COMMUNITY_BANNED_ERROR,
    banned_route,
    clear_ban,
    community_banned,
    is_banned_answer,
    note_banned,
    reset_community_ban_for_tests,
    take_ban_probe,
)
from app.services.community_live import CommunityLiveRelay, reset_community_live_for_tests
from app.services.meshloom_community import (
    BAN_PROBE_PATH,
    OPT_OUT_PATH,
    CommunityEffective,
    bind_community_iata,
    reset_stats_client_for_tests,
    stats_request,
    update_community,
)
from app.services.observer_position import _rebind_once, reset_position_rebind_for_tests
from tests.test_community_live import FakeStatsSocket, _wait_until
from tests.test_mqtt import _make_settings


def _state() -> CommunityEffective:
    return CommunityEffective(
        enabled=True,
        iata="LYS",
        broker_host="mqtt.meshloom.app",
        api_base="https://api.meshloom.app",
        api_audience="api.meshloom.app",
        mqtt_audience="mqtt.meshloom.app",
    )


def _banned_response() -> httpx.Response:
    return httpx.Response(
        403, json={"detail": "this key is banned", "code": "banned", "server_time": 0}
    )


@pytest.fixture(autouse=True)
def _reset():
    reset_community_ban_for_tests()
    reset_stats_client_for_tests()
    reset_community_live_for_tests()
    reset_position_rebind_for_tests()
    yield
    reset_community_ban_for_tests()
    reset_stats_client_for_tests()
    reset_community_live_for_tests()
    reset_position_rebind_for_tests()


def _enter_on(stack: ExitStack, http: AsyncMock) -> MagicMock:
    stack.enter_context(
        patch(
            "app.services.meshloom_community.community_egress_state",
            new_callable=AsyncMock,
            return_value=_state(),
        )
    )
    stack.enter_context(
        patch("app.services.meshloom_community.mint_stats_jwt", MagicMock(return_value="tok"))
    )
    stack.enter_context(patch("app.services.meshloom_community._community_http", http))
    relay = MagicMock()
    relay.stop_for_ban = AsyncMock()
    stack.enter_context(patch("app.services.community_live.get_live_relay", return_value=relay))
    return relay


class TestBanState:
    def test_only_a_403_with_code_banned_is_a_ban(self):
        assert is_banned_answer(403, {"code": "banned"})
        assert not is_banned_answer(403, {"code": "opted_out"})
        assert not is_banned_answer(401, {"code": "banned"})
        assert not is_banned_answer(403, {"detail": "banned"})
        assert not is_banned_answer(403, None)

    def test_the_refused_routes_are_the_contract_list(self):
        assert {
            ("PUT", "/v1/me/iata"),
            ("POST", "/v1/me/iata/override"),
            ("PUT", "/v1/me/hashtags"),
            ("POST", "/v1/hashtags/resolve"),
            ("POST", "/v1/hashtags/samples"),
            ("POST", "/v1/directory/packets/observations"),
        } == BANNED_REFUSED_ROUTES
        assert banned_route("put", "/v1/me/iata")
        assert not banned_route("GET", "/v1/me/stats")
        assert not banned_route("POST", "/v1/me/opt-out")

    def test_holds_for_a_long_time_then_checks_again(self):
        assert note_banned(now=1000.0) is True
        # A second answer extends the hold but does not start it again.
        assert note_banned(now=1001.0) is False
        assert community_banned(now=1001.0 + BANNED_HOLD_S - 1)
        assert not community_banned(now=1001.0 + BANNED_HOLD_S)
        assert community_ban._banned_until is None

    def test_clear(self):
        note_banned()
        assert clear_ban() is True
        assert not community_banned()
        assert clear_ban() is False

    def test_probe_is_rate_limited_and_a_clear_resets_it(self):
        assert take_ban_probe(now=100.0) is True
        assert take_ban_probe(now=100.0 + BAN_PROBE_MIN_INTERVAL_S - 1) is False
        assert take_ban_probe(now=100.0 + BAN_PROBE_MIN_INTERVAL_S) is True
        assert take_ban_probe(now=100.0 + BAN_PROBE_MIN_INTERVAL_S + 1) is False
        clear_ban()
        assert take_ban_probe(now=100.0 + BAN_PROBE_MIN_INTERVAL_S + 1) is True


@pytest.mark.asyncio
class TestHttpBan:
    async def test_ban_answer_maps_to_community_banned_and_stops_writes(self):
        http = AsyncMock(return_value=_banned_response())
        with ExitStack() as stack:
            relay = _enter_on(stack, http)
            with pytest.raises(HTTPException) as exc:
                await stats_request("PUT", "/v1/me/hashtags", auth=True, json_body={"names": []})
            assert exc.value.status_code == 403
            assert exc.value.detail["code"] == COMMUNITY_BANNED_ERROR
            assert community_banned()
            await _wait_until(lambda: relay.stop_for_ban.await_count == 1)
            # Every refused route now fails locally, without a request.
            for method, path in sorted(BANNED_REFUSED_ROUTES):
                with pytest.raises(HTTPException) as exc:
                    await stats_request(method, path, auth=True, json_body={})
                assert exc.value.detail["code"] == COMMUNITY_BANNED_ERROR
            assert http.await_count == 1
            # Reads Community does not refuse keep working.
            http.return_value = httpx.Response(200, json={})
            response = await stats_request("GET", "/v1/me/stats", auth=True)
            assert response.status_code == 200
            assert http.await_count == 2
        # One ban, one Live stop.
        assert relay.stop_for_ban.await_count == 1

    async def test_other_403_is_not_a_ban(self):
        http = AsyncMock(
            return_value=httpx.Response(403, json={"detail": "x", "code": "opted_out"})
        )
        with ExitStack() as stack:
            _enter_on(stack, http)
            response = await stats_request("PUT", "/v1/me/hashtags", auth=True, json_body={})
        assert response.status_code == 403
        assert not community_banned()

    async def test_after_the_hold_one_call_checks_again(self):
        http = AsyncMock(return_value=httpx.Response(200, json={}))
        # Expired, whatever the monotonic clock reads (a fresh CI runner starts near 0).
        note_banned(now=time.monotonic() - BANNED_HOLD_S - 1)
        with ExitStack() as stack:
            _enter_on(stack, http)
            await stats_request("PUT", "/v1/me/hashtags", auth=True, json_body={})
        assert http.await_count == 1
        assert not community_banned()

    async def test_opt_out_still_leaves_while_banned(self):
        """#111: POST /v1/me/opt-out stays open to a banned key, so it never waits on the ban."""
        http = AsyncMock(return_value=httpx.Response(200, json={}))
        note_banned()
        with ExitStack() as stack:
            _enter_on(stack, http)
            response = await stats_request("POST", OPT_OUT_PATH, auth=True, json_body={})
        assert response.status_code == 200
        assert http.await_count == 1
        assert http.await_args.args[1].endswith(OPT_OUT_PATH)

    async def test_a_user_bind_clears_the_ban_and_sends_one_request(self):
        """The bind of a user IATA change is the one request that checks again."""
        http = AsyncMock(return_value=_banned_response())
        seen: dict[str, bool] = {}

        async def bind():
            seen["banned_at_bind"] = community_banned()
            await stats_request("PUT", "/v1/me/iata", auth=True, json_body={"iata": "LYS"})

        note_banned()
        with ExitStack() as stack:
            _enter_on(stack, http)
            stack.enter_context(
                patch(
                    "app.services.meshloom_community.get_community_effective",
                    new_callable=AsyncMock,
                    return_value=_state(),
                )
            )
            with pytest.raises(HTTPException) as exc:
                await bind_community_iata("LYS", bind)
        assert exc.value.detail["code"] == COMMUNITY_BANNED_ERROR
        assert seen["banned_at_bind"] is False
        assert http.await_count == 1
        # Community still bans the key: the state is back.
        assert community_banned()

    async def test_status_reports_the_ban_and_a_settings_change_clears_it(self, test_db):
        assert (await get_community()).banned is False
        note_banned()
        assert (await get_community()).banned is True
        await update_community(enabled=False)
        assert (await get_community()).banned is False


def _refused(status: int, code: str) -> InvalidStatus:
    body = json.dumps({"detail": "x", "code": code, "server_time": 0}).encode()
    return InvalidStatus(Response(status, "Forbidden", Headers(), body))


def _enter_live(stack: ExitStack, relay: CommunityLiveRelay, connect: object) -> AsyncMock:
    stack.enter_context(
        patch(
            "app.services.meshloom_community.community_active",
            new_callable=AsyncMock,
            return_value=True,
        )
    )
    stack.enter_context(
        patch(
            "app.services.meshloom_community.get_community_effective",
            new_callable=AsyncMock,
            return_value=_state(),
        )
    )
    stack.enter_context(
        patch(
            "app.services.meshloom_community.community_egress_state",
            new_callable=AsyncMock,
            return_value=_state(),
        )
    )
    stack.enter_context(
        patch("app.services.meshloom_community.mint_stats_jwt", MagicMock(return_value="tok"))
    )
    broadcast = stack.enter_context(
        patch("app.websocket.ws_manager.broadcast", new_callable=AsyncMock)
    )
    stack.enter_context(patch.object(relay, "_connect", side_effect=connect))
    return broadcast


@pytest.mark.asyncio
class TestLiveBan:
    async def test_handshake_403_banned_stops_the_relay(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            raise _refused(403, "banned")

        with ExitStack() as stack:
            broadcast = _enter_live(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay._reader_task is None or relay._reader_task.done())
            assert community_banned()
            snap = relay.snapshot(opted_out=False)
            CommunityLiveStatus.model_validate(snap)
            assert snap["state"] == "banned"
            assert snap["close_code"] is None
            states = [c.args[1]["state"] for c in broadcast.await_args_list]
            assert states[-1] == "banned"
            # A heartbeat does not reopen anything while the ban holds.
            sub = await relay.subscribe()
            await asyncio.sleep(0.05)
            assert sub["state"] == "banned"
            assert len(connects) == 1
            await relay.close_stats()

    async def test_other_403_keeps_the_normal_backoff(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            if len(connects) == 1:
                raise _refused(403, "opted_out")
            return FakeStatsSocket([])

        with ExitStack() as stack:
            _enter_live(stack, relay, connect)
            stack.enter_context(patch("app.services.community_live.RECONNECT_INITIAL_S", 0.01))
            await relay.subscribe()
            await _wait_until(lambda: len(connects) >= 2)
            await relay.close_stats()
        assert not community_banned()

    async def test_restart_clears_the_ban_and_reconnects(self):
        relay = CommunityLiveRelay()
        connects: list[int] = []

        async def connect(_url: str, _token: str):
            connects.append(1)
            return FakeStatsSocket([])

        note_banned()
        with ExitStack() as stack:
            _enter_live(stack, relay, connect)
            await relay.subscribe()
            await asyncio.sleep(0.05)
            assert connects == []
            await relay.restart()
            await _wait_until(lambda: len(connects) == 1)
            assert not community_banned()
            await relay.close_stats()

    async def test_stop_for_ban_closes_and_reports(self):
        relay = CommunityLiveRelay()

        async def connect(_url: str, _token: str):
            return FakeStatsSocket([])

        with ExitStack() as stack:
            broadcast = _enter_live(stack, relay, connect)
            await relay.subscribe()
            await _wait_until(lambda: relay.connected)
            note_banned()
            await relay.stop_for_ban()
            assert not relay.connected
            assert broadcast.await_args_list[-1].args[1]["state"] == "banned"


@pytest.mark.asyncio
class TestPublisherBan:
    async def test_pre_connect_waits_without_connecting_while_banned(self):
        pub = MeshloomStatsPublisher()
        note_banned()
        slept: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            slept.append(seconds)

        with (
            patch(
                "app.services.meshloom_community.community_enrolled_state",
                new_callable=AsyncMock,
                return_value=_state(),
            ),
            patch("app.fanout.meshloom_stats.asyncio.sleep", side_effect=fake_sleep),
        ):
            assert await pub._pre_connect(object()) is False
        assert slept == [BANNED_RECHECK_INTERVAL_S]
        assert pub._error_toast_allowed() is False
        clear_ban()
        assert pub._error_toast_allowed() is True

    async def test_position_rebind_skips_while_banned(self):
        """The re-bind worker takes the local 403 as "skipped", not a failure to retry."""
        http = AsyncMock(return_value=httpx.Response(200, json={}))
        note_banned()
        info = {"public_key": "ab" * 32, "adv_lat": 45.76, "adv_lon": 4.84}
        with ExitStack() as stack:
            _enter_on(stack, http)
            stack.enter_context(
                patch(
                    "app.services.meshloom_community.community_enrolled_state",
                    new_callable=AsyncMock,
                    return_value=_state(),
                )
            )
            stack.enter_context(
                patch("app.services.observer_position.radio_self_info", return_value=info)
            )
            stack.enter_context(
                patch("app.services.radio_ingest_gate.ingest_allowed", return_value=True)
            )
            assert await _rebind_once() == "skipped"
        assert http.await_count == 0
        assert community_banned()


def _auth_refused() -> MqttConnectError:
    return MqttConnectError(5)


@pytest.mark.asyncio
class TestBrokerRefusalProbe:
    """A broker auth refusal asks Community once whether the key is banned."""

    async def test_only_an_auth_refusal_counts(self):
        assert is_auth_refusal(MqttConnectError(5))
        assert is_auth_refusal(MqttConnectError(4))
        assert not is_auth_refusal(MqttConnectError(3))
        assert not is_auth_refusal(aiomqtt.MqttError("network"))
        assert not is_auth_refusal(OSError("refused"))

    async def test_refusal_of_a_banned_key_starts_the_ban(self):
        http = AsyncMock(return_value=_banned_response())
        pub = MeshloomStatsPublisher()
        with ExitStack() as stack:
            relay = _enter_on(stack, http)
            await pub._on_connection_error(_auth_refused())
            assert community_banned()
            assert pub._error_toast_allowed() is False
            await _wait_until(lambda: relay.stop_for_ban.await_count == 1)
            # Banned now: further refusals send nothing.
            await pub._on_connection_error(_auth_refused())
        assert http.await_count == 1
        method, url = http.await_args.args[:2]
        assert method == "POST" and url.endswith(BAN_PROBE_PATH)
        assert http.await_args.kwargs["json"] == {"hash_bytes": []}

    async def test_refusal_without_a_ban_probes_once_per_interval(self):
        http = AsyncMock(return_value=httpx.Response(200, json={"hashtags": []}))
        pub = MeshloomStatsPublisher()
        with ExitStack() as stack:
            _enter_on(stack, http)
            await pub._on_connection_error(_auth_refused())
            await pub._on_connection_error(_auth_refused())
        assert http.await_count == 1
        assert not community_banned()
        assert pub._error_toast_allowed() is True

    async def test_other_failures_send_nothing(self):
        http = AsyncMock(return_value=_banned_response())
        pub = MeshloomStatsPublisher()
        with ExitStack() as stack:
            _enter_on(stack, http)
            await pub._on_connection_error(aiomqtt.MqttError("network"))
            await pub._on_connection_error(MqttConnectError(3))
            stack.enter_context(
                patch("app.services.meshloom_community.opt_out_in_progress", return_value=True)
            )
            await pub._on_connection_error(_auth_refused())
        assert http.await_count == 0
        assert not community_banned()

    async def test_after_the_hold_a_refusal_finds_the_ban_again(self):
        http = AsyncMock(return_value=_banned_response())
        pub = MeshloomStatsPublisher()
        assert take_ban_probe()
        note_banned(now=time.monotonic() - BANNED_HOLD_S - 1)
        community_ban._last_probe_at = time.monotonic() - BANNED_HOLD_S - 1
        assert not community_banned()
        with ExitStack() as stack:
            _enter_on(stack, http)
            await pub._on_connection_error(_auth_refused())
        assert http.await_count == 1
        assert community_banned()
        assert pub._error_toast_allowed() is False

    async def test_loop_runs_the_hook_before_deciding_the_toast(self):
        order: list[str] = []

        class Probing(MqttPublisher):
            async def _on_connection_error(self, exc: Exception) -> None:
                order.append(f"hook:{type(exc).__name__}")

            def _error_toast_allowed(self) -> bool:
                order.append("toast?")
                return False

        client = AsyncMock()
        client.__aenter__ = AsyncMock(side_effect=_auth_refused())
        client.__aexit__ = AsyncMock(return_value=False)
        pub = Probing()
        with (
            patch("app.fanout.mqtt_base.aiomqtt.Client", return_value=client),
            patch("app.fanout.mqtt_base._broadcast_health"),
            patch("app.websocket.broadcast_error") as broadcast_error,
        ):
            await pub.start(_make_settings())
            await _wait_until(lambda: "toast?" in order)
            await pub.stop()
        assert order[:2] == ["hook:MqttConnectError", "toast?"]
        broadcast_error.assert_not_called()
