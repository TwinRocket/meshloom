"""Meshloom-only apply: job files, 409s, settings toggle, helper start."""

from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.services.update_apply import (
    APPLY_TIMEOUT_ERROR,
    APPLYING_TTL_SECONDS,
    UpdateApplyBusy,
    expire_stale_applying_job,
    job_is_applying,
    job_path,
    last_attempt_recent,
    public_job,
    public_job_for_client,
    read_job,
    request_path,
    start_apply,
    start_package_helper,
    versions_match,
    write_job,
)


@pytest.fixture
def job_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MESHLOOM_UPDATE_JOB_PATH", str(tmp_path / "update-job.json"))
    return tmp_path


def test_package_request_path_is_canonical() -> None:
    from app.services.update_apply import PACKAGE_REQUEST_PATH

    assert Path("/var/lib/meshloom/request-update") == PACKAGE_REQUEST_PATH


def test_job_path_override_and_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MESHLOOM_UPDATE_JOB_PATH", str(tmp_path / "update-job.json"))
    assert job_path() == tmp_path / "update-job.json"
    assert request_path() == tmp_path / "request-update"
    monkeypatch.delenv("MESHLOOM_UPDATE_JOB_PATH", raising=False)
    monkeypatch.setenv("MESHCORE_DATABASE_PATH", str(tmp_path / "meshcore.db"))
    assert job_path() == tmp_path / "update-job.json"
    assert request_path() == tmp_path / "request-update"


def test_write_job_replaces_unwritable_file(job_dir: Path) -> None:
    path = job_path()
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o444)
    written = write_job(state="failed", phase="preparing", error="retry")
    assert written["state"] == "failed"
    assert read_job()["error"] == "retry"


def test_write_and_read_job_round_trip(job_dir: Path) -> None:
    written = write_job(state="applying", phase="downloading", percent=40)
    assert written["state"] == "applying"
    loaded = read_job()
    assert loaded["phase"] == "downloading"
    assert loaded["percent"] == 40
    assert job_is_applying(loaded)
    public = public_job(loaded)
    assert "last_attempt" not in public
    assert set(public) == {"state", "phase", "percent", "error", "started_at"}


def test_versions_match_ignores_leading_v() -> None:
    assert versions_match("v1.2.3", "1.2.3") is True
    assert versions_match("1.2.3", "1.2.4") is False
    assert versions_match("", "1.2.3") is False


def test_public_job_for_client_hides_failed_when_already_current(job_dir: Path) -> None:
    write_job(
        state="failed",
        phase="installing",
        error="See systemctl status meshloom-update.service",
        target="1.2.3",
    )
    hidden = public_job_for_client(current="1.2.3", latest="1.2.3")
    assert hidden["state"] == "succeeded"
    assert hidden["error"] is None
    assert hidden["phase"] == "done"
    assert read_job()["state"] == "failed"

    visible = public_job_for_client(current="1.2.2", latest="1.2.3")
    assert visible["state"] == "failed"
    assert visible["error"]


def test_last_attempt_recent_uses_backoff(job_dir: Path) -> None:
    write_job(state="failed", phase="installing", error="boom", last_attempt=1_000)
    assert last_attempt_recent(read_job(), now=1_000 + 60) is True
    assert last_attempt_recent(read_job(), now=1_000 + 7 * 3600) is False


@pytest.mark.asyncio
async def test_start_apply_package_starts_unit(job_dir: Path) -> None:
    with patch("app.services.update_apply.start_package_helper", new=AsyncMock()) as helper:
        job = await start_apply("package", target="9.9.9")
    helper.assert_awaited_once()
    assert job["state"] == "applying"
    assert job["phase"] == "preparing"
    assert job["target"] == "9.9.9"


