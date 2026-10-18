"""The auth gate (app/auth/middleware.py): 401s, cookies by scheme, CSRF Origin rule."""

import pytest

from app import origin_policy
from app.auth import sessions
from app.repository.auth import AuthSessionRepository, AuthSettingsRepository
from tests.auth_helpers import (
    ADMIN,
    PASSWORD,
    basic,
    create_user,
    login,
    make_client,
    set_cookie_headers,
)

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

FOREIGN = "http://evil.example"


def _code(response) -> str:
    return response.json()["detail"]["code"]


async def test_every_api_route_needs_setup_while_no_account_exists():
    async with make_client() as client:
        for method, path in [
            ("GET", "/api/contacts"),
            ("GET", "/api/settings"),
            ("POST", "/api/messages/channel"),
            ("GET", "/api/debug"),
            ("GET", "/api/auth/me"),
        ]:
            response = await client.request(method, path)
            assert response.status_code == 401, path
            assert _code(response) == "auth_setup_required", path
            assert response.headers["cache-control"] == "no-store"


async def test_unauthenticated_requests_get_a_coded_401():
    await create_user()
    async with make_client() as client:
        response = await client.get("/api/contacts")
        assert response.status_code == 401
        assert response.json() == {
            "detail": {"code": "auth_required", "message": "Authentication required"}
        }
        assert "www-authenticate" not in response.headers

        client.cookies.set("meshloom_session", "not-a-session")
        response = await client.get("/api/contacts")
        assert response.status_code == 401
        assert _code(response) == "auth_invalid_credentials"


async def test_public_routes_answer_anonymously_and_minimally():
    await create_user()
    async with make_client() as client:
        health = await client.get("/api/health")
        assert health.status_code == 200
        assert health.json() == {"ok": True}

        session = await client.get("/api/auth/session")
        assert session.status_code == 200
        body = session.json()
        assert body == {
            "authenticated": False,
            "setup_required": False,
            "setup_code_required": False,
            "methods": {"password": True, "ingress": False},
            "auth_compat": {"basic": False},
            "secure": False,
        }
        assert "instance" not in body and "version" not in session.text
        assert session.headers["cache-control"] == "no-store"


async def test_frontend_paths_stay_public_and_api_docs_need_an_account():
    await create_user()
    async with make_client() as client:
        assert (await client.get("/")).status_code != 401
        for path in ("/docs", "/redoc", "/openapi.json"):
            response = await client.get(path)
            assert response.status_code == 401, path
            assert _code(response) == "auth_required", path
        await login(client)
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await client.get(path)).status_code == 200, path


async def test_login_sets_a_plain_cookie_over_http_and_unlocks_the_api():
    await create_user()
    async with make_client() as client:
        response = await login(client)
        [cookie] = set_cookie_headers(response)
        assert cookie.startswith("meshloom_session=")
        attributes = [part.strip().lower() for part in cookie.split(";")]
        assert "httponly" in attributes
        assert "samesite=lax" in attributes
        assert "path=/" in attributes
        assert "secure" not in attributes
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["authenticated"] is True
        assert body["user"]["username"] == ADMIN and body["user"]["role"] == "admin"
        assert body["via"] == "lan"
        assert body["instance"]["version"]

        assert (await client.get("/api/auth/me")).status_code == 200
        health = await client.get("/api/health")
        assert health.status_code == 200 and "radio_state" in health.json()


