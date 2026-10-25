"""Share service client: coded errors, banned state, breaker, read buckets, negative cache, timeouts.

The no-egress sweep (sharing off, hosts, secrets) is ``tests/test_share_off_no_egress.py``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time

import httpx
import pytest

from app.net import egress_guard
from app.repository import AppSettingsRepository
from app.services import share
from app.services.share import (
    BANNED_HOLD_S,
    BREAKER_OPEN_SECONDS,
    NEGATIVE_CACHE_S,
    SHARE_ERRORS,
    ShareError,
    create_link,
    fetch_object,
    get_meta,
    report_object,
    reset_share_client_for_tests,
    share_banned,
    share_breaker,
)
from tests.share_fakes import Dns, Recorder, json_response

URL = "https://example.com/some/long/path"
LINK = {"id": "AbCdEfG", "url": "https://meshl.ink/AbCdEfG", "expires_at": 1791636000}
META = {"id": "AbCdEfG", "kind": "link", "target_host": "example.com", "expires_at": 1}
# What a Cloudflare Workers Free account answers past its daily limit.
CLOUDFLARE_1027 = (
    "<!DOCTYPE html><html><head><title>Worker exceeded resource limits | meshl.ink | "
    "Cloudflare</title></head><body><h1>Error 1027</h1></body></html>"
)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock():
    c = Clock()
    reset_share_client_for_tests(c)
    return c


@pytest.fixture
async def share_on(test_db, monkeypatch):
    await AppSettingsRepository.update(share_enabled=True)
    Dns(monkeypatch)


def _err(code: str, status: int, headers: dict[str, str] | None = None, **extra):
    return json_response(
        status, {"detail": code, "code": code, "server_time": 1760100000, **extra}, headers
    )


async def _raises(coro) -> ShareError:
    with pytest.raises(ShareError) as info:
        await coro
    return info.value


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("answer", "code", "params"),
        [
            (_err("banned", 403), "share_banned", {}),
            (
                _err("quota_exceeded", 429, {"Retry-After": "3600"}, scope="key"),
                "share_quota_exceeded",
                {"retry_after": 3600, "scope": "key"},
            ),
            (
                _err("quota_exceeded", 429, scope="<script>"),
                "share_quota_exceeded",
                {"retry_after": None, "scope": None},
            ),
            (
                _err("rate_limited", 429, {"Retry-After": "30"}),
                "share_rate_limited",
                {"retry_after": 30},
            ),
            (_err("too_large", 413), "share_too_large", {}),
            (_err("type_refused", 415), "share_type_refused", {}),
            (_err("gone", 410), "share_gone", {}),
            (_err("not_found", 404), "share_not_found", {}),
            (_err("target_blocked", 422), "share_target_blocked", {}),
            (_err("invalid_url", 400), "share_invalid_url", {}),
            (_err("unavailable", 503, {"Retry-After": "5"}), "share_unavailable", {}),
            (_err("kill_switch", 503), "share_unavailable", {}),
            (_err("token_replayed", 401), "share_unavailable", {}),
            (_err("invalid_request", 400), "share_unavailable", {}),
            (_err("banned", 500), "share_unavailable", {}),
            (httpx.Response(500, text="oops"), "share_unavailable", {}),
            (
                httpx.Response(403, text=CLOUDFLARE_1027, headers={"Content-Type": "text/html"}),
                "share_unavailable",
                {},
            ),
            (
                httpx.Response(
                    503, text="<html>Just a moment...</html>", headers={"Content-Type": "text/html"}
                ),
                "share_unavailable",
                {},
            ),
            # A JSON-looking body under a non-JSON type is not a contract answer.
            (
                httpx.Response(
                    403, text='{"code":"banned"}', headers={"Content-Type": "text/html"}
                ),
                "share_unavailable",
                {},
            ),
            (httpx.Response(200, text="<html>ok</html>"), "share_unavailable", {}),
            (
                json_response(201, {"id": "0OIl000", "url": "x", "expires_at": 1}),
                "share_unavailable",
                {},
            ),
            (
                json_response(201, {**LINK, "url": "https://evil.example/AbCdEfG"}),
                "share_unavailable",
                {},
            ),
            (
                httpx.Response(302, headers={"Location": "https://elsewhere.example/"}),
                "share_unavailable",
                {},
            ),
        ],
    )
    async def test_service_answer_maps_to_code(self, share_on, monkeypatch, answer, code, params):
        Recorder(monkeypatch, lambda request: answer)
        error = await _raises(create_link(URL))
        status, message = SHARE_ERRORS[code]
        assert error.status_code == status
        assert error.detail == {"code": code, "message": message, "params": params}

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("refused"),
            httpx.ConnectTimeout("slow"),
            httpx.ReadTimeout("slow"),
            httpx.WriteTimeout("slow"),
            httpx.RemoteProtocolError("bad"),
        ],
    )
    async def test_network_failure_is_unavailable(self, share_on, monkeypatch, exc):
        def fail(request):
            raise exc

        Recorder(monkeypatch, fail)
        error = await _raises(create_link(URL))
        assert error.status_code == 502
        assert error.code == "share_unavailable"

    async def test_off_is_disabled_without_request(self, test_db, monkeypatch):
        recorder = Recorder(monkeypatch)
        error = await _raises(create_link(URL))
        assert (error.status_code, error.code) == (403, "share_disabled")
        assert recorder.requests == []

    async def test_guard_refuses_a_private_answer(self, test_db, monkeypatch):
        await AppSettingsRepository.update(share_enabled=True)
        Dns(monkeypatch, {share.SHARE_HOST: ["10.0.0.7"]})
        recorder = Recorder(monkeypatch)
        error = await _raises(create_link(URL))
        assert error.code == "share_unavailable"
        assert recorder.requests == []

    async def test_bad_id_never_leaves(self, share_on, monkeypatch):
        recorder = Recorder(monkeypatch)
        for bad in ("0OIl000", "short", "AbCdEfG/../x", "AbCdEfGH"):
            assert (await _raises(get_meta(bad))).code == "share_not_found"
            assert (await _raises(fetch_object(bad))).code == "share_not_found"
        assert recorder.requests == []


class TestRequestShape:
    async def test_write_carries_a_bound_token_and_the_version(self, share_on, monkeypatch):
        recorder = Recorder(monkeypatch, lambda request: json_response(201, LINK))
        link = await create_link(URL)
        assert link.id == "AbCdEfG"
        (request,) = recorder.requests
        assert request.url.scheme == "https"
        assert request.url.path == "/v1/links"
        assert request.headers["host"] == "meshl.ink"
        assert request.headers["user-agent"].startswith("Meshloom/")
        assert json.loads(request.content) == {"url": URL}
        scheme, token = request.headers["authorization"].split(" ", 1)
        assert scheme == "Bearer"
        payload_b64 = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        assert claims["aud"] == "meshloom-share"
        assert claims["bh"] == hashlib.sha256(URL.encode()).hexdigest()

    async def test_report_binding(self, share_on, monkeypatch):
        recorder = Recorder(monkeypatch, lambda request: httpx.Response(202))
        await report_object("AbCdEfG", "spam", "free text")
        (request,) = recorder.requests
        token = request.headers["authorization"].split(" ", 1)[1]
        payload_b64 = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
        assert claims["bh"] == hashlib.sha256(b"AbCdEfG\nspam").hexdigest()

    async def test_reads_carry_no_token(self, share_on, monkeypatch):
        def answer(request):
            if request.url.path.startswith("/v1/meta/"):
                return json_response(200, META)
            return httpx.Response(200, content=b"\x89PNG", headers={"Content-Type": "image/png"})

        recorder = Recorder(monkeypatch, answer)
        assert (await get_meta("AbCdEfG")).target_host == "example.com"
        obj = await fetch_object("AbCdEfG")
        assert (obj.content, obj.content_type) == (b"\x89PNG", "image/png")
        assert [r.url.path for r in recorder.requests] == ["/v1/meta/AbCdEfG", "/AbCdEfG"]
        assert all("authorization" not in r.headers for r in recorder.requests)
        assert recorder.requests[1].headers["accept"] == "application/octet-stream"

    @pytest.mark.parametrize(
        ("content_type", "ok"),
        [
            ("image/png", True),
            ("image/webp", True),
            ("IMAGE/JPEG; charset=binary", True),
            ("application/octet-stream", True),
            ("text/html; charset=utf-8", False),
            ("image/svg+xml", False),
            ("application/json", False),
            (None, False),
        ],
    )
    async def test_object_types(self, share_on, monkeypatch, content_type, ok):
        headers = {"Content-Type": content_type} if content_type else {}
        Recorder(monkeypatch, lambda request: httpx.Response(200, content=b"x", headers=headers))
        if ok:
            assert (await fetch_object("AbCdEfG")).content == b"x"
        else:
            assert (await _raises(fetch_object("AbCdEfG"))).code == "share_unavailable"

    def test_service_code_is_sanitized_in_logs(self, caplog):
        response = httpx.Response(401, headers={"Content-Type": "application/json"})
        checked = share.CheckedResponse(
            status_code=401,
            headers=response.headers,
            content=b"",
            url="https://meshl.ink/",
            redirects=0,
        )
        with caplog.at_level("WARNING", logger="app.services.share"):
            share._service_error(checked, {"code": "token_replayed"})
            share._service_error(checked, {"code": "evil\nINJECTED line"})
        assert "token_replayed" in caplog.text
        assert "INJECTED" not in caplog.text
        assert "unknown" in caplog.text

    async def test_timeouts_and_pinning(self, share_on, monkeypatch):
        recorder = Recorder(monkeypatch, lambda request: json_response(201, LINK))
        await create_link(URL)
        (kwargs,) = recorder.client_kwargs
        timeout = kwargs["timeout"]
        assert (timeout.connect, timeout.read, timeout.write) == (5.0, 30.0, 60.0)
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False

    async def test_hosts_are_read_at_call_time(self, test_db, monkeypatch):
        await AppSettingsRepository.update(share_enabled=True)
        monkeypatch.setattr(share, "SHARE_HOST", "new.example")
        monkeypatch.setattr(share, "SHARE_HOSTS", ("new.example", "meshl.ink"))
        Dns(monkeypatch, {"new.example": ["93.184.215.14"]})
        answer = {**LINK, "url": "https://new.example/AbCdEfG"}
        recorder = Recorder(monkeypatch, lambda request: json_response(201, answer))
        await create_link(URL)
        assert recorder.hosts() == ["new.example"]
        assert share.is_share_host("MESHL.INK")
        assert not share.is_share_host("meshl.ink.evil.example")


class TestBanned:
    async def test_ban_refuses_writes_locally_for_12h(self, share_on, monkeypatch, clock):
        recorder = Recorder(monkeypatch, lambda request: _err("banned", 403))
        assert (await _raises(create_link(URL))).code == "share_banned"
        assert share_banned()
        recorder.handler = lambda request: json_response(201, LINK)
        assert (await _raises(create_link(URL))).code == "share_banned"
        assert (await _raises(report_object("AbCdEfG", "spam", ""))).code == "share_banned"
        assert len(recorder.requests) == 1
        # Reads stay open while banned.
        recorder.handler = lambda request: json_response(200, META)
        await get_meta("AbCdEfG")
        assert len(recorder.requests) == 2
        clock.now += BANNED_HOLD_S
        recorder.handler = lambda request: json_response(201, LINK)
        await create_link(URL)
        assert not share_banned()

    async def test_settings_change_clears_the_ban(self, share_on, monkeypatch):
        recorder = Recorder(monkeypatch, lambda request: _err("banned", 403))
        await _raises(create_link(URL))
        assert share_banned()
        await AppSettingsRepository.update(share_preview_policy="never")
        assert not share_banned()
        recorder.handler = lambda request: json_response(201, LINK)
        await create_link(URL)

    async def test_unrelated_settings_keep_the_ban(self, share_on, monkeypatch):
        Recorder(monkeypatch, lambda request: _err("banned", 403))
        await _raises(create_link(URL))
        await AppSettingsRepository.update(radio_offline_grace_s=30)
        assert share_banned()


class TestBreaker:
    async def test_opens_after_three_failures_and_closes_after_5_min(
        self, share_on, monkeypatch, clock
    ):
        def fail(request):
            raise httpx.ConnectError("down")

        recorder = Recorder(monkeypatch, fail)
        for _ in range(3):
            await _raises(get_meta("AbCdEfG"))
        assert share_breaker().is_open
        assert len(recorder.requests) == 3
        # Open: answered locally, writes and reads alike.
        assert (await _raises(get_meta("AbCdEfG"))).code == "share_unavailable"
        assert (await _raises(create_link(URL))).code == "share_unavailable"
        assert len(recorder.requests) == 3
        clock.now += BREAKER_OPEN_SECONDS - 1
        await _raises(get_meta("AbCdEfG"))
        assert len(recorder.requests) == 3
        clock.now += 1
        recorder.handler = lambda request: json_response(200, META)
        await get_meta("AbCdEfG")
        assert len(recorder.requests) == 4
        assert not share_breaker().is_open

    async def test_failed_trial_opens_again(self, share_on, monkeypatch, clock):
        def fail(request):
            raise httpx.ConnectError("down")

        recorder = Recorder(monkeypatch, fail)
        for _ in range(3):
            await _raises(get_meta("AbCdEfG"))
        clock.now += BREAKER_OPEN_SECONDS
        await _raises(get_meta("AbCdEfG"))
        assert len(recorder.requests) == 4
        await _raises(get_meta("AbCdEfG"))
        assert len(recorder.requests) == 4

    async def test_edge_pages_and_5xx_count_contract_errors_do_not(self, share_on, monkeypatch):
        answers = iter(
            [
                httpx.Response(403, text=CLOUDFLARE_1027, headers={"Content-Type": "text/html"}),
                _err("unavailable", 503),
                _err("invalid_url", 400),
            ]
        )
        Recorder(monkeypatch, lambda request: next(answers))
        await _raises(create_link(URL))
        await _raises(create_link(URL))
        assert share_breaker().failures == 2
        await _raises(create_link(URL))
        assert share_breaker().failures == 0

    async def test_community_breaker_is_separate(self, share_on, monkeypatch):
        from app.services.meshloom_community import community_breaker

        def fail(request):
            raise httpx.ConnectError("down")

        Recorder(monkeypatch, fail)
        for _ in range(3):
            await _raises(get_meta("AbCdEfG"))
        assert share_breaker().is_open
        assert not community_breaker().is_open


class TestReadBuckets:
    @pytest.fixture
    async def frozen_loop(self, monkeypatch):
        loop = asyncio.get_running_loop()
        frozen = loop.time()
        monkeypatch.setattr(loop, "time", lambda: frozen)

    async def test_61st_meta_in_a_minute_is_refused_locally(
        self, share_on, monkeypatch, frozen_loop
    ):
        recorder = Recorder(monkeypatch, lambda request: json_response(200, META))
        # Distinct ids: a good answer is cached per id.
        ids = [f"AbCdE{a}{b}" for a in "12345678" for b in "12345678"]
        for share_id in ids[:60]:
            await get_meta(share_id)
        error = await _raises(get_meta(ids[60]))
        assert (error.status_code, error.code) == (429, "share_rate_limited")
        assert error.params == {"retry_after": 1, "scope": "local"}
        assert len(recorder.requests) == 60

    async def test_21st_byte_fetch_is_refused_and_writes_are_not_limited(
        self, share_on, monkeypatch, frozen_loop
    ):
        def answer(request):
            if request.url.path == "/v1/links":
                return json_response(201, LINK)
            return httpx.Response(200, content=b"x", headers={"Content-Type": "image/png"})

        recorder = Recorder(monkeypatch, answer)
        for _ in range(20):
            await fetch_object("AbCdEfG")
        assert (await _raises(fetch_object("AbCdEfG"))).code == "share_rate_limited"
        await create_link(URL)
        assert len(recorder.requests) == 21


class TestNegativeCache:
    @pytest.mark.parametrize(("answer", "code"), [("not_found", 404), ("gone", 410)])
    async def test_json_404_and_410_are_cached_10_min(
        self, share_on, monkeypatch, clock, answer, code
    ):
        recorder = Recorder(monkeypatch, lambda request: _err(answer, code))
        first = await _raises(get_meta("AbCdEfG"))
        second = await _raises(get_meta("AbCdEfG"))
        assert first.detail == second.detail
        assert second.status_code == code
        assert len(recorder.requests) == 1
        # Another id, or the bytes of the same id, are not covered.
        await _raises(get_meta("BbCdEfG"))
        await _raises(fetch_object("AbCdEfG"))
        assert len(recorder.requests) == 3
        clock.now += NEGATIVE_CACHE_S
        await _raises(get_meta("AbCdEfG"))
        assert len(recorder.requests) == 4

    async def test_non_json_404_is_never_cached(self, share_on, monkeypatch, clock):
        recorder = Recorder(
            monkeypatch,
            lambda request: httpx.Response(
                404, text="<html>Not Found</html>", headers={"Content-Type": "text/html"}
            ),
        )
        assert (await _raises(get_meta("AbCdEfG"))).code == "share_unavailable"
        await _raises(get_meta("AbCdEfG"))
        assert len(recorder.requests) == 2

    async def test_unavailable_is_never_cached(self, share_on, monkeypatch):
        recorder = Recorder(monkeypatch, lambda request: _err("unavailable", 503))
        await _raises(get_meta("AbCdEfG"))
        await _raises(get_meta("AbCdEfG"))
        assert len(recorder.requests) == 2


class TestTimeouts:
    async def test_hanging_server_times_out(self, test_db, monkeypatch):
        """A server that accepts and never answers: the call ends at the connect timeout."""
        await AppSettingsRepository.update(share_enabled=True)

        async def hang(reader, writer):
            await asyncio.sleep(3600)

        server = await asyncio.start_server(hang, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(share, "SHARE_HOST", f"meshl.ink:{port}")
        monkeypatch.setattr(share, "SHARE_TIMEOUT", httpx.Timeout(0.3))
        Dns(monkeypatch, {"meshl.ink": ["127.0.0.1"]})
        # The guard refuses loopback; this test only exercises the timeout.
        monkeypatch.setattr(egress_guard, "check_addresses", lambda ips, **kw: None)
        try:
            started = time.monotonic()
            error = await _raises(create_link(URL))
            assert time.monotonic() - started < 3
            assert error.code == "share_unavailable"
            assert share_breaker().failures == 1
        finally:
            server.close()

    async def test_total_bound(self, share_on, monkeypatch):
        """A server dripping its answer is cut by the bound on the whole call."""
        monkeypatch.setattr(share, "SHARE_TOTAL_TIMEOUT_S", 0.2)

        class Drip(httpx.AsyncByteStream):
            async def __aiter__(self):
                while True:
                    await asyncio.sleep(0.05)
                    yield b" "

        real = httpx.AsyncClient

        class Dripping(real):
            def __init__(self, **kwargs):
                kwargs["transport"] = httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, headers={"Content-Type": "application/json"}, stream=Drip()
                    )
                )
                super().__init__(**kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Dripping)
        started = time.monotonic()
        assert (await _raises(create_link(URL))).code == "share_unavailable"
        assert time.monotonic() - started < 2


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_every_code_is_translated(lang):
    from pathlib import Path

    slice_path = Path(__file__).parents[1] / f"frontend/src/i18n/locales/slices/ah.{lang}.json"
    errors = json.loads(slice_path.read_text(encoding="utf-8"))["errors"]
    assert set(SHARE_ERRORS) <= set(errors)