@pytest.mark.asyncio
async def test_start_package_helper_writes_canonical_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = tmp_path / "meshloom-update.path"
    unit.write_text("[Path]\n", encoding="utf-8")
    request = tmp_path / "canonical" / "request-update"
    request.parent.mkdir()
    request.write_text("leftover\n", encoding="utf-8")
    monkeypatch.setattr("app.services.update_apply.UPDATE_PATH_UNIT", unit)
    monkeypatch.setattr("app.services.update_apply.PACKAGE_REQUEST_PATH", request)
    with (
        patch(
            "app.services.update_apply.package_path_watcher_active",
            new=AsyncMock(return_value=True),
        ),
        patch("asyncio.create_subprocess_exec") as spawn,
    ):
        await start_package_helper()
    spawn.assert_not_called()
    assert request.read_text(encoding="utf-8") == "1\n"


@pytest.mark.asyncio
async def test_start_apply_package_does_not_use_job_dir_request(
    job_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = tmp_path / "meshloom-update.path"
    unit.write_text("[Path]\n", encoding="utf-8")
    request = tmp_path / "canonical-request"
    monkeypatch.setattr("app.services.update_apply.UPDATE_PATH_UNIT", unit)
    monkeypatch.setattr("app.services.update_apply.PACKAGE_REQUEST_PATH", request)
    with patch(
        "app.services.update_apply.package_path_watcher_active",
        new=AsyncMock(return_value=True),
    ):
        job = await start_apply("package", target="1.2.3")
    assert request.read_text(encoding="utf-8") == "1\n"
    assert not (job_dir / "request-update").exists()
    assert job["state"] == "applying"
    assert job["error"] is None


@pytest.mark.asyncio
async def test_start_package_helper_falls_back_to_no_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.services.update_apply.UPDATE_PATH_UNIT", tmp_path / "missing.path")
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as spawn:
        await start_package_helper()
    spawn.assert_awaited_once()
    assert spawn.await_args.args[:4] == (
        "systemctl",
        "start",
        "--no-block",
        "meshloom-update.service",
    )


@pytest.mark.asyncio
async def test_start_package_helper_falls_back_when_watcher_inactive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = tmp_path / "meshloom-update.path"
    unit.write_text("[Path]\n", encoding="utf-8")
    request = tmp_path / "request-update"
    monkeypatch.setattr("app.services.update_apply.UPDATE_PATH_UNIT", unit)
    monkeypatch.setattr("app.services.update_apply.PACKAGE_REQUEST_PATH", request)
    proc = AsyncMock()
    proc.returncode = 0
    proc.communicate = AsyncMock(return_value=(b"", b""))
    with (
        patch(
            "app.services.update_apply.package_path_watcher_active",
            new=AsyncMock(return_value=False),
        ),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)) as spawn,
    ):
        await start_package_helper()
    spawn.assert_awaited_once()
    assert spawn.await_args.args[:4] == (
        "systemctl",
        "start",
        "--no-block",
        "meshloom-update.service",
    )
    assert not request.exists()


@pytest.mark.asyncio
async def test_start_apply_does_not_overwrite_job_after_enqueue(job_dir: Path) -> None:
    async def helper() -> None:
        write_job(state="applying", phase="downloading", percent=50, target="1.2.3")

    with patch("app.services.update_apply.start_package_helper", new=helper):
        job = await start_apply("package", target="1.2.3")
    assert job["state"] == "applying"
    assert job["phase"] == "downloading"
    assert job["percent"] == 50
    assert job.get("error") is None


@pytest.mark.asyncio
async def test_start_apply_compose_writes_request(job_dir: Path) -> None:
    job = await start_apply("compose")
    assert job["state"] == "applying"
    assert (job_dir / "request-update").read_text(encoding="utf-8").strip() == "1"


@pytest.mark.asyncio
async def test_start_apply_rejects_second_job(job_dir: Path) -> None:
    write_job(state="applying", phase="installing")
    with pytest.raises(UpdateApplyBusy):
        await start_apply("compose")