@pytest.mark.parametrize(
    ("base_url", "headers"),
    # X-Forwarded-Proto counts only from a trusted proxy: tests/test_proxy_headers.py.
    [("https://test", {}), ("https://test", {"X-Forwarded-Proto": "http"})],
)
async def test_https_requests_get_a_host_prefixed_secure_cookie(base_url, headers):
    await create_user()
    async with make_client(base_url) as client:
        response = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": PASSWORD}, headers=headers
        )
        assert response.status_code == 200
        [cookie] = set_cookie_headers(response)
        assert cookie.startswith("__Host-meshloom_session=")
        attributes = [part.strip().lower() for part in cookie.split(";")]
        assert "secure" in attributes and "path=/" in attributes
        assert not any(part.startswith("domain") for part in attributes)
        token = cookie.split(";", 1)[0].split("=", 1)[1]

    # Over https only the __Host- name is read: a plain cookie is ignored.
    async with make_client("https://test") as client:
        client.cookies.set("meshloom_session", token)
        assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    async with make_client("https://test") as client:
        client.cookies.set("__Host-meshloom_session", token)
        assert (await client.get("/api/auth/me", headers=headers)).status_code == 200


async def test_no_password_session_under_home_assistant_ingress(monkeypatch):
    """Ingress identities: tests/test_auth_ingress.py."""
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    await create_user()
    async with make_client(client=("172.30.32.2", 40000)) as client:
        response = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": PASSWORD}
        )
        assert response.status_code == 403
        assert _code(response) == "auth_ingress_only"
        assert set_cookie_headers(response) == []
        assert await AuthSessionRepository.list_for_user(1) == []
        logout = await client.post("/api/auth/logout")
        assert set_cookie_headers(logout) == []
        # A cookie sent through ingress anyway is never read.
        client.cookies.set("meshloom_session", "anything")
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_supervisor_address_is_not_ingress_outside_the_add_on(monkeypatch):
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "compose")
    await create_user()
    async with make_client(client=("172.30.32.2", 40000)) as client:
        response = await login(client)
    assert set_cookie_headers(response) != []
    assert response.json()["via"] == "lan"


async def test_unsafe_request_with_cookie_and_foreign_origin_is_refused():
    await create_user()
    async with make_client() as client:
        await login(client)
        refused = await client.patch(
            "/api/auth/me", json={"display_name": "x"}, headers={"Origin": FOREIGN}
        )
        assert refused.status_code == 403
        assert _code(refused) == "auth_origin_rejected"

        for headers in (
            {"Origin": "null"},
            {"Sec-Fetch-Site": "cross-site"},
            {"Sec-Fetch-Site": "same-site", "Origin": "http://other.test"},
        ):
            response = await client.patch("/api/auth/me", json={}, headers=headers)
            assert response.status_code == 403, headers

        for headers in (
            {"Origin": "http://test"},
            {"Sec-Fetch-Site": "same-origin", "Origin": FOREIGN},
            {"Sec-Fetch-Site": "none"},
            {},  # not a browser: scripts send neither header
            {
                "Origin": FOREIGN.replace("evil.example", "proxy.example"),
                "X-Forwarded-Host": "proxy.example",
            },
        ):
            response = await client.patch("/api/auth/me", json={}, headers=headers)
            assert response.status_code == 200, headers

        # GET is never blocked.
        assert (await client.get("/api/auth/me", headers={"Origin": FOREIGN})).status_code == 200


async def test_allowed_origins_pass_the_csrf_rule():
    await create_user()
    origin_policy._current = ("http://dashboard.example",)
    async with make_client() as client:
        await login(client)
        response = await client.patch(
            "/api/auth/me", json={}, headers={"Origin": "http://dashboard.example"}
        )
    assert response.status_code == 200


async def test_only_a_resolved_bearer_token_is_exempt_from_the_origin_rule(monkeypatch):
    from app.auth import middleware
    from app.auth.principal import Principal

    user = await create_user()
    async with make_client() as client:
        # No token is valid yet: an unknown bearer gets no exemption.
        for path in ("/api/auth/logout", "/api/auth/me"):
            response = await client.request(
                "POST" if path.endswith("logout") else "PATCH",
                path,
                json={},
                headers={"Origin": FOREIGN, "Authorization": "Bearer abc"},
            )
            assert response.status_code == 403, path
            assert _code(response) == "auth_origin_rejected"
        anonymous = await client.patch(
            "/api/auth/me", json={}, headers={"Authorization": "Bearer abc"}
        )
        assert anonymous.status_code == 401
        assert _code(anonymous) == "auth_invalid_credentials"
        assert "www-authenticate" not in anonymous.headers

        async def resolve_token(token, via, ip):
            if token == "good":
                return Principal(user=user, via=via, credential="token", token_id="t1")
            return None

        monkeypatch.setattr(middleware, "resolve_bearer_token", resolve_token)
        passed = await client.patch(
            "/api/auth/me", json={}, headers={"Origin": FOREIGN, "Authorization": "Bearer good"}
        )
        assert passed.status_code == 200


