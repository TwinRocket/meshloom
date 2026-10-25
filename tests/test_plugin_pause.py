# ruff: noqa: F811 (pytest fixtures imported from tests.test_plugins_api)
"""The global plugin pause (MSHL-216): supervisor, persistence, health, API, audit.

Real children (the bundled echo plugin) through the fixtures of
``tests/test_plugins_api.py``; the pause state of ``app/plugins/pause.py`` is
reset around every test.
"""

import asyncio
import os
import sys

import pytest

from app.auth.audit import AUDIT_ACTIONS
from app.config import settings
from app.plugins import pause as pause_module
from app.repository.auth import AuthAuditRepository
from app.repository.plugins import PluginRepository
from app.repository.settings import AppSettingsRepository
from tests.test_plugins_api import (  # noqa: F401 (fixtures)
    ECHO_GRANT,
    api,
    code_of,
    install,
    manager,
    statuses,
    wait_until,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")


@pytest.fixture(autouse=True)
def _fresh_pause(monkeypatch):
    from app.fanout.manager import fanout_manager

    monkeypatch.setattr(pause_module, "_state", pause_module.PauseState())
    monkeypatch.setattr(pause_module, "_lock", asyncio.Lock())
    monkeypatch.setattr(fanout_manager, "_bots_paused", False)


async def _audit_rows(*actions: str):
    rows = list(reversed(await AuthAuditRepository.list_recent(limit=1000)))
    return [r for r in rows if r.action in actions]


async def _pause(api, paused: bool):
    response = await api.put("/api/plugins/pause", json={"paused": paused})
    assert response.status_code == 200, response.text
    return response.json()


def _pid_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


# --- supervisor ------------------------------------------------------------------


async def test_pause_stops_every_running_instance_and_keeps_the_enabled_flags(api, manager):
    first = await install(api, manager)
    second = await install(api, manager)
    off = await install(api, manager)
    assert (
        await api.patch(f"/api/plugins/{off['instance_id']}", json={"enabled": False})
    ).is_success
    pids = [manager.get(i["instance_id"]).pid for i in (first, second)]

    state = await _pause(api, True)
    assert state["paused"] is True and state["by"] == "test-admin" and state["since"]

    assert all(manager.get(i["instance_id"]) is None for i in (first, second))
    for pid in pids:
        assert pid is not None
        await wait_until(lambda p=pid: _pid_gone(p), what="process gone")
    rows = {r["instance_id"]: r for r in await PluginRepository.get_all()}
    assert rows[first["instance_id"]]["enabled"] and rows[second["instance_id"]]["enabled"]
    assert not rows[off["instance_id"]]["enabled"]
    listed = {v["instance_id"]: v["status"] for v in (await api.get("/api/plugins")).json()}
    assert listed == {
        first["instance_id"]: "paused",
        second["instance_id"]: "paused",
        off["instance_id"]: "disabled",
    }
    # "paused" is shown, never stored.
    assert rows[first["instance_id"]]["status"] != "paused"


async def test_nothing_starts_while_paused(api, manager):
    created = await install(api, manager)
    iid = created["instance_id"]
    await _pause(api, True)

    await manager.start(iid)
    await manager.load_from_db()
    assert manager.get(iid) is None
    # Install, enable and config changes are saved but start nothing.
    other = await api.post(
        "/api/plugins/install",
        json={"kind": "bundled", "plugin_id": "meshloom.echo", "granted_permissions": ECHO_GRANT},
    )
    assert other.status_code == 201, other.text
    assert other.json()["status"] == "paused"
    assert manager.get(other.json()["instance_id"]) is None
    assert (await api.patch(f"/api/plugins/{iid}", json={"enabled": False})).is_success
    assert (await api.patch(f"/api/plugins/{iid}", json={"enabled": True})).is_success
    assert (await api.patch(f"/api/plugins/{iid}", json={"label": "renamed"})).is_success
    await asyncio.sleep(0.2)
    assert manager.get(iid) is None
    restart = await api.post(f"/api/plugins/{iid}/restart")
    assert restart.status_code == 409 and code_of(restart) == "plugins_paused"


async def test_events_are_dropped_and_the_test_route_is_409(api, manager):
    created = await install(api, manager)
    iid = created["instance_id"]
    await _pause(api, True)

    manager.dispatch("message", {"type": "PRIV", "text": "x"})
    response = await api.post(f"/api/plugins/{iid}/test", json={"text": "x"})
    assert response.status_code == 409 and code_of(response) == "plugins_paused"

    await _pause(api, False)
    await wait_until(lambda: manager.get(iid) is not None, what="restarted")
    instance = manager.get(iid)
    await wait_until(lambda: instance.status == "running", what="running")
    # Nothing was queued during the pause.
    assert instance.queued == 0 and instance.dropped_events == 0


async def test_resume_restarts_exactly_the_enabled_set(api, manager):
    first = await install(api, manager)
    second = await install(api, manager)
    off = await install(api, manager)
    await api.patch(f"/api/plugins/{off['instance_id']}", json={"enabled": False})
    held = await install(api, manager)
    await _pause(api, True)
    await PluginRepository.update(held["instance_id"], status="crash_loop")

    await _pause(api, False)
    for created in (first, second):
        iid = created["instance_id"]
        await wait_until(lambda i=iid: manager.get(i) is not None, what="restarted")
        instance = manager.get(iid)
        await wait_until(lambda i=instance: i.status == "running", what="running")
    assert manager.get(off["instance_id"]) is None
    assert manager.get(held["instance_id"]) is None


async def test_put_is_idempotent_and_audited_once(api, manager):
    first = await _pause(api, True)
    assert await _pause(api, True) == first
    assert len(await _audit_rows("plugins.paused")) == 1
    await _pause(api, False)
    assert await _pause(api, False) == {"paused": False, "since": None, "by": None}
    assert len(await _audit_rows("plugins.resumed")) == 1


async def test_a_start_in_flight_when_the_pause_lands_does_not_launch(api, manager, monkeypatch):
    # MSHL-341: start() awaits the repository after its first pause check; a pause set
    # during that await must still win, since pause_all() only stops what is registered.
    created = await install(api, manager)
    iid = created["instance_id"]
    await manager.stop(iid)
    assert manager.get(iid) is None

    entered, release = asyncio.Event(), asyncio.Event()
    real_get = PluginRepository.get

    async def slow_get(instance_id):
        entered.set()
        await release.wait()
        return await real_get(instance_id)

    monkeypatch.setattr(PluginRepository, "get", slow_get)
    starting = asyncio.create_task(manager.start(iid))
    await asyncio.wait_for(entered.wait(), 5)
    await manager.pause_all()
    release.set()
    await asyncio.wait_for(starting, 5)
    assert manager.get(iid) is None


# --- persistence -----------------------------------------------------------------


async def test_the_pause_survives_a_restart(api, manager, monkeypatch):
    await _pause(api, True)
    assert (await AppSettingsRepository.get_plugins_pause())[0] is True

    # A new process: fresh in-memory state, read back at boot.
    monkeypatch.setattr(pause_module, "_state", pause_module.PauseState())
    manager.paused = False
    await pause_module.load(manager)
    assert pause_module.state().paused and pause_module.state().by == "test-admin"
    assert manager.paused

    await _pause(api, False)
    assert await AppSettingsRepository.get_plugins_pause() == (False, None, None)


async def test_an_unreadable_pause_fails_closed_until_an_admin_resumes(api, manager, monkeypatch):
    from app.fanout.manager import fanout_manager
    from app.routers.health import HealthResponse, build_health_data

    created = await install(api, manager)
    iid = created["instance_id"]
    await manager.stop_all(5.0)
    manager.paused = False

    real = AppSettingsRepository.get_plugins_pause

    async def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(AppSettingsRepository, "get_plugins_pause", broken)
    await pause_module.load(manager)
    monkeypatch.setattr(AppSettingsRepository, "get_plugins_pause", real)
    state = pause_module.state()
    assert state.paused and state.unreadable and state.since is None and state.by is None
    assert manager.paused and fanout_manager._bots_paused
    await manager.load_from_db()
    assert manager.get(iid) is None
    health = HealthResponse(**await build_health_data(False, None))
    assert health.plugins_disabled_source == "paused"
    assert health.plugins_pause_unreadable is True and health.plugins_paused_since is None
    body = (await api.get("/api/plugins/pause")).json()
    assert body == {"paused": True, "since": None, "by": None}

    # Resume writes the state again and starts the enabled set.
    await _pause(api, False)
    assert await AppSettingsRepository.get_plugins_pause() == (False, None, None)
    assert not pause_module.state().paused and not manager.paused
    await wait_until(lambda: manager.get(iid) is not None, what="restarted")
    health = HealthResponse(**await build_health_data(False, None))
    assert health.plugins_pause_unreadable is False and health.plugins_disabled_source is None


async def test_pausing_over_an_unreadable_hold_records_the_pause(api, manager, monkeypatch):
    real = AppSettingsRepository.get_plugins_pause

    async def broken():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(AppSettingsRepository, "get_plugins_pause", broken)
    await pause_module.load(manager)
    monkeypatch.setattr(AppSettingsRepository, "get_plugins_pause", real)

    state = await _pause(api, True)
    assert state["paused"] is True and state["by"] == "test-admin" and state["since"]
    assert not pause_module.state().unreadable
    assert (await AppSettingsRepository.get_plugins_pause())[0] is True
    assert len(await _audit_rows("plugins.paused")) == 1


# --- health ----------------------------------------------------------------------


async def test_health_reports_the_pause_after_the_host_lock(api, manager, monkeypatch):
    from app.fanout.manager import fanout_manager
    from app.routers.health import HealthResponse, build_health_data

    health = HealthResponse(**await build_health_data(False, None))
    assert health.plugins_disabled is False and health.plugins_disabled_source is None
    assert health.plugins_paused_since is None

    state = await _pause(api, True)
    health = HealthResponse(**await build_health_data(False, None))
    assert health.plugins_disabled is True
    assert health.plugins_disabled_source == "paused"
    assert health.plugins_paused_since == state["since"]
    # The deprecated bot fields mirror the pause; bots stay creatable and restorable.
    assert health.bots_disabled is True and health.bots_disabled_source == "paused"
    assert fanout_manager.bots_disabled_effective() is False

    monkeypatch.setattr(settings, "disable_plugins", True)
    health = HealthResponse(**await build_health_data(False, None))
    assert health.plugins_disabled_source == "env"


# --- access and audit ------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "read", "write"),
    [("kiosk", 403, 403), ("viewer", 403, 403), ("member", 200, 403), ("admin", 200, 200)],
)
async def test_pause_routes_by_role(api, manager, sign_in_as, role, read, write):
    sign_in_as(role)
    assert (await api.get("/api/plugins/pause")).status_code == read
    response = await api.put("/api/plugins/pause", json={"paused": True})
    assert response.status_code == write
    assert pause_module.state().paused is (write == 200)


async def test_member_reads_who_paused(api, manager, sign_in_as):
    await _pause(api, True)
    sign_in_as("member")
    body = (await api.get("/api/plugins/pause")).json()
    assert body["paused"] is True and body["by"] == "test-admin"


async def test_pause_and_resume_are_audited_with_the_actor_only(api, manager):
    await install(api, manager)
    await _pause(api, True)
    await _pause(api, False)
    rows = await _audit_rows("plugins.paused", "plugins.resumed")
    assert [(r.action, r.outcome) for r in rows] == [
        ("plugins.paused", "success"),
        ("plugins.resumed", "success"),
    ]
    for row in rows:
        assert row.actor_user_id == 1 and row.target is None and not row.details


def test_the_audit_vocabulary_has_the_pause():
    assert {"plugins.paused", "plugins.resumed"} <= set(AUDIT_ACTIONS)
