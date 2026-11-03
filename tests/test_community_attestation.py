"""Community attestations of the instance keys (MSHL-445, app/services/community_attestation.py).

Community, Links and the Remote registry are mocked: a recorder replaces
``httpx.AsyncClient`` (``tests/share_fakes.py``), so the real ``stats_request`` and the
real egress guard run. The autouse ``_fake_community_attestation`` fixture of
``tests/conftest.py`` is undone here: these tests use the real client.

Provenance: tests/fixtures/attestation_vectors.json is a byte-for-byte copy of
TwinRocket/meshloom-community docs/contracts/vectors/attestation.json on main (contract
commit fe63050, MSHL-445, 2026-11-03), sha256 below. To update, copy the file again and
change the hash; never edit the copy to make a test pass. Meshloom does not verify the
signature (Links and Remote do): the vectors check what the client keeps or drops.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from app import keystore
from app.auth.audit import SYSTEM_ACTOR
from app.repository import AppSettingsRepository
from app.repository.community_attestations import CommunityAttestationRepository
from app.repository.remote import RemoteRepository
from app.services import community_attestation as ca
from app.services import meshloom_community, remote, share
from app.services.community_ban import banned_http_exception, reset_community_ban_for_tests
from app.services.instance_identity import REMOTE_SERVICE, SHARE_SERVICE, ensure_instance_key
from app.services.share import ShareError
from tests.attestation_fakes import attestation_answer, fake_attestation_token
from tests.share_fakes import PUBLIC_IP, Dns, Recorder, json_response

VECTORS_PATH = Path(__file__).parent / "fixtures" / "attestation_vectors.json"
VECTORS_SHA256 = "7df2f73c953caa3acaa0468c6fb55663afdef2111a5a0c623a6d03ba7cd0fedb"
VECTORS = json.loads(VECTORS_PATH.read_text())

# The real functions, before the conftest fixture replaces them.
REAL_ATTESTATION_FOR = ca.attestation_for
REAL_RENEW = ca.renew_after_rejection
REAL_REQUEST = ca._request_attestation

RADIO_PRIVATE_KEY = bytes(range(64))
COMMUNITY_HOST = "api.meshloom.app"
NOW = 1_800_000_000.0


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    ca.reset_attestations_for_tests(c)
    monkeypatch.setattr(ca, "attestation_for", REAL_ATTESTATION_FOR)
    monkeypatch.setattr(ca, "renew_after_rejection", REAL_RENEW)
    return c


async def _key(service: str) -> str:
    return bytes((await ensure_instance_key(service)).verify_key).hex().upper()


class FakeCommunity:
    """Stands in for ``_request_attestation``: records calls, answers from a queue."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.calls: list[tuple[str, str]] = []
        self.answers: list[Any] = []

    async def __call__(self, instance_key: str, audience: str) -> httpx.Response:
        self.calls.append((instance_key, audience))
        answer = self.answers.pop(0) if self.answers else "ok"
        if isinstance(answer, BaseException):
            raise answer
        if answer == "ok":
            return attestation_answer(instance_key, audience, iat=int(self.clock.now))
        return answer


@pytest.fixture
def community(clock, monkeypatch, test_db):
    fake = FakeCommunity(clock)
    monkeypatch.setattr(ca, "_request_attestation", fake)
    return fake


def _refusal(status: int, code: str, headers: dict[str, str] | None = None) -> httpx.Response:
    return json_response(status, {"detail": code, "code": code, "server_time": 1}, headers)


