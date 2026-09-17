"""OSS update-badge: Stats catalogue fetch, SemVer compare, GET /api/updates."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.meshloom_community import (
    DEFAULT_API_BASE,
    CommunityEffective,
    fetch_meshloom_latest,
)
from app.services.oss_updates import (
    get_update_status,
    is_newer_release,
    is_unknown_local_version,
    refresh_oss_update_cache,
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
        assert stored == null_payload
        with patch(
            "app.services.oss_updates.get_app_build_info",
            return_value=_build("1.2.3"),
        ):
            status = get_update_status()
        assert status["latest"] is None
        assert status["update_available"] is False


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
                new=AsyncMock(return_value=type("S", (), {"auto_update": False})()),
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
        assert data["job"] == {
            "state": "idle",
            "phase": None,
            "percent": None,
            "error": None,
            "started_at": None,
        }
        assert "update_available" not in health
