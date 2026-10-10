"""Update badge: GitHub-first release check, Community mirror, SemVer, GET /api/updates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services.meshloom_community import (
    DEFAULT_API_BASE,
    CommunityEffective,
    fetch_meshloom_latest,
)
from app.services.oss_updates import (
    fetch_github_latest as github_latest_original,
)
from app.services.oss_updates import (
    fetch_latest_release,
    get_update_status,
    is_newer_release,
    is_unknown_local_version,
    refresh_oss_update_cache,
    release_from_redirect,
    reset_oss_update_cache,
)
from app.version_info import AppBuildInfo

_STATS_PAYLOAD = {
    "version": "9.9.9",
    "tag": "v9.9.9",
    "released_at": "2026-01-01T00:00:00Z",
    "html_url": "https://github.com/TwinRocket/meshloom/releases/tag/v9.9.9",
}

_OPTED_OUT = CommunityEffective(
    enabled=False,
    locked=False,
    iata="",
    broker_host="mqtt.meshloom.app",
    api_base=DEFAULT_API_BASE,
    api_audience="api.meshloom.app",
    mqtt_audience="mqtt.meshloom.app",
)


def _build(version: str) -> AppBuildInfo:
    return AppBuildInfo(
        version=version, version_source="test", commit_hash=None, commit_source=None
    )


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_oss_update_cache()
    yield
    reset_oss_update_cache()


class TestFetchMeshloomLatest:
    @pytest.mark.asyncio
    async def test_opted_out_still_fetches_without_jwt(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _STATS_PAYLOAD
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "app.services.meshloom_community.get_community_effective",
                new=AsyncMock(return_value=_OPTED_OUT),
            ),
            patch(
                "app.services.meshloom_community.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch("app.services.meshloom_community.stats_request", new=AsyncMock()) as stats_req,
            patch("app.services.meshloom_community.stats_json", new=AsyncMock()) as stats_json,
        ):
            payload = await fetch_meshloom_latest()

        assert payload == _STATS_PAYLOAD
        stats_req.assert_not_called()
        stats_json.assert_not_called()
        mock_client.get.assert_awaited_once_with(f"{DEFAULT_API_BASE}/v1/meshloom/latest")
        assert mock_client.get.await_args.kwargs.get("headers") in (None, {})


class TestSemVerAndCache:
    def test_null_latest_is_not_available(self):
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.2.3"),
        ):
            status = get_update_status()
        assert status["latest"] is None
        assert status["update_available"] is False
        assert status["current"] == "1.2.3"
        assert status["html_url"] is None

    def test_local_0_0_0_never_badges(self):
        import app.services.oss_updates as oss_updates

        assert is_unknown_local_version("0.0.0") is True
        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("0.0.0"),
        ):
            status = get_update_status()
        assert status["latest"] == "9.9.9"
        assert status["update_available"] is False

    def test_mocked_stats_payload_marks_update(self):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.0.0"),
        ):
            status = get_update_status()
        assert status["current"] == "1.0.0"
        assert status["latest"] == "9.9.9"
        assert status["update_available"] is True
        assert status["html_url"] == _STATS_PAYLOAD["html_url"]

    def test_env_latest_override_wins_over_catalogue(self, monkeypatch: pytest.MonkeyPatch):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        monkeypatch.setenv("MESHLOOM_UPDATE_LATEST", "9.9.9")
        monkeypatch.setenv("MESHLOOM_UPDATE_HTML_URL", "https://example.invalid/private")
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.0.0"),
        ):
            status = get_update_status()
        assert status["latest"] == "9.9.9"
        assert status["update_available"] is True
        assert status["html_url"] == "https://example.invalid/private"

    def test_env_latest_override_keeps_catalogue_html(self, monkeypatch: pytest.MonkeyPatch):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        monkeypatch.setenv("MESHLOOM_UPDATE_LATEST", "8.8.8")
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.0.0"),
        ):
            status = get_update_status()
        assert status["latest"] == "8.8.8"
        assert status["html_url"] == _STATS_PAYLOAD["html_url"]

    def test_equal_or_older_latest_is_not_available(self):
        assert is_newer_release("1.2.3", "1.2.3") is False
        assert is_newer_release("1.2.2", "1.2.3") is False
        assert is_newer_release("1.3.0", "1.2.9") is True
        assert is_newer_release("v2.0.0", "1.9.9") is True

    @pytest.mark.asyncio
    async def test_refresh_stores_null_version_payload(self):
        null_payload = {
            "version": None,
            "tag": None,
            "released_at": None,
            "html_url": None,
        }
        with patch(
            "app.services.oss_updates.fetch_meshloom_latest",
            new=AsyncMock(return_value=null_payload),
        ):
            stored = await refresh_oss_update_cache()
        assert stored == {**null_payload, "source": "community"}
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.2.3"),
        ):
            status = get_update_status()
        assert status["latest"] is None
        assert status["update_available"] is False
        assert status["checked_at"] is not None


class TestUpdatesEndpoint:
    def test_get_updates_returns_cached_status(self, tmp_path, monkeypatch):
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = dict(_STATS_PAYLOAD)
        monkeypatch.setenv("MESHLOOM_UPDATE_JOB_PATH", str(tmp_path / "update-job.json"))
        from app.main import app

        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": _STATS_PAYLOAD["html_url"],
                },
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(
                    return_value=type(
                        "S",
                        (),
                        {
                            "auto_update": False,
                            "auto_update_window_start": "00:00",
                            "auto_update_window_end": "00:00",
                            "auto_update_weekdays": [0, 1, 2, 3, 4, 5, 6],
                        },
                    )()
                ),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/api/updates")
                health = client.get("/api/health").json()

        assert response.status_code == 200
        data = response.json()
        assert data["current"] == "1.0.0"
        assert data["latest"] == "9.9.9"
        assert data["update_available"] is True
        assert data["html_url"] == _STATS_PAYLOAD["html_url"]
        assert data["install_kind"] in {"package", "compose", "addon", "container", "source"}
        assert data["apply_supported"] is False or data["install_kind"] in {"package", "compose"}
        assert data["auto_update"] is False
        assert data["auto_update_window_start"] == "00:00"
        assert data["auto_update_window_end"] == "00:00"
        assert data["auto_update_weekdays"] == [0, 1, 2, 3, 4, 5, 6]
        assert data["checked_at"] is None
        assert isinstance(data["tz_name"], str) and data["tz_name"]
        assert data["next_auto_apply_at"] is None
        assert data["job"] == {
            "state": "idle",
            "phase": None,
            "percent": None,
            "error": None,
            "started_at": None,
        }
        assert "update_available" not in health

    def test_refresh_fetches_without_auto_apply(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MESHLOOM_UPDATE_JOB_PATH", str(tmp_path / "update-job.json"))
        from app.main import app

        with (
            patch(
                "app.routers.updates.refresh_oss_update_cache",
                new=AsyncMock(return_value=_STATS_PAYLOAD),
            ) as refresh,
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": _STATS_PAYLOAD["html_url"],
                    "checked_at": 1_700_000_000,
                },
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(
                    return_value=type(
                        "S",
                        (),
                        {
                            "auto_update": True,
                            "auto_update_window_start": "00:00",
                            "auto_update_window_end": "00:00",
                            "auto_update_weekdays": [0, 1, 2, 3, 4, 5, 6],
                        },
                    )()
                ),
            ),
            patch("app.routers.updates.start_apply", new=AsyncMock()) as start,
        ):
            with TestClient(app) as client:
                response = client.post("/api/updates/refresh")

        assert response.status_code == 200
        refresh.assert_awaited_once()
        start.assert_not_called()
        data = response.json()
        assert data["checked_at"] == 1_700_000_000
        assert data["auto_update"] is True
        assert data["latest"] == "9.9.9"


def _github(handler):
    real = httpx.AsyncClient

    def make(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    return make


_REPO = "https://github.com/TwinRocket/meshloom"


class TestGithubFirstUpdateCheck:
    def test_redirect_must_be_strict_release_tag(self):
        assert release_from_redirect(f"{_REPO}/releases/tag/4.18.0") == "4.18.0"
        for bad in (
            f"{_REPO}/releases/tag/v4.18.0",
            f"{_REPO}/releases/tag/4.18.0-rc1",
            f"{_REPO}/releases/tag/04.18.0",
            f"{_REPO}/releases",
            "https://evil.example/TwinRocket/meshloom/releases/tag/9.9.9",
            f"{_REPO}/releases/tag/4.18.0/../9.9.9",
            "",
        ):
            assert release_from_redirect(bad) is None, bad

    @pytest.mark.asyncio
    async def test_github_redirect_is_parsed_without_following(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(302, headers={"location": f"{_REPO}/releases/tag/4.19.0"})

        with patch("app.services.oss_updates.httpx.AsyncClient", _github(handler)):
            payload = await github_latest_original()
        assert seen == [f"{_REPO}/releases/latest"]
        assert payload == {
            "version": "4.19.0",
            "html_url": f"{_REPO}/releases/tag/4.19.0",
            "source": "github",
        }

    @pytest.mark.asyncio
    async def test_github_failure_returns_none(self):
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route", request=request)

        with patch("app.services.oss_updates.httpx.AsyncClient", _github(down)):
            assert await github_latest_original() is None
        with patch(
            "app.services.oss_updates.httpx.AsyncClient",
            _github(lambda _r: httpx.Response(200, text="<html>")),
        ):
            assert await github_latest_original() is None

    @pytest.mark.asyncio
    async def test_github_answer_never_touches_community(self):
        community = AsyncMock(return_value={"version": "1.0.0"})
        with (
            patch(
                "app.services.oss_updates.fetch_github_latest",
                new=AsyncMock(
                    return_value={"version": "4.19.0", "html_url": "u", "source": "github"}
                ),
            ),
            patch("app.services.oss_updates.fetch_meshloom_latest", new=community),
        ):
            payload = await fetch_latest_release()
        assert payload is not None and payload["version"] == "4.19.0"
        community.assert_not_called()

    @pytest.mark.asyncio
    async def test_community_is_the_fallback_mirror(self):
        with (
            patch("app.services.oss_updates.fetch_github_latest", new=AsyncMock(return_value=None)),
            patch(
                "app.services.oss_updates.fetch_meshloom_latest",
                new=AsyncMock(return_value={"version": "4.19.0", "html_url": "u"}),
            ),
            patch(
                "app.services.oss_updates.get_app_build_info",
                return_value=_build("4.18.0"),
            ),
        ):
            await refresh_oss_update_cache()
            status = get_update_status()
        assert status["latest"] == "4.19.0"
        assert status["update_available"] is True
        assert status["source"] == "community"

    @pytest.mark.asyncio
    async def test_community_down_does_not_hide_update(self):
        community = AsyncMock(return_value=None)
        with (
            patch(
                "app.services.oss_updates.fetch_github_latest",
                new=AsyncMock(
                    return_value={"version": "4.19.0", "html_url": "u", "source": "github"}
                ),
            ),
            patch("app.services.oss_updates.fetch_meshloom_latest", new=community),
            patch(
                "app.services.oss_updates.get_app_build_info",
                return_value=_build("4.18.0"),
            ),
        ):
            await refresh_oss_update_cache()
            status = get_update_status()
        assert status["update_available"] is True
        assert status["source"] == "github"
        community.assert_not_called()

    @pytest.mark.asyncio
    async def test_community_mirror_skipped_while_breaker_open(self):
        from app.services.meshloom_community import community_breaker

        breaker = community_breaker()
        for _ in range(breaker.threshold):
            breaker.record_failure()
        client = MagicMock()
        with patch("app.services.meshloom_community.httpx.AsyncClient", client):
            assert await fetch_meshloom_latest() is None
        client.assert_not_called()
