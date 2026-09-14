from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend_static import (
    ASSET_CACHE_CONTROL,
    FRONTEND_BUILD_INSTRUCTIONS,
    INDEX_CACHE_CONTROL,
    STATIC_FILE_CACHE_CONTROL,
    register_first_available_frontend_static_routes,
    register_frontend_missing_fallback,
    register_frontend_static_routes,
)


def test_missing_dist_keeps_app_running(tmp_path):
    app = FastAPI()
    missing_dist = tmp_path / "frontend" / "dist"

    registered = register_frontend_static_routes(app, missing_dist)

    assert registered is False

    # Register the fallback like main.py does
    register_frontend_missing_fallback(app)

    with TestClient(app) as client:
        resp = client.get("/")
        assert resp.status_code == 404
        assert FRONTEND_BUILD_INSTRUCTIONS in resp.json()["detail"]


def test_missing_index_skips_frontend_routes(tmp_path):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)

    registered = register_frontend_static_routes(app, dist_dir)

    assert registered is False


def test_valid_dist_serves_static_and_spa_fallback(tmp_path):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    assets_dir = dist_dir / "assets"
    dist_dir.mkdir(parents=True)
    assets_dir.mkdir(parents=True)

    index_file = dist_dir / "index.html"
    index_file.write_text("<html><body>index page</body></html>")
    (dist_dir / "robots.txt").write_text("User-agent: *")
    (assets_dir / "app.js").write_text("console.log('ok');")

    registered = register_frontend_static_routes(app, dist_dir)
    assert registered is True

    with TestClient(app) as client:
        root_response = client.get("/")
        assert root_response.status_code == 200
        assert "index page" in root_response.text
        assert root_response.headers["cache-control"] == INDEX_CACHE_CONTROL

        manifest_response = client.get("/site.webmanifest")
        assert manifest_response.status_code == 200
        assert manifest_response.headers["content-type"].startswith("application/manifest+json")
        assert manifest_response.headers["cache-control"] == "no-store"
        manifest = manifest_response.json()
        assert manifest["start_url"] == "./"
        assert manifest["scope"] == "./"
        assert manifest["id"] == "./"
        assert manifest["display"] == "standalone"
        icon_srcs = {icon["src"] for icon in manifest["icons"]}
        assert "./web-app-manifest-192x192.png" in icon_srcs
        assert "./web-app-manifest-512x512.png" in icon_srcs
        # SVG icons cause inconsistent PWA icon rendering on iOS; the manifest
        # must be PNG-only.
        assert all(icon["type"] == "image/png" for icon in manifest["icons"])

        file_response = client.get("/robots.txt")
        assert file_response.status_code == 200
        assert file_response.text == "User-agent: *"
        assert file_response.headers["cache-control"] == STATIC_FILE_CACHE_CONTROL

        explicit_index_response = client.get("/index.html")
        assert explicit_index_response.status_code == 200
        assert "index page" in explicit_index_response.text
        assert explicit_index_response.headers["cache-control"] == INDEX_CACHE_CONTROL

        missing_response = client.get("/channel/some-route")
        assert missing_response.status_code == 200
        assert "index page" in missing_response.text
        assert missing_response.headers["cache-control"] == INDEX_CACHE_CONTROL

        missing_api_response = client.get("/api/not-a-real-endpoint")
        assert missing_api_response.status_code == 404
        assert missing_api_response.json() == {
            "detail": (
                "API endpoint not found. If you are seeing this in response to a frontend "
                "request, you may be running a newer frontend with an older backend or vice "
                "versa. A full update is suggested."
            )
        }

        asset_response = client.get("/assets/app.js")
        assert asset_response.status_code == 200
        assert "console.log('ok');" in asset_response.text
        assert asset_response.headers["cache-control"] == ASSET_CACHE_CONTROL