class TestVectors:
    def test_copy_is_the_contract_file(self):
        assert hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest() == VECTORS_SHA256

    def test_constants_follow_the_contract(self):
        assert VECTORS["max_token_chars"] == ca.TOKEN_MAX_LEN
        assert set(VECTORS["audiences"]) == {ca.AUDIENCE_LINKS, ca.AUDIENCE_REMOTE}
        assert set(VECTORS["error_codes"]) == ca.REJECTION_CODES
        assert ca.ATTESTATIONS_PATH == meshloom_community.ATTESTATIONS_PATH
        # The local budget stays under Community's 12 per radio key and hour.
        assert ca.FETCH_BUDGET * 2 <= 12

    def test_the_issue_vector_is_kept(self):
        issue = VECTORS["issue"]
        answer = httpx.Response(
            201,
            json={
                "attestation": issue["token"],
                "audience": issue["audience"],
                "issued_at": issue["iat"],
                "expires_at": issue["exp"],
            },
        )
        kept = ca._parse(answer, issue["audience"], issue["instance_key"], issue["iat"])
        assert kept is not None
        assert (kept.token, kept.expires_at) == (issue["token"], issue["exp"])
        # Another key or audience: dropped.
        assert ca._parse(answer, issue["audience"], "A" * 64, issue["iat"]) is None
        assert ca._parse(answer, "remote", issue["instance_key"], issue["iat"]) is None

    @pytest.mark.parametrize(
        "case",
        [c for c in VECTORS["cases"] if c["expect"] == "ok"],
        ids=lambda c: c["id"],
    )
    def test_every_valid_token_has_a_shape_the_client_keeps(self, case):
        claims = ca._token_claims(case["token"])
        exp = claims["exp"]
        answer = httpx.Response(
            201,
            json={
                "attestation": case["token"],
                "audience": case["audience"],
                "issued_at": claims["iat"],
                "expires_at": exp,
            },
        )
        now = min(case["now"], exp - ca.MIN_REMAINING_S - 1)
        assert ca._parse(answer, case["audience"], case["instance_key"], now) is not None

    @pytest.mark.parametrize(
        "case_id",
        [
            "wrong_audience",
            "other_instance_key",
            "lowercase_sub",
            "two_segments",
            "base64_signature",
            "too_long",
            "empty",
        ],
    )
    def test_tokens_the_client_can_tell_are_wrong_are_dropped(self, case_id):
        case = next(c for c in VECTORS["cases"] if c["id"] == case_id)
        answer = httpx.Response(
            201,
            json={
                "attestation": case["token"],
                "audience": case["audience"],
                "issued_at": int(case["now"]),
                "expires_at": int(case["now"]) + 259200,
            },
        )
        assert ca._parse(answer, case["audience"], case["instance_key"], case["now"]) is None


class TestCache:
    async def test_asks_once_then_reuses_it_across_a_restart(self, community, clock):
        key = await _key(SHARE_SERVICE)
        token = await ca.attestation_for("links")
        assert community.calls == [(key, "links")]
        assert await ca.attestation_for("links") == token
        ca.reset_attestations_for_tests(clock)  # a restart: memory gone, the row stays
        assert await ca.attestation_for("links") == token
        assert len(community.calls) == 1
        stored = await CommunityAttestationRepository.get("links")
        assert stored is not None and stored.instance_key == key

    async def test_one_per_audience_and_key(self, community):
        links = await ca.attestation_for("links")
        remote_token = await ca.attestation_for("remote")
        assert links != remote_token
        assert [audience for _, audience in community.calls] == ["links", "remote"]
        assert community.calls[1][0] == await _key(REMOTE_SERVICE)

    async def test_renews_under_24_hours_left(self, community, clock):
        first = await ca.attestation_for("links")
        clock.now += 259200 - ca.RENEW_BEFORE_S + 1
        second = await ca.attestation_for("links")
        assert second != first and len(community.calls) == 2

    async def test_keeps_the_old_one_when_renewal_fails(self, community, clock):
        first = await ca.attestation_for("links")
        clock.now += 259200 - 3600
        community.answers = [_refusal(503, "unavailable"), _refusal(503, "unavailable")]
        assert await ca.attestation_for("links") == first
        clock.now += 3600 - ca.MIN_REMAINING_S  # too close to its end now, still refused
        with pytest.raises(ca.AttestationUnavailable):
            await ca.attestation_for("links")

    async def test_a_new_instance_key_needs_a_new_one(self, community):
        from app.services.instance_identity import regenerate

        first = await ca.attestation_for("links")
        await regenerate(SHARE_SERVICE)
        second = await ca.attestation_for("links")
        assert second != first
        assert community.calls[1][0] == await _key(SHARE_SERVICE)

    async def test_concurrent_callers_share_one_request(self, community):
        import asyncio

        tokens = await asyncio.gather(*(ca.attestation_for("links") for _ in range(5)))
        assert len(set(tokens)) == 1 and len(community.calls) == 1

    async def test_community_off_and_restore_forget_it(self, community, community_on):
        await ca.attestation_for("links")
        await AppSettingsRepository.update_community(enabled=False)
        await ca._on_community_change()
        assert await CommunityAttestationRepository.get("links") is None
        await ca.attestation_for("links")
        await ca._forget_after_restore("json")  # type: ignore[arg-type]
        assert await CommunityAttestationRepository.get("links") is None


