"""Sign-in with an OpenID Connect provider (MSHL-427): ``app/auth/oidc.py``,
``app/routers/auth_oidc.py``, against a fake provider on loopback
(``tests/oidc_fake_provider.py``) reached through the real egress guard.

The manual check against a real Keycloak is in ``docs/dev/auth.md``, "OpenID Connect".
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlsplit

import pyotp
import pytest
from joserfc.jwk import RSAKey

from app import secret_store
from app.auth import oidc, totp
from app.repository.auth import (
    AuthAuditRepository,
    AuthSettingsRepository,
    TotpRepository,
    UserRepository,
)
from app.repository.oidc import OidcLinkRepository, OidcProviderRepository
from tests.auth_helpers import create_user, login, make_client
from tests.oidc_fake_provider import CLIENT_ID, CLIENT_SECRET, running_provider

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

# Every way back to the app is relative: from <base>api/auth/oidc/* to <base>.
HOME = "../../../"


@pytest.fixture(autouse=True)
def _fresh_state():
    oidc.reset_caches()
    oidc.reset_auth_requests()
    oidc.reset_failure_audit()
    yield
    oidc.reset_caches()
    oidc.reset_auth_requests()
    oidc.reset_failure_audit()


@pytest.fixture
def provider():
    with running_provider() as fake:
        yield fake


async def _configure(provider, **extra):
    fields = {
        "enabled": True,
        "label": "Test IdP",
        "issuer": provider.issuer,
        "client_id": CLIENT_ID,
        "allow_private_network": True,
    }
    fields.update(extra)
    return await OidcProviderRepository.update(fields, client_secret=CLIENT_SECRET)


async def _sign_in(provider, client, *, remember: bool = False):
    """Start, let the provider answer, follow the callback; returns its response."""
    started = await client.get(f"/api/auth/oidc/start?remember={str(remember).lower()}")
    assert started.status_code == 303, started.text
    location = started.headers["location"]
    if not location.startswith(provider.issuer):
        return started
    path, code, state = provider.authorize(location)
    assert path == oidc.CALLBACK_PATH
    return await client.get(path, params={"code": code, "state": state})


def _error_of(response) -> str | None:
    query = parse_qs(urlsplit(response.headers["location"]).query)
    return query.get("oidc_error", [None])[0]


async def _session(client) -> dict:
    return (await client.get("/api/auth/session")).json()


async def _audit() -> list[dict]:
    return [row.model_dump() for row in await AuthAuditRepository.list_recent(limit=500)]


# --- Sign-in ---------------------------------------------------------------------


async def _redirect_uri_for(client, base: str | None) -> str:
    url = "/api/auth/oidc/start"
    started = await client.get(url, params={"base": base} if base is not None else None)
    assert started.status_code == 303, started.text
    return parse_qs(urlsplit(started.headers["location"]).query)["redirect_uri"][0]


async def test_the_redirect_uri_keeps_a_subpath_only_for_an_address_of_this_instance(provider):
    await create_user()
    await _configure(provider)
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.org/meshloom"])
    async with make_client() as client:
        assert await _redirect_uri_for(client, None) == "http://test/api/auth/oidc/callback"
        # This request's origin, under a prefix (a proxy serving Meshloom at /mc/).
        assert (
            await _redirect_uri_for(client, "http://test/mc/")
            == "http://test/mc/api/auth/oidc/callback"
        )
        # A declared public address, with its prefix (a proxy that rewrites Host).
        assert (
            await _redirect_uri_for(client, "https://mesh.example.org/meshloom/")
            == "https://mesh.example.org/meshloom/api/auth/oidc/callback"
        )
        # Anything else falls back to this request's origin at /.
        for foreign in (
            "https://evil.example/",
            "https://mesh.example.org/other/",
            "http://test/mc",
            "http://test/mc/?x=1",
            "http://user@test/",
            "http://test/../",
            "javascript:alert(1)//",
        ):
            assert (
                await _redirect_uri_for(client, foreign) == "http://test/api/auth/oidc/callback"
            ), foreign
        await login(client)
        view = (
            await client.get("/api/auth/oidc/settings", params={"base": "http://test/mc/"})
        ).json()
    assert view["redirect_uris"] == [
        "http://test/mc/api/auth/oidc/callback",
        "https://mesh.example.org/meshloom/api/auth/oidc/callback",
    ]


async def test_a_subject_link_signs_the_existing_account_in(provider):
    await create_user()
    alice = await create_user("alice", role="member")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    async with make_client() as client:
        anonymous = await _session(client)
        assert anonymous["methods"]["oidc"] is True
        assert anonymous["methods"]["oidc_label"] == "Test IdP"
        response = await _sign_in(provider, client)
        assert response.status_code == 303
        assert response.headers["location"] == HOME
        cookies = response.headers.get_list("set-cookie")
        assert any(c.startswith("meshloom_session=") for c in cookies)
        assert any(c.startswith("meshloom_oidc=;") or "meshloom_oidc=; " in c for c in cookies)
        session = await _session(client)
    assert session["authenticated"] is True
    assert session["user"]["username"] == "alice"
    assert session["user"]["role"] == "member"
    # PKCE and the client secret by HTTP Basic (the default) reached the provider.
    [request] = provider.token_requests
    assert request["basic"] is True and "client_secret" not in request["form"]
    assert request["form"]["code_verifier"]
    rows = [r for r in await _audit() if r["action"] == "login"]
    assert rows[0]["outcome"] == "success"
    assert rows[0]["details"] == {"method": "oidc", "matched": "subject"}
    assert rows[0]["target"] == f"user:{alice.id}"
    [link] = await OidcLinkRepository.list_all()
    assert link.last_login_at is not None


async def test_the_secret_can_go_in_the_form(provider):
    alice = await create_user("alice", role="member")
    await _configure(provider, token_auth_method="client_secret_post")
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    async with make_client() as client:
        response = await _sign_in(provider, client)
    assert response.headers["location"] == HOME
    [request] = provider.token_requests
    assert request["basic"] is False
    assert request["form"]["client_secret"] == CLIENT_SECRET


async def test_an_email_link_needs_a_verified_email_then_binds_the_subject(provider):
    alice = await create_user("alice", role="viewer")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, email="alice@example.org")
    provider.claims = {"sub": "sub-a", "email": "Alice@Example.org", "email_verified": False}
    async with make_client() as client:
        assert _error_of(await _sign_in(provider, client)) == "auth_oidc_no_account"
    provider.claims = {"sub": "sub-a", "email": "Alice@Example.org", "email_verified": True}
    async with make_client() as client:
        response = await _sign_in(provider, client)
        assert response.headers["location"] == HOME
        assert (await _session(client))["user"]["username"] == "alice"
    [link] = await OidcLinkRepository.list_all()
    assert link.subject == "sub-a"
    # Bound: another subject with the same verified e-mail signs nobody in.
    provider.claims = {"sub": "sub-b", "email": "alice@example.org", "email_verified": True}
    async with make_client() as client:
        assert _error_of(await _sign_in(provider, client)) == "auth_oidc_no_account"
    # The subject alone now matches, whatever the e-mail says.
    provider.claims = {"sub": "sub-a"}
    async with make_client() as client:
        assert (await _sign_in(provider, client)).headers["location"] == HOME


async def test_the_email_may_come_from_userinfo_for_the_same_subject_only(provider):
    alice = await create_user("alice", role="viewer")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, email="alice@example.org")
    provider.claims = {"sub": "sub-a"}
    provider.userinfo = {
        "sub": "someone-else",
        "email": "alice@example.org",
        "email_verified": True,
    }
    async with make_client() as client:
        assert _error_of(await _sign_in(provider, client)) == "auth_oidc_no_account"
    provider.userinfo = {"sub": "sub-a", "email": "alice@example.org", "email_verified": True}
    async with make_client() as client:
        assert (await _sign_in(provider, client)).headers["location"] == HOME


async def test_no_link_signs_nobody_in_unless_auto_create_makes_a_viewer(provider):
    await create_user()
    await _configure(provider)
    provider.claims = {"sub": "new-1", "preferred_username": "owner", "name": "New Person"}
    async with make_client() as client:
        assert _error_of(await _sign_in(provider, client)) == "auth_oidc_no_account"
    assert len(await UserRepository.list_all()) == 1
    await OidcProviderRepository.update({"auto_create": True})
    async with make_client() as client:
        response = await _sign_in(provider, client)
        assert response.headers["location"] == HOME
        session = await _session(client)
    # The lowest role of a person, a name that does not clash with "owner", no password.
    assert session["user"]["role"] == "viewer"
    assert session["user"]["username"] == "owner-2"
    created = await UserRepository.get(session["user"]["id"])
    assert created is not None and not created.has_password
    assert created.display_name == "New Person"
    rows = await _audit()
    assert any(
        r["action"] == "user.created" and r["details"] == {"role": "viewer", "provider": "oidc"}
        for r in rows
    )
    # The same subject comes back to the same account.
    async with make_client() as client:
        await _sign_in(provider, client)
        assert (await _session(client))["user"]["id"] == created.id
    assert len(await UserRepository.list_all()) == 2


async def test_disabled_kiosk_and_home_assistant_accounts_are_never_signed_in(provider):
    member = await create_user("member", role="member")
    kiosk = await create_user("screen", password=None, role="kiosk")
    ha = await UserRepository.create("ha", "member", provider="ingress", external_id="x")
    await _configure(provider)
    await OidcLinkRepository.add(member.id, provider.issuer, subject="s-member")
    await OidcLinkRepository.add(kiosk.id, provider.issuer, subject="s-kiosk")
    await OidcLinkRepository.add(ha.id, provider.issuer, subject="s-ha")
    await UserRepository.update(member.id, disabled=True)
    for subject in ("s-member", "s-kiosk", "s-ha"):
        provider.claims = {"sub": subject}
        async with make_client() as client:
            assert _error_of(await _sign_in(provider, client)) == "auth_oidc_no_account"
            assert (await _session(client))["authenticated"] is False


async def test_a_link_of_another_issuer_matches_nothing(provider):
    alice = await create_user("alice", role="member")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, "https://other.example", subject="user-1")
    async with make_client() as client:
        assert _error_of(await _sign_in(provider, client)) == "auth_oidc_no_account"


async def test_the_state_is_bound_to_the_browser_and_the_client_and_used_once(
    provider, monkeypatch
):
    # Every refusal audited here (no throttle window): see the throttle tests below.
    monkeypatch.setattr(oidc, "FAILURE_AUDIT_WINDOW_S", 0.0)
    alice = await create_user("alice", role="member")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    async with make_client() as client:
        started = await client.get("/api/auth/oidc/start")
        path, code, state = provider.authorize(started.headers["location"])
    # Another browser (no state cookie) gets nothing: login CSRF.
    async with make_client() as other:
        refused = await other.get(path, params={"code": code, "state": state})
        assert _error_of(refused) == "auth_oidc_state_invalid"
    # The request was spent by that attempt.
    async with make_client() as client:
        started = await client.get("/api/auth/oidc/start")
        path, code, state = provider.authorize(started.headers["location"])
        # Another User-Agent on the same browser cookie: refused too.
        refused = await client.get(
            path, params={"code": code, "state": state}, headers={"user-agent": "other"}
        )
        assert _error_of(refused) == "auth_oidc_state_invalid"
        started = await client.get("/api/auth/oidc/start")
        path, code, state = provider.authorize(started.headers["location"])
        assert (await client.get(path, params={"code": code, "state": state})).headers[
            "location"
        ] == HOME
        replay = await client.get(path, params={"code": code, "state": state})
        assert _error_of(replay) == "auth_oidc_state_invalid"
    rows = [r for r in await _audit() if r["action"] == "login" and r["outcome"] == "failure"]
    assert all(r["details"]["method"] == "oidc" for r in rows)
    assert len(rows) == 3


def _oidc_failures(rows: list[dict]) -> list[dict]:
    return [
        r
        for r in rows
        if r["action"] == "login"
        and r["outcome"] == "failure"
        and (r["details"] or {}).get("method") == "oidc"
    ]


async def test_callbacks_while_oidc_is_off_write_no_audit_row(provider):
    # Off by default: an anonymous flood of callbacks (any page can make a browser
    # load the URL) must not fill the audit log.
    async with make_client() as client:
        for _ in range(30):
            response = await client.get(oidc.CALLBACK_PATH, params={"state": "x"})
            assert _error_of(response) == "auth_oidc_disabled"
    await _configure(provider, enabled=False)
    async with make_client() as client:
        response = await client.get(oidc.CALLBACK_PATH, params={"state": "x", "code": "y"})
        assert _error_of(response) == "auth_oidc_disabled"
    assert _oidc_failures(await _audit()) == []


async def test_bad_callbacks_are_audited_once_per_address_per_window(provider, monkeypatch):
    await _configure(provider)
    now = [1000.0]
    monkeypatch.setattr(oidc, "failure_audit", oidc.FailureAudit(lambda: now[0]))
    async with make_client() as client:
        for _ in range(30):
            response = await client.get(oidc.CALLBACK_PATH, params={"state": "x"})
            assert _error_of(response) == "auth_oidc_state_invalid"
        # A started flow whose callback fails is throttled the same way.
        started = await client.get("/api/auth/oidc/start")
        _, _, state = provider.authorize(started.headers["location"])
        denied = await client.get(oidc.CALLBACK_PATH, params={"state": state, "error": "nope"})
        assert _error_of(denied) == "auth_oidc_denied"
    assert len(_oidc_failures(await _audit())) == 1
    # Another address gets its own row.
    async with make_client(client=("192.168.1.50", 40000)) as other:
        for _ in range(5):
            await other.get(oidc.CALLBACK_PATH, params={"state": "x"})
    assert len(_oidc_failures(await _audit())) == 2
    # The next window writes one row again.
    now[0] += oidc.FAILURE_AUDIT_WINDOW_S
    async with make_client() as client:
        for _ in range(5):
            await client.get(oidc.CALLBACK_PATH, params={"state": "x"})
    assert len(_oidc_failures(await _audit())) == 3


async def test_bad_callbacks_from_many_addresses_hit_a_global_cap(provider, monkeypatch):
    await _configure(provider)
    monkeypatch.setattr(oidc, "failure_audit", oidc.FailureAudit(lambda: 1000.0))
    for index in range(3 * oidc.FAILURE_AUDIT_MAX_PER_WINDOW):
        async with make_client(client=(f"203.0.113.{index}", 40000)) as client:
            await client.get(oidc.CALLBACK_PATH, params={"state": "x"})
    assert len(_oidc_failures(await _audit())) == oidc.FAILURE_AUDIT_MAX_PER_WINDOW


async def test_on_a_shared_address_bad_callbacks_share_one_row(provider, monkeypatch):
    from app.auth import ratelimit

    monkeypatch.setattr(ratelimit.login_limiter, "address_rules_active", lambda: False)
    await _configure(provider)
    for index in range(10):
        async with make_client(client=(f"172.17.0.{index + 2}", 40000)) as client:
            await client.get(oidc.CALLBACK_PATH, params={"state": "x"})
    assert len(_oidc_failures(await _audit())) == 1


async def test_a_provider_error_is_reported(provider):
    await _configure(provider)
    async with make_client() as client:
        started = await client.get("/api/auth/oidc/start")
        _, _, state = provider.authorize(started.headers["location"])
        response = await client.get(
            oidc.CALLBACK_PATH, params={"state": state, "error": "access_denied"}
        )
    assert _error_of(response) == "auth_oidc_denied"


@pytest.mark.parametrize(
    "claims",
    [
        {"sub": "user-1", "nonce": "not-the-one-sent"},
        {"sub": "user-1", "aud": "another-client"},
        {"sub": "user-1", "aud": [CLIENT_ID, "another-client"]},  # no azp
        {"sub": "user-1", "aud": [CLIENT_ID], "azp": "another-client"},
        {"sub": "user-1", "iss": "http://127.0.0.1:1"},
        {"sub": "user-1", "exp": int(time.time()) - 3600},
        {"sub": "user-1", "iat": None},
        {"sub": None},
    ],
    ids=["nonce", "aud", "aud-without-azp", "foreign-azp", "iss", "expired", "no-iat", "no-sub"],
)
async def test_an_id_token_that_does_not_check_signs_nobody_in(provider, claims):
    alice = await create_user("alice", role="member")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    provider.claims = claims
    async with make_client() as client:
        response = await _sign_in(provider, client)
        assert _error_of(response) == "auth_oidc_token_invalid"
        assert (await _session(client))["authenticated"] is False


async def test_an_issuer_changed_after_the_start_fails_the_callback(provider):
    alice = await create_user("alice", role="member")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    async with make_client() as client:
        started = await client.get("/api/auth/oidc/start")
        assert started.status_code == 303, started.text
        path, code, state = provider.authorize(started.headers["location"])
        with running_provider() as other:
            await OidcProviderRepository.update({"issuer": other.issuer})
            await OidcLinkRepository.add(alice.id, other.issuer, subject="user-1")
            response = await client.get(path, params={"code": code, "state": state})
            assert _error_of(response) == "auth_oidc_state_invalid"
            assert (await _session(client))["authenticated"] is False
            # Nothing reached either provider's token endpoint.
            assert provider.token_requests == [] and other.token_requests == []


async def test_a_token_signed_by_another_key_signs_nobody_in(provider):
    alice = await create_user("alice", role="member")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    provider.sign_with = RSAKey.generate_key(2048, parameters={"kid": "k1"})
    async with make_client() as client:
        assert _error_of(await _sign_in(provider, client)) == "auth_oidc_token_invalid"


async def test_a_wrong_client_secret_fails_the_exchange(provider):
    alice = await create_user("alice", role="member")
    await _configure(provider)
    await OidcProviderRepository.update({}, client_secret="wrong")
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    async with make_client() as client:
        assert _error_of(await _sign_in(provider, client)) == "auth_oidc_exchange_failed"


async def test_an_account_with_totp_still_owes_its_code(provider):
    alice = await create_user("alice", role="member")
    secret = totp.new_secret()
    await TotpRepository.set_pending_secret(alice.id, secret)
    assert await TotpRepository.enable(alice.id, secret, 0, [])
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    async with make_client() as client:
        response = await _sign_in(provider, client, remember=True)
        location = response.headers["location"]
        assert location.startswith(HOME + "#")
        assert not any(
            c.startswith("meshloom_session=") for c in response.headers.get_list("set-cookie")
        )
        fragment = parse_qs(urlsplit(location).fragment)
        assert fragment["methods"] == ["totp,recovery"]
        token = fragment["oidc_2fa"][0]
        assert (await _session(client))["authenticated"] is False
        done = await client.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "code": pyotp.TOTP(secret).now()},
        )
        assert done.status_code == 200, done.text
        assert done.json()["user"]["username"] == "alice"


async def test_an_oidc_sign_in_never_asks_for_the_email_code(provider):
    """MSHL-426: the e-mail code follows a password only; TOTP is still asked."""
    from app.repository.email_factor import EmailFactorRepository

    alice = await create_user("alice", role="member")
    await EmailFactorRepository.set_address(alice.id, "alice@example.org")
    await EmailFactorRepository.set_flags(alice.id, otp_enabled=True)
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    async with make_client() as client:
        response = await _sign_in(provider, client)
        assert response.headers["location"] == HOME
        assert (await _session(client))["user"]["username"] == "alice"
    secret = totp.new_secret()
    await TotpRepository.set_pending_secret(alice.id, secret)
    assert await TotpRepository.enable(alice.id, secret, 0, [])
    async with make_client() as client:
        response = await _sign_in(provider, client)
        fragment = parse_qs(urlsplit(response.headers["location"]).fragment)
        assert fragment["methods"] == ["totp,recovery"]


async def test_off_or_incomplete_means_no_button_and_no_flow(provider):
    await create_user()
    await _configure(provider, enabled=False)
    async with make_client() as client:
        assert (await _session(client))["methods"]["oidc"] is False
        started = await client.get("/api/auth/oidc/start")
        assert _error_of(started) == "auth_oidc_disabled"


async def test_plain_http_only_reaches_a_private_address():
    with pytest.raises(oidc.OidcError) as refused:
        await oidc._check_http("http://93.184.215.14/", allow_private_network=True)
    assert refused.value.code == "auth_oidc_http_public"
    with pytest.raises(oidc.OidcError):
        await oidc._check_http("http://127.0.0.1/", allow_private_network=False)
    await oidc._check_http("http://127.0.0.1/", allow_private_network=True)


def test_auth_requests_expire_and_are_bounded():
    now = {"t": 0.0}
    requests = oidc.AuthRequests(clock_fn=lambda: now["t"])
    issued = requests.issue(issuer="i", redirect_uri="r", client="c", remember=False)
    now["t"] = oidc.REQUEST_TTL_S + 1
    assert requests.take(issued.state, issued.browser_token, "c") is None
    for _ in range(oidc.REQUEST_MAX_ENTRIES + 5):
        requests.issue(issuer="i", redirect_uri="r", client="c", remember=False)
    assert len(requests) == oidc.REQUEST_MAX_ENTRIES


# --- Settings and links (admin) ------------------------------------------------------


async def test_the_settings_keep_the_secret_write_only_and_sealed(provider, test_db):
    await create_user()
    async with make_client() as client:
        await login(client)
        incomplete = await client.patch("/api/auth/oidc/settings", json={"enabled": True})
        assert incomplete.status_code == 400
        assert incomplete.json()["detail"]["code"] == "auth_oidc_incomplete"
        plain = await client.patch("/api/auth/oidc/settings", json={"issuer": provider.issuer})
        assert plain.json()["detail"]["code"] == "auth_oidc_issuer_invalid"
        saved = await client.patch(
            "/api/auth/oidc/settings",
            json={
                "preset": "keycloak",
                "issuer": provider.issuer,
                "allow_private_network": True,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scopes": "email openid profile",
                "enabled": True,
            },
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert body["client_secret_set"] is True and CLIENT_SECRET not in saved.text
        assert body["scopes"] == "openid email profile"
        assert body["redirect_uris"] == ["http://test/api/auth/oidc/callback"]
        tested = await client.post("/api/auth/oidc/test")
        assert tested.status_code == 200, tested.text
        assert tested.json()["keys"] == 1
        # A null secret keeps the stored one; clearing removes it.
        kept = await client.patch("/api/auth/oidc/settings", json={"label": "Mine"})
        assert kept.json()["client_secret_set"] is True
        async with test_db.tx() as conn:
            async with conn.execute("SELECT client_secret FROM oidc_provider") as cursor:
                stored = (await cursor.fetchone())[0]
        assert stored.startswith(secret_store.PREFIX) and CLIENT_SECRET not in stored
        assert await OidcProviderRepository.client_secret() == CLIENT_SECRET
        cleared = await client.patch("/api/auth/oidc/settings", json={"clear_client_secret": True})
        assert cleared.json()["client_secret_set"] is False
    audit_text = json.dumps(await _audit())
    assert CLIENT_SECRET not in audit_text
    assert '"target": "oidc_provider"' in audit_text


async def test_the_test_reports_an_issuer_mismatch(provider):
    await create_user()
    await _configure(provider, issuer=provider.issuer + "/")
    async with make_client() as client:
        await login(client)
        response = await client.post("/api/auth/oidc/test")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "auth_oidc_issuer_mismatch"
    assert detail["params"]["issuer"] == provider.issuer


async def test_the_oidc_admin_routes_are_refused_to_a_member(provider):
    await create_user()
    await create_user("alice", role="member")
    async with make_client() as client:
        await login(client, "alice")
        for method, path in (
            ("GET", "/api/auth/oidc/settings"),
            ("PATCH", "/api/auth/oidc/settings"),
            ("POST", "/api/auth/oidc/test"),
            ("GET", "/api/auth/oidc/links"),
            ("POST", "/api/auth/oidc/links"),
            ("DELETE", "/api/auth/oidc/links/1"),
        ):
            response = await client.request(method, path, json={})
            assert response.status_code == 403, (method, path)


async def test_links_are_added_listed_and_removed(provider):
    admin = await create_user()
    alice = await create_user("alice", role="member")
    kiosk = await create_user("screen", password=None, role="kiosk")
    await _configure(provider)
    async with make_client() as client:
        await login(client)
        both = await client.post(
            "/api/auth/oidc/links", json={"user_id": alice.id, "subject": "s", "email": "a@b.c"}
        )
        assert both.json()["detail"]["code"] == "auth_oidc_link_invalid"
        refused = await client.post(
            "/api/auth/oidc/links", json={"user_id": kiosk.id, "subject": "s"}
        )
        assert refused.json()["detail"]["code"] == "auth_oidc_link_user_invalid"
        added = await client.post(
            "/api/auth/oidc/links", json={"user_id": alice.id, "email": " Alice@Example.ORG "}
        )
        assert added.status_code == 201, added.text
        assert added.json()["email"] == "alice@example.org"
        again = await client.post(
            "/api/auth/oidc/links", json={"user_id": admin.id, "email": "alice@example.org"}
        )
        assert again.status_code == 409
        listed = (await client.get("/api/auth/oidc/links")).json()
        assert listed["issuer"] == provider.issuer
        assert [(row["username"], row["email"]) for row in listed["links"]] == [
            ("alice", "alice@example.org")
        ]
        link_id = listed["links"][0]["id"]
        assert (await client.delete(f"/api/auth/oidc/links/{link_id}")).status_code == 204
        assert (await client.delete(f"/api/auth/oidc/links/{link_id}")).status_code == 404
    actions = [r["action"] for r in await _audit()]
    assert "oidc.linked" in actions and "oidc.unlinked" in actions


async def test_deleting_an_account_deletes_its_links(provider):
    alice = await create_user("alice", role="member")
    await _configure(provider)
    await OidcLinkRepository.add(alice.id, provider.issuer, subject="user-1")
    assert await UserRepository.delete(alice.id)
    assert await OidcLinkRepository.list_all() == []


async def test_the_database_download_drops_the_client_secret(provider, test_db):
    from app.services.backup_sections import prepare_sqlite_snapshot

    await _configure(provider)
    async with test_db.tx() as conn:
        cleared = await prepare_sqlite_snapshot(conn, 0)
        async with conn.execute("SELECT client_secret FROM oidc_provider") as cursor:
            assert (await cursor.fetchone())[0] is None
    assert "oidc_provider.client_secret" in cleared


async def test_ingress_never_starts_a_sign_in(provider, monkeypatch):
    await _configure(provider)
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    async with make_client(client=("172.30.32.2", 50000)) as client:
        response = await client.get("/api/auth/oidc/start")
    assert _error_of(response) == "auth_ingress_only"