def test_expire_stale_job_survives_unwritable_file(
    job_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_job(
        state="applying",
        phase="installing",
        started_at=1,
        last_attempt=1,
        target="9.9.9",
    )

    def boom(**kwargs: object) -> dict[str, object]:
        raise PermissionError("denied")

    monkeypatch.setattr("app.services.update_apply.write_job", boom)
    expired = expire_stale_applying_job(now=1 + APPLYING_TTL_SECONDS + 5)
    assert expired["state"] == "failed"
    assert expired["error"] == APPLY_TIMEOUT_ERROR
    assert expired["target"] == "9.9.9"
    assert read_job()["state"] == "applying"


def test_stale_applying_job_expires(job_dir: Path) -> None:
    write_job(
        state="applying",
        phase="installing",
        started_at=1,
        last_attempt=1,
        target="9.9.9",
    )
    expired = expire_stale_applying_job(now=1 + APPLYING_TTL_SECONDS + 5)
    assert expired["state"] == "failed"
    assert expired["error"] == APPLY_TIMEOUT_ERROR
    assert expired["target"] == "9.9.9"


def test_fresh_applying_job_is_not_expired(job_dir: Path) -> None:
    write_job(state="applying", phase="installing")
    assert expire_stale_applying_job()["state"] == "applying"


@pytest.mark.asyncio
async def test_start_apply_after_stale_job(job_dir: Path) -> None:
    write_job(state="applying", phase="installing", started_at=1)
    with patch("app.services.update_apply.start_package_helper", new=AsyncMock()):
        job = await start_apply("package", target="1.2.3")
    assert job["state"] == "applying"
    assert job["target"] == "1.2.3"


@pytest.mark.asyncio
async def test_start_apply_unsupported_kind_fails_job(job_dir: Path) -> None:
    job = await start_apply("addon")
    assert job["state"] == "failed"
    assert "not supported" in (job["error"] or "")


def _settings(
    auto_update: bool = False,
    *,
    window_start: str = "00:00",
    window_end: str = "00:00",
    weekdays: list[int] | None = None,
):
    return SimpleNamespace(
        auto_update=auto_update,
        auto_update_window_start=window_start,
        auto_update_window_end=window_end,
        auto_update_weekdays=list(range(7)) if weekdays is None else weekdays,
    )


class TestUpdatesRouter:
    def test_get_updates_includes_apply_fields(self, job_dir: Path) -> None:
        from app.main import app

        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": "https://example.invalid/r",
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("source", False),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(return_value=_settings()),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/api/updates")

        assert response.status_code == 200
        data = response.json()
        assert data["install_kind"] == "source"
        assert data["apply_supported"] is False
        assert data["auto_update"] is False
        assert data["auto_update_window_start"] == "00:00"
        assert data["auto_update_window_end"] == "00:00"
        assert data["auto_update_weekdays"] == [0, 1, 2, 3, 4, 5, 6]
        assert data["checked_at"] is None
        assert isinstance(data["tz_name"], str) and data["tz_name"]
        assert data["next_auto_apply_at"] is None
        assert data["job"]["state"] == "idle"
        assert "last_attempt" not in data["job"]

    def test_get_updates_hides_failed_job_when_current_matches(self, job_dir: Path) -> None:
        from app.main import app

        write_job(
            state="failed",
            phase="installing",
            error="See systemctl status meshloom-update.service",
            target="9.9.9",
        )
        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "9.9.9",
                    "latest": "9.9.9",
                    "update_available": False,
                    "html_url": None,
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("package", True),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(return_value=_settings()),
            ),
        ):
            with TestClient(app) as client:
                response = client.get("/api/updates")

        assert response.status_code == 200
        job = response.json()["job"]
        assert job["state"] == "succeeded"
        assert job["error"] is None
        assert read_job()["state"] == "failed"

    def test_apply_unsupported_is_409(self, job_dir: Path) -> None:
        from app.main import app

        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": None,
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("addon", False),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(return_value=_settings()),
            ),
        ):
            with TestClient(app) as client:
                response = client.post("/api/updates/apply")

        assert response.status_code == 409
        assert response.json()["detail"] == "apply_not_supported"

    def test_apply_in_progress_is_409(self, job_dir: Path) -> None:
        from app.main import app

        write_job(state="applying", phase="downloading")
        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": None,
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("package", True),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(return_value=_settings()),
            ),
        ):
            with TestClient(app) as client:
                response = client.post("/api/updates/apply")

        assert response.status_code == 409
        assert response.json()["detail"] == "apply_in_progress"

    def test_apply_without_update_is_409(self, job_dir: Path) -> None:
        from app.main import app

        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "1.0.0",
                    "update_available": False,
                    "html_url": None,
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("package", True),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(return_value=_settings()),
            ),
            patch("app.routers.updates.start_apply", new=AsyncMock()) as start,
        ):
            with TestClient(app) as client:
                response = client.post("/api/updates/apply")

        assert response.status_code == 409
        assert response.json()["detail"] == "update_not_available"
        start.assert_not_called()

    def test_apply_starts_helper(self, job_dir: Path) -> None:
        from app.main import app

        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": None,
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("package", True),
            ),
            patch(
                "app.routers.updates.start_apply",
                new=AsyncMock(
                    return_value={
                        "state": "applying",
                        "phase": "preparing",
                        "percent": 0,
                        "error": None,
                        "started_at": 1,
                    }
                ),
            ) as start,
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(return_value=_settings()),
            ),
        ):
            with TestClient(app) as client:
                response = client.post("/api/updates/apply")

        assert response.status_code == 202
        start.assert_awaited_once()
        assert start.await_args.args[0] == "package"

    def test_patch_auto_update(self, job_dir: Path) -> None:
        from app.main import app

        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": None,
                    "update_available": False,
                    "html_url": None,
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("package", True),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(return_value=_settings(True)),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.update",
                new=AsyncMock(),
            ),
        ):
            with TestClient(app) as client:
                response = client.patch("/api/updates/settings", json={"auto_update": True})

        assert response.status_code == 200
        assert response.json()["auto_update"] is True

    def test_patch_window_fields_partial(self, job_dir: Path) -> None:
        from app.main import app

        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": None,
                    "update_available": False,
                    "html_url": None,
                    "checked_at": None,
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("package", True),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(
                    return_value=_settings(
                        True, window_start="22:00", window_end="06:00", weekdays=[0, 1, 2, 3, 4]
                    )
                ),
            ),
            patch(
                "app.routers.updates.AppSettingsRepository.update",
                new=AsyncMock(),
            ) as update,
        ):
            with TestClient(app) as client:
                response = client.patch(
                    "/api/updates/settings",
                    json={
                        "auto_update_window_start": "22:00",
                        "auto_update_window_end": "06:00",
                        "auto_update_weekdays": [0, 1, 2, 3, 4],
                    },
                )

        assert response.status_code == 200
        update.assert_awaited_once()
        kwargs = update.await_args.kwargs
        assert kwargs["auto_update_window_start"] == "22:00"
        assert kwargs["auto_update_window_end"] == "06:00"
        assert kwargs["auto_update_weekdays"] == [0, 1, 2, 3, 4]
        assert "auto_update" not in kwargs
        data = response.json()
        assert data["auto_update_window_start"] == "22:00"
        assert data["auto_update_weekdays"] == [0, 1, 2, 3, 4]