def test_webmanifest_uses_forwarded_origin_headers(tmp_path):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>index page</body></html>")

    registered = register_frontend_static_routes(app, dist_dir)
    assert registered is True

    with TestClient(app) as client:
        response = client.get(
            "/site.webmanifest",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "mesh.example.com:8443",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["start_url"] == "./"
        assert data["scope"] == "./"
        assert data["id"] == "./"


def test_webmanifest_includes_forwarded_prefix(tmp_path):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>index page</body></html>")

    registered = register_frontend_static_routes(app, dist_dir)
    assert registered is True

    with TestClient(app) as client:
        response = client.get(
            "/site.webmanifest",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "homeassistant.local:8123",
                "x-forwarded-prefix": "/api/hassio_ingress/abc123",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["start_url"] == "./"
        assert data["scope"] == "./"
        assert data["id"] == "./"
        icon_srcs = {icon["src"] for icon in data["icons"]}
        assert "./web-app-manifest-192x192.png" in icon_srcs
        assert "./web-app-manifest-512x512.png" in icon_srcs


def test_webmanifest_ignores_unsafe_forwarded_headers(tmp_path):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>index page</body></html>")

    registered = register_frontend_static_routes(app, dist_dir)
    assert registered is True

    with TestClient(app) as client:
        response = client.get(
            "/site.webmanifest",
            headers={
                "x-forwarded-proto": "javascript",
                "x-forwarded-host": "evil.example/path",
                "x-forwarded-prefix": "meshcore",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["start_url"] == "./"
        assert data["scope"] == "./"


def test_webmanifest_ignores_forwarded_credentials_and_query_prefix(tmp_path):
    app = FastAPI()
    dist_dir = tmp_path / "frontend" / "dist"
    dist_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>index page</body></html>")

    registered = register_frontend_static_routes(app, dist_dir)
    assert registered is True

    with TestClient(app) as client:
        response = client.get(
            "/site.webmanifest",
            headers={
                "x-forwarded-proto": "https",
                "x-forwarded-host": "user:pass@mesh.example.com",
                "x-forwarded-prefix": "/meshcore?next=https://evil.example",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["start_url"] == "./"


def test_first_available_prefers_dist_over_prebuilt(tmp_path):
    app = FastAPI()
    frontend_dir = tmp_path / "frontend"
    dist_dir = frontend_dir / "dist"
    prebuilt_dir = frontend_dir / "prebuilt"
    dist_dir.mkdir(parents=True)
    prebuilt_dir.mkdir(parents=True)
    (dist_dir / "index.html").write_text("<html><body>dist</body></html>")
    (prebuilt_dir / "index.html").write_text("<html><body>prebuilt</body></html>")

    selected = register_first_available_frontend_static_routes(app, [dist_dir, prebuilt_dir])

    assert selected == dist_dir.resolve()

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "dist" in response.text


def test_first_available_uses_prebuilt_when_dist_missing(tmp_path):
    app = FastAPI()
    frontend_dir = tmp_path / "frontend"
    dist_dir = frontend_dir / "dist"
    prebuilt_dir = frontend_dir / "prebuilt"
    prebuilt_dir.mkdir(parents=True)
    (prebuilt_dir / "index.html").write_text("<html><body>prebuilt</body></html>")

    selected = register_first_available_frontend_static_routes(app, [dist_dir, prebuilt_dir])

    assert selected == prebuilt_dir.resolve()

    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "prebuilt" in response.text


def _shell_app(tmp_path):
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html>\n<html lang="fr" class="dark">\n<head></head><body></body></html>\n',
        encoding="utf-8",
    )
    app = FastAPI()
    assert register_frontend_static_routes(app, dist) is True
    return app


def test_stored_theme_is_on_the_root_element_of_the_served_shell(tmp_path, monkeypatch):
    """The page must paint in the right theme on the first frame.

    A theme learned from a fetch paints the default first and then changes. That
    flash cannot be avoided with a local copy either: a device visiting for the
    first time has nothing stored, which is exactly the multi-device case.
    """
    from unittest.mock import AsyncMock

    from app.models import AppSettings, UiPreferences

    monkeypatch.setattr(
        "app.repository.AppSettingsRepository.get",
        AsyncMock(return_value=AppSettings(ui_preferences=UiPreferences(theme="light"))),
    )
    with TestClient(_shell_app(tmp_path)) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert '<html data-theme="light" lang="fr"' in resp.text
        # Never cached, so the injected value can never go stale.
        assert resp.headers["Cache-Control"] == INDEX_CACHE_CONTROL


def test_following_the_os_injects_nothing(tmp_path, monkeypatch):
    """An empty theme is answered by CSS, not by an attribute."""
    from unittest.mock import AsyncMock

    from app.models import AppSettings, UiPreferences

    monkeypatch.setattr(
        "app.repository.AppSettingsRepository.get",
        AsyncMock(return_value=AppSettings(ui_preferences=UiPreferences(theme=""))),
    )
    with TestClient(_shell_app(tmp_path)) as client:
        assert "data-theme" not in client.get("/").text


def test_a_theme_id_that_is_not_a_slug_never_reaches_the_attribute(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from app.models import AppSettings, UiPreferences

    monkeypatch.setattr(
        "app.repository.AppSettingsRepository.get",
        AsyncMock(
            return_value=AppSettings(ui_preferences=UiPreferences(theme='x" onload="alert(1)'))
        ),
    )
    with TestClient(_shell_app(tmp_path)) as client:
        body = client.get("/").text
        assert "onload" not in body
        assert "data-theme" not in body


def test_a_failing_settings_read_still_serves_the_page(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.repository.AppSettingsRepository.get",
        AsyncMock(side_effect=RuntimeError("database is gone")),
    )
    with TestClient(_shell_app(tmp_path)) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "<html" in resp.text