class TestRefusals:
    @pytest.mark.parametrize(
        ("answer", "reason", "hold"),
        [
            (_refusal(403, "radio_not_heard"), "radio_not_heard", 900),
            (_refusal(403, "opted_out"), "opted_out", 3600),
            (banned_http_exception(), "banned", 3600),
            (_refusal(429, "rate_limited", {"Retry-After": "120"}), "unavailable", 120),
            (json_response(429, {"detail": "attestation rate limit"}), "unavailable", 900),
            (_refusal(503, "attestation_unconfigured"), "unavailable", 900),
            (HTTPException(status_code=503, detail="Community is unreachable"), "unavailable", 60),
            (_refusal(422, "invalid_request"), "unavailable", 60),
            (httpx.Response(201, json={"attestation": "x"}), "unavailable", 60),
        ],
    )
    async def test_maps_and_holds(self, community, clock, answer, reason, hold):
        community.answers = [answer]
        with pytest.raises(ca.AttestationUnavailable) as info:
            await ca.attestation_for("links")
        assert info.value.reason == reason
        # Held: no request until the hold ends.
        clock.now += hold - 1
        with pytest.raises(ca.AttestationUnavailable) as again:
            await ca.attestation_for("links")
        assert again.value.reason == reason and len(community.calls) == 1
        clock.now += 1
        await ca.attestation_for("links")
        assert len(community.calls) == 2

    async def test_backoff_grows_then_resets(self, community, clock):
        down = HTTPException(status_code=503, detail="Community is unreachable")
        community.answers = [down, down, down]
        for hold in (60, 120, 240):
            with pytest.raises(ca.AttestationUnavailable) as info:
                await ca.attestation_for("links")
            assert info.value.retry_after == hold
            clock.now += hold
        await ca.attestation_for("links")
        assert ca._state.failures == {}

    async def test_community_off_is_not_held(self, community):
        community.answers = [HTTPException(status_code=403, detail="Community is disabled")]
        with pytest.raises(ca.AttestationUnavailable):
            await ca.attestation_for("links")
        await ca.attestation_for("links")
        assert len(community.calls) == 2

    async def test_local_budget(self, community, clock):
        for _ in range(ca.FETCH_BUDGET):
            await CommunityAttestationRepository.delete()
            await ca.attestation_for("links")
        await CommunityAttestationRepository.delete()
        with pytest.raises(ca.AttestationUnavailable) as info:
            await ca.attestation_for("links")
        assert info.value.reason == "unavailable"
        assert len(community.calls) == ca.FETCH_BUDGET
        await ca.attestation_for("remote")  # the other audience has its own budget
        clock.now += ca.FETCH_WINDOW_S
        await ca.attestation_for("links")


class TestRenewAfterRejection:
    async def test_an_old_token_is_replaced_once(self, community, clock):
        first = await ca.attestation_for("links")
        clock.now += ca.FORCED_RENEW_MIN_INTERVAL_S
        second = await ca.renew_after_rejection("links", first)
        assert second != first and len(community.calls) == 2

    async def test_a_fresh_token_refused_is_not_asked_again(self, community, clock):
        first = await ca.attestation_for("links")
        with pytest.raises(ca.AttestationUnavailable):
            await ca.renew_after_rejection("links", first)
        assert len(community.calls) == 1
        assert await CommunityAttestationRepository.get("links") is None

    async def test_already_renewed_by_another_request(self, community, clock):
        first = await ca.attestation_for("links")
        clock.now += ca.FORCED_RENEW_MIN_INTERVAL_S
        second = await ca.renew_after_rejection("links", first)
        assert await ca.renew_after_rejection("links", first) == second
        assert len(community.calls) == 2


class TestCommunityRequest:
    async def test_the_request_and_its_guard(self, clock, community_on, monkeypatch):
        keystore.set_private_key(RADIO_PRIVATE_KEY)
        reset_community_ban_for_tests()
        meshloom_community.reset_stats_client_for_tests()
        key = await _key(SHARE_SERVICE)
        recorder = Recorder(
            monkeypatch,
            lambda request: attestation_answer(
                json.loads(request.content)["instance_key"], "links", iat=int(clock.now)
            ),
        )
        try:
            monkeypatch.setattr(ca, "_request_attestation", REAL_REQUEST)
            await ca.attestation_for("links")
            [request] = recorder.requests
            assert (request.method, request.url.host, request.url.path) == (
                "POST",
                COMMUNITY_HOST,
                ca.ATTESTATIONS_PATH,
            )
            assert json.loads(request.content) == {"instance_key": key, "audience": "links"}
            assert request.headers["authorization"].startswith("Bearer ")
            # Community off: nothing leaves.
            await AppSettingsRepository.update_community(enabled=False)
            await ca.forget_attestations()
            with pytest.raises(ca.AttestationUnavailable):
                await ca.attestation_for("links")
            assert len(recorder.requests) == 1
        finally:
            keystore.clear_keys()

    def test_no_iata_needed_and_banned_keys_refused_locally(self):
        from app.services.community_ban import banned_route

        assert not meshloom_community._path_requires_iata(ca.ATTESTATIONS_PATH)
        assert banned_route("POST", ca.ATTESTATIONS_PATH)