def test_settings_patch_model_excludes_auto_update() -> None:
    from app.routers.settings import AppSettingsUpdate

    assert "auto_update" not in AppSettingsUpdate.model_fields
    assert "auto_update_window_start" not in AppSettingsUpdate.model_fields
    assert "auto_update_window_end" not in AppSettingsUpdate.model_fields
    assert "auto_update_weekdays" not in AppSettingsUpdate.model_fields
    assert "last_notified_update_version" not in AppSettingsUpdate.model_fields


class TestAutoApply:
    @pytest.mark.asyncio
    async def test_starts_when_enabled(
        self, test_db, job_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.repository import AppSettingsRepository
        from app.services.oss_updates import _maybe_auto_apply
        from app.version_info import AppBuildInfo

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
        helper = job_dir / "apply-update"
        helper.write_text("ok", encoding="utf-8")
        monkeypatch.setattr("app.services.install_kind.APPLY_UPDATE_BIN", helper)
        await AppSettingsRepository.update(auto_update=True)
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {
            "version": "9.9.9",
            "html_url": "https://example.invalid/r",
        }
        with (
            patch(
                "app.services.oss_updates.get_app_build_info",
                return_value=AppBuildInfo(
                    version="1.0.0", version_source="test", commit_hash=None, commit_source=None
                ),
            ),
            patch("app.services.update_apply.start_package_helper", new=AsyncMock()),
        ):
            await _maybe_auto_apply()
        assert read_job()["state"] == "applying"

    @pytest.mark.asyncio
    async def test_backs_off_after_failure(
        self, test_db, job_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.repository import AppSettingsRepository
        from app.services.oss_updates import _maybe_auto_apply
        from app.version_info import AppBuildInfo

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
        helper = job_dir / "apply-update"
        helper.write_text("ok", encoding="utf-8")
        monkeypatch.setattr("app.services.install_kind.APPLY_UPDATE_BIN", helper)
        await AppSettingsRepository.update(auto_update=True)
        write_job(state="failed", error="boom", last_attempt=int(time.time()))
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "9.9.9", "html_url": None}
        with (
            patch(
                "app.services.oss_updates.get_app_build_info",
                return_value=AppBuildInfo(
                    version="1.0.0", version_source="test", commit_hash=None, commit_source=None
                ),
            ),
            patch("app.services.update_apply.start_apply", new=AsyncMock()) as start,
        ):
            await _maybe_auto_apply()
        start.assert_not_called()

    @pytest.mark.asyncio
    async def test_backs_off_recent_success_without_target(
        self, test_db, job_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.repository import AppSettingsRepository
        from app.services.oss_updates import _maybe_auto_apply
        from app.version_info import AppBuildInfo

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
        helper = job_dir / "apply-update"
        helper.write_text("ok", encoding="utf-8")
        monkeypatch.setattr("app.services.install_kind.APPLY_UPDATE_BIN", helper)
        await AppSettingsRepository.update(auto_update=True)
        write_job(state="succeeded", phase="done", last_attempt=int(time.time()))
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "9.9.9", "html_url": None}
        with (
            patch(
                "app.services.oss_updates.get_app_build_info",
                return_value=AppBuildInfo(
                    version="1.0.0", version_source="test", commit_hash=None, commit_source=None
                ),
            ),
            patch("app.services.update_apply.start_apply", new=AsyncMock()) as start,
        ):
            await _maybe_auto_apply()
        start.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_outside_window(
        self, test_db, job_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime

        from app.repository import AppSettingsRepository
        from app.services.oss_updates import _maybe_auto_apply
        from app.version_info import AppBuildInfo

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
        helper = job_dir / "apply-update"
        helper.write_text("ok", encoding="utf-8")
        monkeypatch.setattr("app.services.install_kind.APPLY_UPDATE_BIN", helper)
        today = datetime.now().astimezone().weekday()
        await AppSettingsRepository.update(
            auto_update=True,
            auto_update_weekdays=[(today + 1) % 7],
        )
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {"version": "9.9.9", "html_url": None}
        with (
            patch(
                "app.services.oss_updates.get_app_build_info",
                return_value=AppBuildInfo(
                    version="1.0.0", version_source="test", commit_hash=None, commit_source=None
                ),
            ),
            patch("app.services.update_apply.start_apply", new=AsyncMock()) as start,
        ):
            await _maybe_auto_apply()
        start.assert_not_called()
        assert read_job()["state"] == "idle"

    @pytest.mark.asyncio
    async def test_applies_inside_window(
        self, test_db, job_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.repository import AppSettingsRepository
        from app.services.oss_updates import _maybe_auto_apply
        from app.version_info import AppBuildInfo

        monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
        helper = job_dir / "apply-update"
        helper.write_text("ok", encoding="utf-8")
        monkeypatch.setattr("app.services.install_kind.APPLY_UPDATE_BIN", helper)
        await AppSettingsRepository.update(
            auto_update=True,
            auto_update_window_start="00:00",
            auto_update_window_end="00:00",
            auto_update_weekdays=list(range(7)),
        )
        import app.services.oss_updates as oss_updates

        oss_updates._latest_payload = {
            "version": "9.9.9",
            "html_url": "https://example.invalid/r",
        }
        with (
            patch(
                "app.services.oss_updates.get_app_build_info",
                return_value=AppBuildInfo(
                    version="1.0.0", version_source="test", commit_hash=None, commit_source=None
                ),
            ),
            patch("app.services.update_apply.start_package_helper", new=AsyncMock()),
        ):
            await _maybe_auto_apply()
        assert read_job()["state"] == "applying"

    def test_manual_apply_ignores_window(self, job_dir: Path) -> None:
        from datetime import datetime

        from app.main import app

        today = datetime.now().astimezone().weekday()
        with (
            patch(
                "app.routers.updates.get_update_status",
                return_value={
                    "current": "1.0.0",
                    "latest": "9.9.9",
                    "update_available": True,
                    "html_url": None,
                    "checked_at": None,
                },
            ),
            patch(
                "app.routers.updates.detect_install_kind",
                return_value=("package", True),
            ),
            patch(
                "app.routers.updates.start_apply",
                new=AsyncMock(
                    return_value={
                        "state": "applying",
                        "phase": "preparing",
                        "percent": 0,
                        "error": None,
                        "started_at": 1,
                    }
                ),
            ) as start,
            patch(
                "app.routers.updates.AppSettingsRepository.get",
                new=AsyncMock(
                    return_value=_settings(True, weekdays=[(today + 1) % 7]),
                ),
            ),
        ):
            with TestClient(app) as client:
                response = client.post("/api/updates/apply")

        assert response.status_code == 202
        start.assert_awaited_once()


# ── 4.18 trust boundary: app attempt file + root status file ─────────────────


@pytest.fixture
def status_file(job_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    status_dir = job_dir / "update-status"
    status_dir.mkdir()
    path = status_dir / "status.json"
    monkeypatch.setenv("MESHLOOM_UPDATE_STATUS_PATH", str(path))
    return path


def _helper_status(path: Path, **fields: object) -> None:
    base = {
        "schema": 1,
        "state": "applying",
        "phase": "downloading",
        "percent": None,
        "error": None,
        "started_at": int(time.time()),
        "updated_at": int(time.time()),
        "version": "1.2.3",
    }
    base.update(fields)
    path.write_text(json.dumps(base), encoding="utf-8")


def test_write_job_goes_to_the_app_attempt_file(job_dir: Path) -> None:
    from app.services.update_apply import attempt_path

    write_job(state="applying", phase="preparing", target="1.2.3")
    assert attempt_path() == job_dir / "update-attempt.json"
    assert attempt_path().exists()
    assert not (job_dir / "update-job.json").exists()


def test_helper_status_wins_once_it_started_after_the_request(
    job_dir: Path, status_file: Path
) -> None:
    now = int(time.time())
    write_job(state="applying", phase="preparing", target="1.2.4", started_at=now, last_attempt=now)
    _helper_status(
        status_file, state="applying", phase="downloading", percent=40, started_at=now + 1
    )
    job = read_job()
    assert job["phase"] == "downloading"
    assert job["percent"] == 40
    assert job["target"] == "1.2.4"
    _helper_status(status_file, state="succeeded", phase="done", percent=100, started_at=now + 1)
    assert read_job()["state"] == "succeeded"


def test_stale_helper_status_does_not_hide_a_new_request(job_dir: Path, status_file: Path) -> None:
    now = int(time.time())
    _helper_status(status_file, state="succeeded", phase="done", started_at=now - 3600)
    write_job(state="applying", phase="preparing", started_at=now, last_attempt=now)
    job = read_job()
    assert job["state"] == "applying"
    assert job["phase"] == "preparing"


def test_app_side_failure_beats_helper_status(job_dir: Path, status_file: Path) -> None:
    now = int(time.time())
    _helper_status(status_file, state="applying", started_at=now)
    write_job(state="failed", error=APPLY_TIMEOUT_ERROR, started_at=now - 1)
    assert read_job()["state"] == "failed"


@pytest.mark.parametrize(
    "raw",
    [
        "not json",
        "[]",
        '{"state": "pwned"}',
        '{"state": "applying", "percent": "1; rm -rf /", "phase": "evil", "started_at": "x"}',
        '{"state": "failed", "error": ' + json.dumps("x" * 10_000) + "}",
        "{" + " " * (70 * 1024) + "}",
    ],
)
def test_hostile_status_json_is_sanitised(job_dir: Path, status_file: Path, raw: str) -> None:
    status_file.write_text(raw, encoding="utf-8")
    job = read_job()
    assert job["state"] in {"idle", "applying", "failed"}
    assert job["phase"] in {None, "preparing", "downloading", "installing", "restarting", "done"}
    assert job["percent"] is None or isinstance(job["percent"], int)
    assert job["started_at"] is None or isinstance(job["started_at"], int)
    assert job["error"] is None or len(job["error"]) <= 500


def test_legacy_job_file_is_read_only_fallback(job_dir: Path, status_file: Path) -> None:
    legacy = job_dir / "update-job.json"
    legacy.write_text(
        json.dumps({"state": "succeeded", "phase": "done", "started_at": 5, "target": "4.18.0"}),
        encoding="utf-8",
    )
    job = read_job()
    assert job["state"] == "succeeded"
    assert job["last_attempt"] == 5
    status_file.write_text(json.dumps({"state": "failed", "error": "new", "started_at": 9}))
    assert read_job()["error"] == "new"
    assert legacy.exists()


@pytest.mark.asyncio
async def test_compose_request_with_legacy_helper_drops_stale_target(job_dir: Path) -> None:
    legacy = job_dir / "update-job.json"
    legacy.write_text(json.dumps({"state": "succeeded", "target": "4.17.0"}), encoding="utf-8")
    await start_apply("compose", target="4.18.0")
    # The old root helper re-applies `target` from this file; without it,
    # it resolves the latest release itself.
    assert not legacy.exists()
    assert (job_dir / "request-update").read_text(encoding="utf-8") == "1\n"


@pytest.mark.asyncio
async def test_compose_request_with_secure_helper_keeps_files(
    job_dir: Path, status_file: Path
) -> None:
    legacy = job_dir / "update-job.json"
    legacy.write_text("{}", encoding="utf-8")
    await start_apply("compose", target="4.18.0")
    assert legacy.exists()
    assert (job_dir / "request-update").exists()


def test_legacy_compose_helper_flag(job_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.routers.updates import build_update_status

    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "compose")
    monkeypatch.setenv("MESHLOOM_UPDATE_HELPER", "compose")
    monkeypatch.delenv("MESHLOOM_UPDATE_STATUS_PATH", raising=False)
    settings = SimpleNamespace(
        auto_update=False,
        auto_update_window_start="00:00",
        auto_update_window_end="00:00",
        auto_update_weekdays=[0, 1, 2, 3, 4, 5, 6],
    )
    catalogue = {
        "current": "4.18.0",
        "latest": "4.18.0",
        "update_available": False,
        "html_url": None,
    }
    import asyncio

    with (
        patch("app.routers.updates.get_update_status", return_value=catalogue),
        patch(
            "app.routers.updates.AppSettingsRepository.get", new=AsyncMock(return_value=settings)
        ),
    ):
        legacy = asyncio.run(build_update_status())
        status_dir = job_dir / "update-status"
        status_dir.mkdir()
        monkeypatch.setenv("MESHLOOM_UPDATE_STATUS_PATH", str(status_dir / "status.json"))
        secure = asyncio.run(build_update_status())
    assert legacy.legacy_update_helper is True
    assert secure.legacy_update_helper is False
