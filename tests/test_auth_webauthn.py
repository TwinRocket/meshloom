"""Passkeys (Access T52): ``app/auth/webauthn.py``, ``app/routers/auth_webauthn.py``,
the passkey second factor of ``POST /api/auth/login/2fa``."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pyotp
import pytest
from webauthn import base64url_to_bytes

from app.auth import totp, webauthn
from app.auth.models import User
from app.repository.auth import (
    AuthAuditRepository,
    AuthSessionRepository,
    AuthSettingsRepository,
    PasskeyRepository,
    StoredPasskey,
    UserRepository,
)
from tests.auth_helpers import ADMIN, PASSWORD, create_user, login, make_client
from tests.webauthn_authenticator import SoftAuthenticator

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

PUBLIC = "https://meshloom.example"
VECTORS = json.loads((Path(__file__).parent / "fixtures" / "webauthn_vectors.json").read_text())


def _scope(host: str, scheme: str = "https", *, relay: bool = False) -> dict:
    scope: dict = {
        "type": "http",
        "scheme": scheme,
        "headers": [(b"host", host.encode())],
        "client": ("10.0.0.5", 1234),
        "state": {"via": "relay"} if relay else {},
    }
    return scope


def _owner(user_id: int = 1, created_at: int = 0) -> User:
    return User(
        id=user_id,
        username=ADMIN,
        display_name="",
        role="admin",
        provider="local",
        has_password=True,
        disabled=False,
        must_change_password=False,
        created_at=created_at,
        updated_at=created_at,
    )


@pytest.fixture
async def public_address():
    await AuthSettingsRepository.update(public_urls=[PUBLIC])


@pytest.fixture
def relay():
    from app.middleware import host_guard

    host_guard.set_remote_hostname("abcde12345.relay.example")
    yield "abcde12345.relay.example"
    host_guard.set_remote_hostname(None)


def relay_client(host: str) -> httpx.AsyncClient:
    """Requests as the relay dispatches them: ``scope["state"]["via"] = "relay"``."""
    from app.main import app

    async def tagged(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope.setdefault("state", {})["via"] = "relay"
        await app(scope, receive, send)

    transport = httpx.ASGITransport(app=tagged, client=("127.0.0.1", 50000))
    return httpx.AsyncClient(transport=transport, base_url=f"http://{host}")


async def _register(client, authenticator: SoftAuthenticator, name: str = "Laptop") -> dict:
    options = await client.post("/api/auth/webauthn/register/options", json={"password": PASSWORD})
    assert options.status_code == 200, options.text
    credential = authenticator.create(options.json(), PUBLIC)
    stored = await client.post(
        "/api/auth/webauthn/register/verify", json={"credential": credential, "name": name}
    )
    assert stored.status_code == 200, stored.text
    return stored.json()


async def _passwordless(client, authenticator: SoftAuthenticator, **kwargs) -> httpx.Response:
    options = await client.post("/api/auth/webauthn/login/options", json={})
    assert options.status_code == 200, options.text
    assert options.json()["userVerification"] == "required"
    assert options.json()["allowCredentials"] == []
    extra = {"remember": kwargs.pop("remember")} if "remember" in kwargs else {}
    credential = authenticator.get(options.json(), kwargs.pop("origin", PUBLIC), **kwargs)
    return await client.post(
        "/api/auth/webauthn/login/verify", json={"credential": credential, **extra}
    )


async def _audit_text() -> str:
    rows = await AuthAuditRepository.list_recent(limit=500)
    return json.dumps([row.model_dump() for row in rows])


# --- py_webauthn's vectors through Meshloom's checks -------------------------------


def _vector_rp() -> webauthn.RelyingParty:
    rp = webauthn.relying_party_for(_scope("localhost:5000", "http"), (), frozenset())
    assert rp == webauthn.RelyingParty("localhost", ("http://localhost:5000",))
    return rp


def _ceremony(purpose: webauthn.Purpose, challenge: str, rp_id: str = "localhost"):
    return webauthn.Ceremony(
        purpose=purpose,
        challenge=base64url_to_bytes(challenge),
        rp_id=rp_id,
        user_id=None,
        binding="",
        expires_at=0,
    )


def _vector_passkey() -> StoredPasskey:
    vector = VECTORS["authentication"]
    return StoredPasskey(
        id=1,
        user_id=1,
        credential_id=base64url_to_bytes(vector["credential"]["rawId"]),
        public_key=base64url_to_bytes(vector["credential_public_key"]),
        sign_count=vector["sign_count"],
        transports=[],
        aaguid="",
        name="vector",
        backup_eligible=False,
        backed_up=False,
        created_at=0,
    )


def test_the_registration_vector_verifies_and_a_wrong_rp_id_or_origin_is_refused():
    vector = VECTORS["registration"]
    rp = _vector_rp()
    ceremony = _ceremony("register", vector["challenge"])
    assert webauthn.credential_challenge(vector["credential"]) == ceremony.challenge
    verified, transports = webauthn.verify_registration(rp, vector["credential"], ceremony)
    assert verified.aaguid == "00000000-0000-0000-0000-000000000000"
    assert verified.credential_id == base64url_to_bytes(vector["credential"]["rawId"])
    for wrong in (
        webauthn.RelyingParty("meshloom.example", ("http://localhost:5000",)),
        webauthn.RelyingParty("localhost", ("https://meshloom.example",)),
    ):
        with pytest.raises(webauthn.PasskeyInvalid):
            webauthn.verify_registration(wrong, vector["credential"], ceremony)
    with pytest.raises(webauthn.PasskeyInvalid):
        webauthn.verify_registration(
            rp, vector["credential"], _ceremony("register", "AAAAAAAAAAAAAAAAAAAAAA")
        )


def test_the_authentication_vector_verifies_and_the_counter_must_grow():
    vector = VECTORS["authentication"]
    rp = _vector_rp()
    ceremony = _ceremony("login", vector["challenge"])
    passkey = _vector_passkey()
    verified = webauthn.verify_assertion(
        rp, vector["credential"], ceremony, passkey, _owner(), require_user_verification=False
    )
    assert verified.new_sign_count == vector["new_sign_count"]
    # The vector's authenticator did not verify the user: a passwordless sign-in refuses it.
    with pytest.raises(webauthn.PasskeyInvalid):
        webauthn.verify_assertion(
            rp, vector["credential"], ceremony, passkey, _owner(), require_user_verification=True
        )
    # A counter at or past the one presented: a cloned authenticator or a replay.
    replayed = passkey.model_copy(update={"sign_count": vector["new_sign_count"]})
    with pytest.raises(webauthn.PasskeyInvalid) as refused:
        webauthn.verify_assertion(
            rp, vector["credential"], ceremony, replayed, _owner(), require_user_verification=False
        )
    assert refused.value.reason == "sign_count_not_increased"
    for wrong in (
        webauthn.RelyingParty("meshloom.example", ("http://localhost:5000",)),
        webauthn.RelyingParty("localhost", ("http://localhost:5001",)),
    ):
        with pytest.raises(webauthn.PasskeyInvalid):
            webauthn.verify_assertion(
                wrong,
                vector["credential"],
                ceremony,
                passkey,
                _owner(),
                require_user_verification=False,
            )


# --- Relying party rules ---------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "scheme", "public_urls", "expected"),
    [
        ("192.168.1.10:8000", "http", (), "insecure"),
        ("meshloom.example", "http", (PUBLIC,), "insecure"),
        ("192.168.1.10", "https", ("https://192.168.1.10",), "host"),
        ("other.example", "https", (PUBLIC,), "host"),
        ("meshloom.local", "https", (), "host"),
    ],
)
def test_requests_without_a_declared_secure_name_cannot_run_a_ceremony(
    host, scheme, public_urls, expected
):
    with pytest.raises(webauthn.PasskeyUnavailable) as refused:
        webauthn.relying_party_for(_scope(host, scheme), public_urls, frozenset())
    assert refused.value.reason == expected


def test_the_rp_id_follows_the_public_addresses_the_relay_and_localhost():
    urls = ("https://meshloom.example:8443/app", PUBLIC, "https://other.example")
    rp = webauthn.relying_party_for(_scope("MeshLoom.example:8443"), urls, frozenset())
    assert rp == webauthn.RelyingParty(
        "meshloom.example", ("https://meshloom.example:8443", "https://meshloom.example")
    )
    remote = frozenset({"abcde12345.relay.example"})
    rp = webauthn.relying_party_for(
        _scope("abcde12345.relay.example", "http", relay=True), (), remote
    )
    assert rp == webauthn.RelyingParty(
        "abcde12345.relay.example", ("https://abcde12345.relay.example",)
    )
    with pytest.raises(webauthn.PasskeyUnavailable):
        webauthn.relying_party_for(_scope("meshloom.example", relay=True), (PUBLIC,), remote)
    assert webauthn.relying_party_for(_scope("localhost:8000", "http"), (), frozenset()) == (
        webauthn.RelyingParty("localhost", ("http://localhost:8000",))
    )


def test_ingress_cannot_run_a_ceremony(monkeypatch):
    from app.auth.providers import ingress

    monkeypatch.setattr(ingress, "is_ingress_request", lambda scope: True)
    with pytest.raises(webauthn.PasskeyUnavailable) as refused:
        webauthn.relying_party_for(_scope("meshloom.example"), (PUBLIC,), frozenset())
    assert refused.value.reason == "ingress"


async def test_methods_passkey_is_false_on_a_lan_ip_and_true_through_the_relay(relay):
    async with make_client("http://192.168.1.10:8000") as client:
        body = (await client.get("/api/auth/session")).json()
        assert body["methods"]["passkey"] is False
    async with relay_client(relay) as client:
        body = (await client.get("/api/auth/session")).json()
        assert body["methods"]["passkey"] is True


async def test_methods_passkey_follows_the_declared_public_address(public_address):
    async with make_client(PUBLIC) as client:
        assert (await client.get("/api/auth/session")).json()["methods"]["passkey"] is True
    async with make_client("https://undeclared.example") as client:
        assert (await client.get("/api/auth/session")).json()["methods"]["passkey"] is False


# --- Ceremonies ------------------------------------------------------------------


def test_a_challenge_is_used_once_bound_and_short_lived():
    now = {"t": 0.0}
    store = webauthn.Ceremonies(clock=lambda: now["t"])
    challenge = store.issue("login", "meshloom.example", None, "client:a")
    with pytest.raises(webauthn.PasskeyInvalid):
        store.take(challenge, "login", "client:b", "meshloom.example")
    # A wrong try used it up.
    with pytest.raises(webauthn.PasskeyInvalid):
        store.take(challenge, "login", "client:a", "meshloom.example")
    challenge = store.issue("login", "meshloom.example", None, "client:a")
    with pytest.raises(webauthn.PasskeyInvalid):
        store.take(challenge, "register", "client:a", "meshloom.example")
    challenge = store.issue("login", "meshloom.example", None, "client:a")
    assert store.take(challenge, "login", "client:a", "meshloom.example").challenge == challenge
    challenge = store.issue("login", "meshloom.example", None, "client:a")
    now["t"] = webauthn.CEREMONY_TTL_S + 1
    with pytest.raises(webauthn.PasskeyInvalid):
        store.take(challenge, "login", "client:a", "meshloom.example")
    assert len(store) == 0


# --- Registration, sign-in, removal ------------------------------------------------


async def test_register_then_sign_in_without_a_password(public_address):
    await create_user()
    authenticator = SoftAuthenticator(backup_eligible=True)
    async with make_client(PUBLIC) as owner:
        await login(owner)
        stored = await _register(owner, authenticator, name="  My   phone ")
        assert stored["name"] == "My phone"
        assert stored["backup_eligible"] is True and stored["transports"] == ["internal", "hybrid"]
        listed = (await owner.get("/api/auth/webauthn/credentials")).json()
        assert [item["id"] for item in listed] == [stored["id"]]
        assert "public_key" not in listed[0] and "credential_id" not in listed[0]

    async with make_client(PUBLIC) as client:
        response = await _passwordless(client, authenticator)
        assert response.status_code == 200, response.text
        assert response.json()["authenticated"] is True
        assert (await client.get("/api/auth/me")).json()["username"] == ADMIN
    [passkey] = await PasskeyRepository.list_for_user(1)
    assert passkey.sign_count == 1 and passkey.last_used_at is not None
    text = await _audit_text()
    assert '"passkey.added"' in text and '"method": "passkey"' in text
    assert authenticator.credential_id.hex() not in text


@pytest.mark.parametrize(
    ("remember", "long_lived"),
    [(True, True), (False, False), (None, False)],
    ids=["true", "false", "absent"],
)
async def test_passkey_sign_in_honours_remember(public_address, remember, long_lived):
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, authenticator)
    kwargs = {} if remember is None else {"remember": remember}
    async with make_client(PUBLIC) as client:
        response = await _passwordless(client, authenticator, **kwargs)
        assert response.status_code == 200, response.text
        [cookie] = [c for c in response.headers.get_list("set-cookie") if "meshloom_session=" in c]
        assert ("Max-Age=" in cookie) is long_lived
        assert "Expires" not in cookie
    sessions = await AuthSessionRepository.list_for_user(1)
    newest = sessions[0]
    assert newest.remember is long_lived


async def test_an_assertion_is_used_once_and_needs_user_verification(public_address):
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, authenticator)
    async with make_client(PUBLIC) as client:
        options = (await client.post("/api/auth/webauthn/login/options", json={})).json()
        credential = authenticator.get(options, PUBLIC)
        first = await client.post(
            "/api/auth/webauthn/login/verify", json={"credential": credential}
        )
        assert first.status_code == 200
        again = await client.post(
            "/api/auth/webauthn/login/verify", json={"credential": credential}
        )
        assert again.status_code == 401
        assert again.json()["detail"]["code"] == "auth_passkey_invalid"
    async with make_client(PUBLIC) as client:
        refused = await _passwordless(client, authenticator, uv=False)
        assert refused.status_code == 401


@pytest.mark.parametrize(
    "kwargs",
    [{"origin": "https://evil.example"}, {"rp_id": "evil.example"}, {"user_handle": b"x" * 32}],
)
async def test_a_wrong_origin_rp_id_or_user_handle_is_refused(public_address, kwargs):
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, authenticator)
    async with make_client(PUBLIC) as client:
        response = await _passwordless(client, authenticator, **kwargs)
        assert response.status_code == 401
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_a_registration_for_another_origin_is_refused(public_address):
    await create_user()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        options = (
            await owner.post("/api/auth/webauthn/register/options", json={"password": PASSWORD})
        ).json()
        credential = SoftAuthenticator().create(options, "https://evil.example")
        response = await owner.post(
            "/api/auth/webauthn/register/verify", json={"credential": credential, "name": "x"}
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "auth_passkey_invalid"
    assert await PasskeyRepository.list_for_user(1) == []


async def test_a_counter_that_goes_back_is_refused(public_address):
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, authenticator)
    async with make_client(PUBLIC) as client:
        assert (await _passwordless(client, authenticator)).status_code == 200
        assert (await _passwordless(client, authenticator)).status_code == 200
        authenticator.counter = 0  # a clone of the key from before those two uses
        assert (await _passwordless(client, authenticator)).status_code == 401
    assert "passkey_sign_count_not_increased" in await _audit_text()
    # An authenticator that always reports 0 (a synced passkey) keeps working.
    synced = SoftAuthenticator(counter_step=0)
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, synced)
    async with make_client(PUBLIC) as client:
        assert (await _passwordless(client, synced)).status_code == 200
        assert (await _passwordless(client, synced)).status_code == 200


async def test_registration_needs_the_password_https_and_its_own_session(public_address):
    await create_user()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        wrong = await owner.post("/api/auth/webauthn/register/options", json={"password": "nope"})
        assert wrong.status_code == 403
        options = (
            await owner.post("/api/auth/webauthn/register/options", json={"password": PASSWORD})
        ).json()
        credential = SoftAuthenticator().create(options, PUBLIC)
        async with make_client(PUBLIC) as other:
            await login(other)
            response = await other.post(
                "/api/auth/webauthn/register/verify", json={"credential": credential, "name": "x"}
            )
            assert response.status_code == 400
    async with make_client("http://192.168.1.10:8000") as lan:
        await login(lan)
        response = await lan.post(
            "/api/auth/webauthn/register/options", json={"password": PASSWORD}
        )
        assert response.status_code == 400
        assert response.json()["detail"] == {
            "code": "auth_passkey_unsupported",
            "message": response.json()["detail"]["message"],
            "params": {"reason": "insecure"},
        }
        response = await lan.post("/api/auth/webauthn/login/options", json={})
        assert response.status_code == 400


async def test_kiosk_accounts_and_non_session_credentials_have_no_passkeys(public_address):
    kiosk = await create_user("kiosk", password=None, role="kiosk")
    assert not webauthn.applicable(kiosk)
    member = await create_user("member", role="member")
    assert webauthn.applicable(member)
    from app.auth.providers import token as token_provider

    _, secret = await token_provider.issue_token(member, "script", ["read"])
    async with make_client(PUBLIC) as client:
        response = await client.post(
            "/api/auth/webauthn/register/options",
            json={"password": PASSWORD},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert response.status_code == 403, response.text


async def test_removal_needs_the_password_and_the_password_still_signs_in(public_address):
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        stored = await _register(owner, authenticator)
        url = f"/api/auth/webauthn/credentials/{stored['id']}"
        wrong = await owner.request("DELETE", url, json={"password": "nope"})
        assert wrong.status_code == 403
        removed = await owner.request("DELETE", url, json={"password": PASSWORD})
        assert removed.status_code == 200
        again = await owner.request("DELETE", url, json={"password": PASSWORD})
        assert again.status_code == 404
    assert await PasskeyRepository.list_for_user(1) == []
    async with make_client(PUBLIC) as client:
        assert (await _passwordless(client, authenticator)).status_code == 401
        response = await login(client)
        assert response.json()["authenticated"] is True
    assert '"passkey.removed"' in await _audit_text()


async def test_another_account_cannot_remove_a_passkey(public_address):
    await create_user()
    await create_user("member", role="member")
    async with make_client(PUBLIC) as owner:
        await login(owner)
        stored = await _register(owner, SoftAuthenticator())
    async with make_client(PUBLIC) as member:
        await login(member, "member")
        response = await member.request(
            "DELETE", f"/api/auth/webauthn/credentials/{stored['id']}", json={"password": PASSWORD}
        )
        assert response.status_code == 404
        assert (await member.get("/api/auth/webauthn/credentials")).json() == []
    assert len(await PasskeyRepository.list_for_user(1)) == 1


async def test_a_disabled_account_cannot_sign_in_with_its_passkey(public_address):
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, authenticator)
    await create_user("second")
    await UserRepository.update(1, disabled=True)
    async with make_client(PUBLIC) as client:
        assert (await _passwordless(client, authenticator)).status_code == 401


async def test_passwordless_options_are_rate_limited_per_address(public_address):
    async with make_client(PUBLIC) as client:
        for _ in range(webauthn.OPTIONS_PER_MINUTE):
            assert (
                await client.post("/api/auth/webauthn/login/options", json={})
            ).status_code == 200
        refused = await client.post("/api/auth/webauthn/login/options", json={})
        assert refused.status_code == 429
        assert int(refused.headers["Retry-After"]) >= 1


async def test_wrong_assertions_count_as_failed_logins(public_address):
    from app.auth import ratelimit

    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, authenticator)
    async with make_client(PUBLIC) as client:
        for _ in range(ratelimit.PAIR_FREE_FAILURES):
            assert (await _passwordless(client, authenticator, uv=False)).status_code == 401
        refused = await _passwordless(client, authenticator)
        assert refused.status_code == 429


# --- Second factor -----------------------------------------------------------------


async def _enrol_totp(client) -> str:
    secret = (await client.post("/api/auth/2fa/enrol", json={"password": PASSWORD})).json()[
        "secret"
    ]
    confirmed = await client.post(
        "/api/auth/2fa/confirm", json={"code": pyotp.TOTP(secret).at(totp.clock())}
    )
    assert confirmed.status_code == 200, confirmed.text
    return secret


async def test_a_passkey_is_the_second_factor_when_totp_is_enrolled(public_address):
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, authenticator)
        await _enrol_totp(owner)

    async with make_client(PUBLIC) as client:
        challenge = (await login(client)).json()["second_factor"]
        assert challenge["methods"] == ["totp", "recovery", "passkey"]
        token = challenge["pending_token"]
        options = await client.post(
            "/api/auth/webauthn/login/options", json={"pending_token": token}
        )
        assert options.status_code == 200, options.text
        assert options.json()["userVerification"] == "preferred"
        assert len(options.json()["allowCredentials"]) == 1
        # Second-factor options never open a passwordless session.
        credential = authenticator.get(options.json(), PUBLIC, uv=False)
        direct = await client.post(
            "/api/auth/webauthn/login/verify", json={"credential": credential}
        )
        assert direct.status_code == 401
        options = await client.post(
            "/api/auth/webauthn/login/options", json={"pending_token": token}
        )
        credential = authenticator.get(options.json(), PUBLIC, uv=False)
        response = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "passkey": credential}
        )
        assert response.status_code == 200, response.text
        assert response.json()["authenticated"] is True
    assert '"factor": "passkey"' in await _audit_text()


async def test_a_passkey_second_factor_is_bound_to_its_pending_login_and_account(public_address):
    await create_user()
    await create_user("member", role="member")
    mine, theirs = SoftAuthenticator(), SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, mine)
        await _enrol_totp(owner)
    async with make_client(PUBLIC) as member:
        await login(member, "member")
        await _register(member, theirs)

    async with make_client(PUBLIC) as client:
        token = (await login(client)).json()["second_factor"]["pending_token"]
        options = (
            await client.post("/api/auth/webauthn/login/options", json={"pending_token": token})
        ).json()
        # Another account's passkey answering the owner's challenge.
        response = await client.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "passkey": theirs.get(options, PUBLIC)},
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "auth_2fa_invalid"
        # A challenge for a passwordless sign-in is not one for this pending login.
        options = (await client.post("/api/auth/webauthn/login/options", json={})).json()
        response = await client.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "passkey": mine.get(options, PUBLIC)},
        )
        assert response.status_code == 401
        # Neither a code nor a passkey, or both: a 422.
        response = await client.post("/api/auth/login/2fa", json={"pending_token": token})
        assert response.status_code == 422
        # Nothing opened a session.
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_without_totp_a_passkey_never_makes_the_password_ask_for_more(public_address):
    await create_user()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        await _register(owner, SoftAuthenticator())
    async with make_client("http://192.168.1.10:8000") as lan:
        response = await login(lan)
        assert response.json()["authenticated"] is True
    async with make_client(PUBLIC) as client:
        response = await login(client)
        assert response.json()["authenticated"] is True


async def test_on_plain_http_the_second_factor_does_not_offer_a_passkey():
    await create_user()
    await PasskeyRepository.add(
        1,
        credential_id=b"\x01" * 16,
        public_key=b"\x02",
        sign_count=0,
        transports=[],
        aaguid="",
        name="x",
        backup_eligible=False,
        backed_up=False,
    )
    async with make_client("http://192.168.1.10:8000") as client:
        await login(client)
        await _enrol_totp(client)
    async with make_client("http://192.168.1.10:8000") as client:
        challenge = (await login(client)).json()["second_factor"]
        assert challenge["methods"] == ["totp", "recovery"]


# --- Repository ----------------------------------------------------------------------


async def test_a_credential_id_is_stored_once_and_the_count_is_bounded():
    from app.repository.auth import MAX_PASSKEYS_PER_USER, PasskeyLimitError

    await create_user()
    await create_user("member", role="member")

    async def add(user_id: int, credential_id: bytes):
        return await PasskeyRepository.add(
            user_id,
            credential_id=credential_id,
            public_key=b"\x02",
            sign_count=0,
            transports=["usb"],
            aaguid="",
            name="x",
            backup_eligible=False,
            backed_up=False,
        )

    assert await add(1, b"\x01") is not None
    assert await add(2, b"\x01") is None
    for index in range(2, MAX_PASSKEYS_PER_USER + 1):
        assert await add(1, bytes([index])) is not None
    with pytest.raises(PasskeyLimitError):
        await add(1, b"\xff")
    [first, *_] = await PasskeyRepository.list_for_user(1)
    assert await PasskeyRepository.record_use(first.id, 0, 5, True)
    # The counter moved meanwhile: a concurrent use of the same assertion loses.
    assert not await PasskeyRepository.record_use(first.id, 0, 5, True)
    assert await PasskeyRepository.delete_all_for_user(1) == MAX_PASSKEYS_PER_USER


# --- Review round 1 ---------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1_800_000_015.0}
    monkeypatch.setattr(totp, "clock", lambda: now["t"])
    return now


async def test_with_totp_on_the_password_alone_cannot_add_a_passkey(public_address, clock):
    """A stolen session and the password must not give a way in that skips TOTP."""
    await create_user()
    authenticator = SoftAuthenticator()
    async with make_client(PUBLIC) as owner:
        await login(owner)
        secret = await _enrol_totp(owner)
    async with make_client(PUBLIC) as attacker:
        token = (await login(attacker)).json()["second_factor"]["pending_token"]
        clock["t"] += totp.PERIOD_S
        signed_in = await attacker.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "code": pyotp.TOTP(secret).at(clock["t"])},
        )
        assert signed_in.status_code == 200
        url = "/api/auth/webauthn/register/options"
        refused = await attacker.post(url, json={"password": PASSWORD})
        assert refused.status_code == 403
        assert refused.json()["detail"]["code"] == "auth_2fa_required"
        wrong = await attacker.post(url, json={"password": PASSWORD, "code": "000000"})
        assert wrong.status_code == 403
        assert wrong.json()["detail"]["code"] == "auth_2fa_invalid"
    assert await PasskeyRepository.list_for_user(1) == []
    async with make_client(PUBLIC) as client:
        assert (await _passwordless(client, authenticator)).status_code == 401

    # The owner, with the password and a fresh code, can.
    async with make_client(PUBLIC) as owner:
        token = (await login(owner)).json()["second_factor"]["pending_token"]
        clock["t"] += totp.PERIOD_S
        await owner.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "code": pyotp.TOTP(secret).at(clock["t"])},
        )
        clock["t"] += totp.PERIOD_S
        options = await owner.post(
            url, json={"password": PASSWORD, "code": pyotp.TOTP(secret).at(clock["t"])}
        )
        assert options.status_code == 200, options.text
        credential = authenticator.create(options.json(), PUBLIC)
        stored = await owner.post(
            "/api/auth/webauthn/register/verify", json={"credential": credential, "name": "Key"}
        )
        assert stored.status_code == 200
    assert "second_factor_missing" in await _audit_text()


def test_the_public_table_refuses_instead_of_evicting_and_purposes_stay_apart(monkeypatch):
    monkeypatch.setattr(
        webauthn, "CEREMONY_MAX_ENTRIES", {"login": 3, "register": 2, "second_factor": 2}
    )
    now = {"t": 0.0}
    store = webauthn.Ceremonies(clock=lambda: now["t"])
    pending = store.issue("register", "meshloom.example", 1, "session:a")
    second = store.issue("second_factor", "meshloom.example", 1, "pending:x")
    first_login = store.issue("login", "meshloom.example", None, "client:a")
    for _ in range(2):
        store.issue("login", "meshloom.example", None, "client:flood")
    with pytest.raises(webauthn.CeremoniesFull):
        store.issue("login", "meshloom.example", None, "client:flood")
    # The flood pushed out nothing: the in-flight ceremonies still complete.
    assert store.take(first_login, "login", "client:a", "meshloom.example")
    assert store.take(pending, "register", "session:a", "meshloom.example")
    assert store.take(second, "second_factor", "pending:x", "meshloom.example")
    # Expired challenges make room again.
    now["t"] = webauthn.CEREMONY_TTL_S + 1
    store.issue("login", "meshloom.example", None, "client:b")
    # An authenticated table drops its own oldest entry.
    old = store.issue("register", "meshloom.example", 1, "session:a")
    store.issue("register", "meshloom.example", 1, "session:b")
    store.issue("register", "meshloom.example", 1, "session:c")
    with pytest.raises(webauthn.PasskeyInvalid):
        store.take(old, "register", "session:a", "meshloom.example")


async def test_options_go_through_the_login_limiter_and_a_full_table(public_address, monkeypatch):
    from app.auth import ratelimit

    def refuse(address, username, device=None):
        raise ratelimit.LoginDelayed("ip", 42.0, True)

    async with make_client(PUBLIC) as client:
        monkeypatch.setattr(ratelimit.login_limiter, "check", refuse)
        refused = await client.post("/api/auth/webauthn/login/options", json={})
        assert refused.status_code == 429
        assert refused.headers["Retry-After"] == "42"
        monkeypatch.undo()
        monkeypatch.setattr(
            webauthn, "CEREMONY_MAX_ENTRIES", {"login": 1, "register": 1, "second_factor": 1}
        )
        assert (await client.post("/api/auth/webauthn/login/options", json={})).status_code == 200
        from unittest import mock

        with mock.patch("app.routers.auth_webauthn.logger") as log:
            full = await client.post("/api/auth/webauthn/login/options", json={})
        assert full.status_code == 429
        log.warning.assert_called_once()


async def test_listing_passkeys_needs_a_session(public_address, sign_in_as):
    # A trusted network (or Basic) principal is not a session: no list.
    sign_in_as("member", credential="trusted_network")
    async with make_client(PUBLIC) as client:
        response = await client.get("/api/auth/webauthn/credentials")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "auth_passkey_not_applicable"