# ---------------------------------------------------------------- Links


LINK = {"id": "AbCdEfG", "url": "https://meshl.ink/AbCdEfG", "expires_at": 1791636000}


@pytest.fixture
async def links(community, community_on, monkeypatch):
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    share.reset_share_client_for_tests()
    Dns(monkeypatch)
    answers: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return answers.pop(0) if answers else json_response(201, LINK)

    recorder = Recorder(monkeypatch, handler)
    recorder.answers = answers  # type: ignore[attr-defined]
    return recorder


class TestLinks:
    async def test_creation_carries_the_attestation(self, links, community):
        await share.create_link("https://example.com/a")
        [request] = links.requests
        stored = await CommunityAttestationRepository.get("links")
        assert stored is not None
        assert request.headers[ca.ATTESTATION_HEADER] == stored.token

    async def test_reads_carry_none(self, links, community):
        links.answers.append(json_response(200, {"id": "AbCdEfG", "kind": "link"}))
        await share.get_meta("AbCdEfG")
        assert ca.ATTESTATION_HEADER.lower() not in links.requests[0].headers
        assert community.calls == []

    async def test_refused_once_renewed_and_retried_once(self, links, community, clock):
        await ca.attestation_for("links")
        clock.now += ca.FORCED_RENEW_MIN_INTERVAL_S
        links.answers.append(_refusal(403, "attestation_expired"))
        await share.create_link("https://example.com/a")
        first, second = (r.headers[ca.ATTESTATION_HEADER] for r in links.requests)
        assert first != second and len(community.calls) == 2

    async def test_refused_twice_is_reported_not_looped(self, links, community, clock):
        await ca.attestation_for("links")
        clock.now += ca.FORCED_RENEW_MIN_INTERVAL_S
        links.answers.extend([_refusal(403, "attestation_invalid")] * 5)
        with pytest.raises(ShareError) as info:
            await share.create_link("https://example.com/a")
        assert info.value.code == "share_attestation_unavailable"
        assert len(links.requests) == 2 and len(community.calls) == 2

    async def test_upload_retried_with_its_body(self, links, community, clock, tmp_path):
        from app.services import share_upload

        await ca.attestation_for("links")
        clock.now += ca.FORCED_RENEW_MIN_INTERVAL_S
        data = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        path = tmp_path / "a.png"
        path.write_bytes(data)
        staged = share_upload.StagedFile(
            path=path,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_type="image/png",
        )
        links.answers.extend(
            [
                _refusal(403, "attestation_missing"),
                json_response(
                    201,
                    {
                        "id": "AbCdEfG",
                        "url": "https://meshl.ink/AbCdEfG",
                        "expires_at": 1,
                        "size": len(data),
                    },
                ),
            ]
        )
        await share_upload.upload_file(staged)
        assert [r.content for r in links.requests] == [data, data]

    @pytest.mark.parametrize(
        ("answer", "code"),
        [
            (_refusal(403, "radio_not_heard"), "share_radio_not_heard"),
            (_refusal(403, "opted_out"), "share_community_opted_out"),
            (banned_http_exception(), "share_community_banned"),
            (_refusal(503, "attestation_unconfigured"), "share_attestation_unavailable"),
            (_refusal(429, "rate_limited"), "share_attestation_unavailable"),
        ],
    )
    async def test_no_attestation_no_creation(self, links, community, answer, code):
        community.answers = [answer]
        with pytest.raises(ShareError) as info:
            await share.create_link("https://example.com/a")
        assert info.value.code == code
        assert links.requests == []

    async def test_sharing_off_asks_nothing(self, links, community):
        await AppSettingsRepository.update(share_enabled=False)
        with pytest.raises(ShareError) as info:
            await share.create_link("https://example.com/a")
        assert info.value.code == "share_disabled"
        assert community.calls == [] and links.requests == []


# ---------------------------------------------------------------- Remote


REGISTERED = {
    "slug": "k3xwq2m7ta",
    "hostname": "k3xwq2m7ta.meshloom.link",
    "relay_url": "wss://relay.meshloom.link/v1/connect",
}