async def test_login_itself_follows_the_origin_rule():
    await create_user()
    async with make_client() as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": ADMIN, "password": PASSWORD},
            headers={"Origin": FOREIGN},
        )
    assert response.status_code == 403
    assert _code(response) == "auth_origin_rejected"


async def test_basic_credentials_are_checked_against_accounts_and_cached(monkeypatch):
    from app.auth.providers import password as provider

    await create_user()
    calls = 0
    real_verify = provider.verify_password

    def counting_verify(*args):
        nonlocal calls
        calls += 1
        return real_verify(*args)

    monkeypatch.setattr(provider, "verify_password", counting_verify)
    async with make_client() as client:
        for _ in range(100):
            response = await client.get("/api/auth/me", headers=basic(ADMIN, PASSWORD))
            assert response.status_code == 200
        assert calls == 1

        wrong = await client.get("/api/auth/me", headers=basic(ADMIN, "wrong password!"))
        assert wrong.status_code == 401
        assert _code(wrong) == "auth_invalid_credentials"

        # Basic is ambient (a browser replays it): same Origin rule as the cookie.
        response = await client.patch(
            "/api/auth/me", json={}, headers={**basic(ADMIN, PASSWORD), "Origin": FOREIGN}
        )
        assert response.status_code == 403


async def test_basic_challenge_only_for_instances_that_came_from_basic():
    await create_user()
    async with make_client() as client:
        assert "www-authenticate" not in (await client.get("/api/contacts")).headers
        await AuthSettingsRepository.update(basic_imported=True)
        response = await client.get("/api/contacts")
        assert response.headers["www-authenticate"] == 'Basic realm="Meshloom", charset="UTF-8"'
        session = await client.get("/api/auth/session")
        assert session.json()["auth_compat"] == {"basic": True}


async def test_disabled_account_loses_its_session():
    from app.repository.auth import UserRepository

    user = await create_user()
    async with make_client() as client:
        await login(client)
        await UserRepository.update(user.id, disabled=True)
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_session_expiry_slides_and_the_cookie_is_sent_again(test_db):
    await create_user()
    async with make_client() as client:
        await login(client)
        [session] = await AuthSessionRepository.list_for_user(1)
        async with test_db.tx() as conn:
            await conn.execute(
                "UPDATE sessions SET last_seen_at = last_seen_at - 120,"
                " expires_at = expires_at - 1000 WHERE id = ?",
                (session.id,),
            )
        response = await client.get("/api/contacts")
        assert response.status_code == 200
        [cookie] = set_cookie_headers(response)
        assert cookie.startswith(f"{sessions.PLAIN_COOKIE_NAME}=")
        [after] = await AuthSessionRepository.list_for_user(1)
        assert after.expires_at > session.expires_at - 1000
        # Within the minute nothing is written and no cookie is sent again.
        assert set_cookie_headers(await client.get("/api/contacts")) == []


async def test_database_error_fails_closed(monkeypatch):
    await create_user()

    async def broken(*args, **kwargs):
        raise RuntimeError("database gone")

    monkeypatch.setattr(sessions, "resolve_session", broken)
    async with make_client() as client:
        client.cookies.set("meshloom_session", "x" * 43)
        response = await client.get("/api/contacts")
    assert response.status_code == 401
