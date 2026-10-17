"""CORS: same-origin by default, plus the origins listed in Settings (MSHL-5)."""

import pytest

from app import cors
from app.main import app
from app.repository import AppSettingsRepository

LISTED = "https://dashboard.example.org"
UNLISTED = "https://evil.example.com"


def _preflight_headers(origin: str, method: str = "PATCH") -> dict[str, str]:
    return {
        "Origin": origin,
        "Access-Control-Request-Method": method,
        "Access-Control-Request-Headers": "content-type",
    }


class TestDefaultSameOrigin:
    @pytest.mark.asyncio
    async def test_credentialed_cross_origin_request_gets_no_cors_headers(self, test_db, client):
        async with client:
            response = await client.get(
                "/api/settings", headers={"Origin": UNLISTED, "Cookie": "session=x"}
            )

        assert response.status_code == 200
        assert "access-control-allow-origin" not in response.headers
        assert "access-control-allow-credentials" not in response.headers

    @pytest.mark.asyncio
    async def test_preflight_from_any_origin_fails(self, test_db, client):
        async with client:
            response = await client.options("/api/settings", headers=_preflight_headers(UNLISTED))

        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers

    @pytest.mark.asyncio
    async def test_same_origin_request_still_works(self, test_db, client):
        """The browser sends Origin on a same-origin PATCH (Vite proxy, HA ingress)."""
        async with client:
            response = await client.patch(
                "/api/settings",
                json={"auto_resend_channel": True},
                headers={"Origin": "http://test"},
            )

        assert response.status_code == 200
        assert response.json()["auto_resend_channel"] is True

    def test_no_wildcard_cors_middleware_left(self):
        from starlette.middleware.cors import CORSMiddleware

        classes = [middleware.cls for middleware in app.user_middleware]
        assert cors.AllowedOriginsCORSMiddleware in classes
        assert CORSMiddleware not in classes


class TestListedOrigin:
    @pytest.mark.asyncio
    async def test_patch_lists_an_origin_and_takes_effect_at_once(self, test_db, client):
        async with client:
            saved = await client.patch(
                "/api/settings", json={"auth_allowed_origins": [" HTTPS://Dashboard.Example.org/ "]}
            )
            preflight = await client.options("/api/settings", headers=_preflight_headers(LISTED))
            simple = await client.get("/api/settings", headers={"Origin": LISTED})
            other = await client.get("/api/settings", headers={"Origin": UNLISTED})
            other_preflight = await client.options(
                "/api/settings", headers=_preflight_headers(UNLISTED)
            )

        assert saved.status_code == 200
        assert saved.json()["auth_allowed_origins"] == [LISTED]

        assert preflight.status_code == 200
        assert preflight.headers["access-control-allow-origin"] == LISTED
        assert preflight.headers["access-control-allow-credentials"] == "true"
        assert "PATCH" in preflight.headers["access-control-allow-methods"]

        assert simple.headers["access-control-allow-origin"] == LISTED
        assert simple.headers["access-control-allow-credentials"] == "true"

        assert "access-control-allow-origin" not in other.headers
        assert other_preflight.status_code == 400

    @pytest.mark.asyncio
    async def test_clearing_the_list_goes_back_to_same_origin(self, test_db, client):
        await AppSettingsRepository.update(auth_allowed_origins=[LISTED])
        async with client:
            await client.patch("/api/settings", json={"auth_allowed_origins": []})
            response = await client.get("/api/settings", headers={"Origin": LISTED})

        assert cors.current() == ()
        assert "access-control-allow-origin" not in response.headers

    @pytest.mark.asyncio
    async def test_unlisted_request_header_is_refused(self, test_db, client):
        await AppSettingsRepository.update(auth_allowed_origins=[LISTED])
        headers = _preflight_headers(LISTED) | {"Access-Control-Request-Headers": "x-custom"}
        async with client:
            response = await client.options("/api/settings", headers=headers)

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_load_reads_the_stored_list(self, test_db):
        await AppSettingsRepository.update(auth_allowed_origins=[LISTED])
        cors.reset()

        assert await cors.load() == (LISTED,)

    @pytest.mark.asyncio
    async def test_default_is_empty(self, test_db):
        assert (await AppSettingsRepository.get()).auth_allowed_origins == []


class TestValidation:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://example.org", "https://example.org"),
            ("HTTPS://Example.ORG/", "https://example.org"),
            ("https://example.org:443", "https://example.org"),
            ("http://example.org:80", "http://example.org"),
            ("http://192.168.1.20:8000", "http://192.168.1.20:8000"),
            ("http://[::1]:5173", "http://[::1]:5173"),
            ("http://meshloom.local:8000", "http://meshloom.local:8000"),
            ("https://exämple.org", "https://xn--exmple-cua.org"),
        ],
    )
    def test_normalizes_to_the_browser_origin_form(self, raw, expected):
        assert cors.normalize_origin(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "*",
            "https://*.example.org",
            "null",
            "example.org",
            "ftp://example.org",
            "https://example.org/path",
            "https://example.org?x=1",
            "https://example.org#top",
            "https://user:pass@example.org",
            "https://" + "a" * 260 + ".org",
        ],
    )
    def test_refuses_what_is_not_an_origin(self, raw):
        with pytest.raises(ValueError):
            cors.normalize_origin(raw)

    def test_drops_blanks_and_duplicates(self):
        assert cors.normalize_origins(["", " ", LISTED, LISTED + "/", "http://a.lan"]) == [
            LISTED,
            "http://a.lan",
        ]

    def test_caps_the_list(self):
        many = [f"http://host{i}.lan" for i in range(cors.MAX_ALLOWED_ORIGINS + 1)]
        with pytest.raises(ValueError):
            cors.normalize_origins(many)

    @pytest.mark.asyncio
    async def test_patch_refuses_a_wildcard_and_keeps_the_list(self, test_db, client):
        await AppSettingsRepository.update(auth_allowed_origins=[LISTED])
        async with client:
            response = await client.patch("/api/settings", json={"auth_allowed_origins": ["*"]})

        assert response.status_code == 422
        assert response.json()["detail"][0]["loc"] == ["body", "auth_allowed_origins"]
        assert (await AppSettingsRepository.get()).auth_allowed_origins == [LISTED]