@pytest.fixture
async def registry(community, community_on, monkeypatch):
    from app.repository.auth import UserRepository

    await UserRepository.create("test-admin", "admin")
    Dns(monkeypatch, {remote.RELAY_HOST: [PUBLIC_IP]})
    answers: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/instances":
            return answers.pop(0) if answers else json_response(201, REGISTERED)
        return httpx.Response(204)

    recorder = Recorder(monkeypatch, handler)
    recorder.answers = answers  # type: ignore[attr-defined]
    return recorder


def _registrations(recorder) -> list[str | None]:
    return [
        r.headers.get(ca.ATTESTATION_HEADER)
        for r in recorder.requests
        if r.url.path == "/v1/instances" and r.method == "POST"
    ]


class TestRemote:
    async def test_registration_carries_the_attestation(self, registry, community):
        state = await remote.enable(SYSTEM_ACTOR)
        assert state.slug == REGISTERED["slug"]
        stored = await CommunityAttestationRepository.get("remote")
        assert stored is not None and _registrations(registry) == [stored.token]
        assert community.calls[0] == (await _key(REMOTE_SERVICE), "remote")

    async def test_without_attestation_it_registers_under_the_grace(self, registry, community):
        community.answers = [_refusal(403, "radio_not_heard")]
        state = await remote.enable(SYSTEM_ACTOR)
        assert state.slug == REGISTERED["slug"]
        assert _registrations(registry) == [None]

    async def test_without_attestation_the_reason_is_the_error(self, registry, community):
        community.answers = [_refusal(403, "radio_not_heard")]
        registry.answers.append(_refusal(403, "attestation_missing"))
        await remote.enable(SYSTEM_ACTOR)
        state = await RemoteRepository.get_state()
        assert state.slug == "" and state.last_error == "remote_radio_not_heard"
        assert len(_registrations(registry)) == 1

    async def test_refused_once_renewed_and_retried(self, registry, community, clock):
        await ca.attestation_for("remote")
        clock.now += ca.FORCED_RENEW_MIN_INTERVAL_S
        registry.answers.append(_refusal(403, "attestation_expired"))
        state = await remote.enable(SYSTEM_ACTOR)
        assert state.slug == REGISTERED["slug"]
        first, second = _registrations(registry)
        assert first and second and first != second

    async def test_registered_instance_falls_back_to_the_grace(self, registry, community, clock):
        await remote.enable(SYSTEM_ACTOR)  # registered, with a slug
        clock.now += ca.FORCED_RENEW_MIN_INTERVAL_S
        registry.answers.extend([_refusal(403, "attestation_invalid")] * 2)
        state = await remote.register(SYSTEM_ACTOR)
        assert state.slug == REGISTERED["slug"]
        headers = _registrations(registry)[1:]
        assert len(headers) == 3 and headers[0] and headers[1] and headers[2] is None

    async def test_new_instance_refused_twice_is_reported(self, registry, community, clock):
        await ca.attestation_for("remote")
        clock.now += ca.FORCED_RENEW_MIN_INTERVAL_S
        registry.answers.extend([_refusal(403, "attestation_invalid")] * 5)
        await remote.enable(SYSTEM_ACTOR)
        state = await RemoteRepository.get_state()
        assert state.slug == "" and state.last_error == "remote_attestation_unavailable"
        assert len(_registrations(registry)) == 2

    async def test_remote_off_asks_nothing(self, registry, community):
        from app.services.remote import RemoteError

        with pytest.raises(RemoteError):
            await remote.register(SYSTEM_ACTOR)
        assert community.calls == [] and registry.requests == []

    async def test_registration_paused_stays_a_registry_error(self, registry, community):
        registry.answers.append(_refusal(503, "registration_paused", {"Retry-After": "3600"}))
        await remote.enable(SYSTEM_ACTOR)
        assert (await RemoteRepository.get_state()).last_error == "registration_paused"


def test_every_attestation_code_is_translated():
    slices = Path(__file__).parent.parent / "frontend" / "src" / "i18n" / "locales" / "slices"
    codes = [c for c in share.SHARE_ERRORS if "attestation" in c or "community_" in c]
    codes += [c for c in remote.REMOTE_ERRORS if "attestation" in c or "community_" in c]
    codes += ["share_radio_not_heard", "remote_radio_not_heard"]
    for lang in ("en", "fr"):
        errors: dict[str, str] = {}
        for name in ("ah", "remote"):
            errors.update(json.loads((slices / f"{name}.{lang}.json").read_text())["errors"])
        for code in codes:
            assert errors.get(code, "").strip(), f"errors.{code} missing in {lang}"


def test_fake_token_has_the_contract_shape():
    token = fake_attestation_token("A" * 64, "links")
    assert ca._TOKEN_RE.fullmatch(token)
    assert ca._token_claims(token)["sub"] == "A" * 64
