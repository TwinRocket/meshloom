"""Store plugin updates (MSHL-197): ``app/plugins/updates.py``, ``GET /api/store/updates``
and ``POST /api/plugins/{instance_id}/update``.

The source is a custom repository (``Acme/Echo``) served through the real egress guard
(``Net``, ``publish`` from ``tests/test_store_repositories.py``); the archives are built
from ``tests/plugin_dev/echo``. The auto-update clock is ``updates.clock``.
"""

from __future__ import annotations

import sys
from datetime import datetime

import httpx
import pytest

from app.plugins import custom_repos, installer, store_egress, updates
from app.repository import AppSettingsRepository
from app.repository.plugins import PluginKvRepository, PluginRepository
from tests.test_plugin_installer import GRANT, SCHEMA, SECRET, Net, build_archive, set_version
from tests.test_plugin_installer import manager as manager  # noqa: F401  (fixture)
from tests.test_store_repositories import REPO, detail, publish

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

WIDER = {**GRANT, "contacts.read": {}}
SCHEMA_V2 = {
    "type": "object",
    "properties": {**SCHEMA["properties"], "farewell": {"type": "string"}},
    "required": ["greeting", "farewell"],
}


@pytest.fixture(autouse=True)
async def _reset(monkeypatch):
    await store_egress.reset_store_egress_for_tests()
    custom_repos.register()
    installer.set_download_gate(store_egress.store_egress_allowed)
    set_version(monkeypatch, "5.0.0")
    yield
    installer.set_download_gate(None)
    custom_repos.unregister()
    updates.unregister()
    await store_egress.reset_store_egress_for_tests()


@pytest.fixture
def net(monkeypatch):
    return Net(monkeypatch)


@pytest.fixture
async def api(manager):  # noqa: F811
    from app.main import app

    await AppSettingsRepository.update(store_online=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def _install(api, config: dict) -> str:
    body = {
        "kind": "custom",
        "plugin_id": "acme.echo",
        "repository": REPO,
        "granted_permissions": GRANT,
        "config": config,
    }
    response = await api.post("/api/plugins/install", json=body)
    assert response.status_code == 201, response.text
    return response.json()["instance_id"]


async def _setup(api, net, tmp_path, *, instances: int = 1, **v1) -> list[str]:
    """Release 1.0.0 added and installed ``instances`` times, each with its own config."""
    publish(net, build_archive(tmp_path, schema=SCHEMA, **v1))
    assert (await api.post("/api/store/repositories", json={"repository": REPO})).status_code == 201
    return [
        await _install(api, {"greeting": f"hi {n}", "token": f"{SECRET}-{n}"})
        for n in range(instances)
    ]


async def _release(net, tmp_path, *, schema=SCHEMA, **changes) -> None:
    """Publish 1.1.0 and let the Store refresh cache it."""
    publish(net, build_archive(tmp_path, schema=schema, version="1.1.0", **changes), tag="v1.1.0")
    await custom_repos.refresh_repositories()


async def _kv(instance_id: str) -> dict:
    return {
        key: await PluginKvRepository.get(instance_id, key)
        for key in await PluginKvRepository.keys(instance_id)
    }


async def _row(instance_id: str) -> dict:
    row = await PluginRepository.get(instance_id)
    assert row is not None
    return row


# --- listing -----------------------------------------------------------------------


async def test_list_shows_the_diffs_and_the_holds(api, net, tmp_path):
    [first] = await _setup(api, net, tmp_path)
    assert (await api.get("/api/store/updates")).json() == []
    await _release(
        net,
        tmp_path,
        schema=SCHEMA_V2,
        permissions=WIDER,
        privacy={"sends_to_third_parties": ["api.example.org"], "stores_message_content": False},
    )
    net.requests.clear()
    response = await api.get("/api/store/updates", params={"lang": "fr"})
    assert response.status_code == 200, response.text
    [entry] = response.json()
    assert net.requests == []  # the cached release, no request
    assert (entry["plugin_id"], entry["from_version"], entry["to_version"]) == (
        "acme.echo",
        "1.0.0",
        "1.1.0",
    )
    assert entry["permissions"] == {
        "added": ["contacts.read"],
        "changed": [],
        "removed": [],
        "privacy_widened": True,
    }
    assert entry["config_schema"] == {
        "added": ["farewell"],
        "removed": [],
        "required_added": ["farewell"],
    }
    assert entry["changelog"] is None  # custom releases carry none
    [instance] = entry["instances"]
    assert instance["instance_id"] == first
    assert instance["hold"] == "consent_required"
    assert instance["permissions"] == ["contacts.read"]
    assert instance["missing_fields"] == ["farewell"]


def test_changelog_follows_the_language():
    entry = {"changelog": {"en": "Fixes", "fr": "Corrections"}}
    assert updates._changelog(entry, "fr") == "Corrections"
    assert updates._changelog(entry, "de") == "Fixes"
    assert updates._changelog({"changelog": "Plain"}, "fr") == "Plain"
    assert updates._changelog({}, "fr") is None


async def test_an_incompatible_release_is_not_listed(api, net, tmp_path):
    await _setup(api, net, tmp_path)
    await _release(net, tmp_path, compat={"meshloom": ">=6.0.0"})
    assert (await api.get("/api/store/updates")).json() == []


# --- applying ----------------------------------------------------------------------


async def test_two_instances_update_together_and_keep_config_secrets_and_kv(
    api, manager, net, tmp_path
):
    ids = await _setup(api, net, tmp_path, instances=2)
    for n, instance_id in enumerate(ids):
        await PluginKvRepository.set(instance_id, "count", n)
    await _release(net, tmp_path)

    response = await api.post(f"/api/plugins/{ids[0]}/update", json={})
    assert response.status_code == 200, response.text
    assert response.json()["version"] == "1.1.0"
    assert installer.code_installed(manager.plugins_root(), "acme.echo", "1.1.0")
    for n, instance_id in enumerate(ids):
        row = await _row(instance_id)
        assert (row["version"], row["status"], row["attention"]) == ("1.1.0", row["status"], None)
        assert row["status"] not in ("needs_attention", "incompatible")
        assert row["enabled"] is True
        assert row["config"]["greeting"] == f"hi {n}"
        clear = await PluginRepository.config_for_init(instance_id)
        assert clear is not None and clear["token"] == f"{SECRET}-{n}"
        assert await _kv(instance_id) == {"count": n}
        assert row["granted_permissions"] == GRANT
    again = await api.post(f"/api/plugins/{ids[1]}/update", json={})
    assert (again.status_code, detail(again)["code"]) == (409, "plugin_up_to_date")


async def test_a_new_permission_holds_each_instance_for_consent(api, manager, net, tmp_path):
    first, second = await _setup(api, net, tmp_path, instances=2)
    await _release(net, tmp_path, permissions=WIDER)

    # Consent that does not cover the new manifest: refused before any download.
    net.requests.clear()
    short = await api.post(f"/api/plugins/{first}/update", json={"granted_permissions": GRANT})
    assert (short.status_code, detail(short)["code"]) == (400, "consent_required")
    assert (await _row(first))["version"] == "1.0.0"

    # The consent covers the instance it names, never its sibling.
    response = await api.post(f"/api/plugins/{first}/update", json={"granted_permissions": WIDER})
    assert response.status_code == 200, response.text
    consented, held = await _row(first), await _row(second)
    assert consented["version"] == held["version"] == "1.1.0"
    assert consented["attention"] is None and consented["granted_permissions"] == WIDER
    assert held["status"] == "needs_attention"
    assert held["attention"]["type"] == "consent_required"
    assert held["attention"]["permissions"] == ["contacts.read"]
    assert held["enabled"] is True
    assert "contacts.read" not in held["granted_permissions"]
    assert manager.get(second) is None

    # The usual PATCH that sends the grants accepts it and starts it.
    accepted = await api.patch(f"/api/plugins/{second}", json={"granted_permissions": WIDER})
    assert accepted.status_code == 200, accepted.text
    assert (await _row(second))["attention"] is None


async def test_without_consent_every_instance_is_held(api, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path, permissions=WIDER)
    response = await api.post(f"/api/plugins/{only}/update", json={})
    assert response.status_code == 200, response.text
    row = await _row(only)
    assert row["attention"]["type"] == "consent_required"
    assert row["granted_permissions"] == GRANT


async def test_a_new_required_field_holds_with_config_required(api, manager, net, tmp_path):
    first, second = await _setup(api, net, tmp_path, instances=2)
    await _release(net, tmp_path, schema=SCHEMA_V2)
    response = await api.post(
        f"/api/plugins/{first}/update",
        json={"config": {"greeting": "hi 0", "farewell": "bye"}},
    )
    assert response.status_code == 200, response.text
    filled, waiting = await _row(first), await _row(second)
    assert filled["attention"] is None and filled["config"]["farewell"] == "bye"
    clear = await PluginRepository.config_for_init(first)
    assert clear is not None and clear["token"] == f"{SECRET}-0"
    assert waiting["status"] == "needs_attention"
    assert waiting["attention"] == {"type": "config_required", "fields": ["farewell"]}
    assert waiting["enabled"] is False
    assert manager.get(second) is None


async def test_an_invalid_config_changes_nothing(api, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path, schema=SCHEMA_V2)
    response = await api.post(f"/api/plugins/{only}/update", json={"config": {"nope": 1}})
    assert (response.status_code, detail(response)["code"]) == (400, "config_invalid")
    assert (await _row(only))["version"] == "1.0.0"


async def test_the_update_respects_the_store_consent(api, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path)
    await AppSettingsRepository.update(store_online=False)
    net.requests.clear()
    response = await api.post(f"/api/plugins/{only}/update", json={})
    assert (response.status_code, detail(response)["code"]) == (409, "store_offline")
    assert net.requests == []
    assert (await _row(only))["version"] == "1.0.0"


async def test_a_tampered_archive_is_refused(api, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path)
    publish(
        net,
        build_archive(tmp_path, schema=SCHEMA, version="1.1.0"),
        tag="v1.1.0",
        sha=b"0" * 64,
    )
    response = await api.post(f"/api/plugins/{only}/update", json={})
    assert (response.status_code, detail(response)["code"]) == (400, "archive_mismatch")
    assert (await _row(only))["version"] == "1.0.0"


async def test_a_local_plugin_is_not_updatable(api):
    created = await api.post("/api/plugins", json={"label": "Mine"})
    assert created.status_code == 201, created.text
    response = await api.post(f"/api/plugins/{created.json()['instance_id']}/update", json={})
    assert (response.status_code, detail(response)["code"]) == (409, "plugin_not_updatable")


# --- auto-update -------------------------------------------------------------------


async def _auto_setup(api, net, tmp_path, monkeypatch, **release) -> str:
    [only] = await _setup(api, net, tmp_path)
    assert (await api.patch(f"/api/plugins/{only}", json={"auto_update": True})).status_code == 200
    await AppSettingsRepository.update(
        auto_update_window_start="02:00",
        auto_update_window_end="04:00",
        auto_update_weekdays=[0, 1, 2, 3, 4, 5, 6],
    )
    await _release(net, tmp_path, **release)
    return only


async def test_auto_update_only_inside_the_window(api, net, tmp_path, monkeypatch):
    only = await _auto_setup(api, net, tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 10, 0))
    await updates.auto_update()
    assert (await _row(only))["version"] == "1.0.0"
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    await updates.auto_update()
    assert (await _row(only))["version"] == "1.1.0"


async def test_auto_update_never_applies_a_hold(api, net, tmp_path, monkeypatch):
    only = await _auto_setup(api, net, tmp_path, monkeypatch, permissions=WIDER)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    await updates.auto_update()
    row = await _row(only)
    assert (row["version"], row["attention"]) == ("1.0.0", None)


async def test_auto_update_needs_every_instance_to_opt_in(api, net, tmp_path, monkeypatch):
    only = await _auto_setup(api, net, tmp_path, monkeypatch)
    await _install(api, {"greeting": "other"})
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    await updates.auto_update()
    assert (await _row(only))["version"] == "1.0.0"


async def test_auto_update_runs_as_a_store_refresher(api, net, tmp_path, monkeypatch):
    only = await _auto_setup(api, net, tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    updates.register()
    assert await store_egress.run_store_refresh() is True
    assert (await _row(only))["version"] == "1.1.0"


# --- compat at start ---------------------------------------------------------------


async def test_a_meshloom_bump_marks_an_out_of_range_instance_incompatible(
    api, manager, net, tmp_path, monkeypatch
):
    [only] = await _setup(api, net, tmp_path, compat={"meshloom": ">=5.0.0,<5.1.0"})
    await manager.stop_all(5.0)
    set_version(monkeypatch, "5.1.0")
    await updates.mark_compat()
    assert (await _row(only))["status"] == "incompatible"
    await manager.load_from_db()
    assert manager.get(only) is None
    refused = await api.post(f"/api/plugins/{only}/restart")
    assert refused.status_code == 409

    set_version(monkeypatch, "5.0.3")
    await updates.mark_compat()
    assert (await _row(only))["status"] == "installed"


async def test_an_update_clears_incompatible(api, net, tmp_path, monkeypatch):
    [only] = await _setup(api, net, tmp_path, compat={"meshloom": ">=5.0.0,<5.0.1"})
    set_version(monkeypatch, "5.0.1")
    await updates.mark_compat()
    await _release(net, tmp_path, compat={"meshloom": ">=5.0.0"})
    response = await api.post(f"/api/plugins/{only}/update", json={})
    assert response.status_code == 200, response.text
    row = await _row(only)
    assert (row["version"], row["status"]) == ("1.1.0", row["status"])
    assert row["status"] != "incompatible"


# --- hardening (MSHL-391) ----------------------------------------------------------

# `greeting` is no longer required and its stored string no longer validates.
SCHEMA_INT = {
    "type": "object",
    "properties": {"greeting": {"type": "integer"}, "token": SCHEMA["properties"]["token"]},
    "required": [],
}
WIDER_SEND = {**GRANT, "messages.send": {"targets": "any"}}


def _staging_empty(manager) -> bool:  # noqa: F811
    staging = manager.plugins_root() / "_staging"
    return not staging.exists() or not any(staging.iterdir())


async def test_the_update_route_checks_compat(api, manager, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path, compat={"meshloom": ">=6.0.0"})
    response = await api.post(f"/api/plugins/{only}/update", json={})
    assert (response.status_code, detail(response)["code"]) == (409, "incompatible")
    assert (await _row(only))["version"] == "1.0.0"
    assert installer.code_installed(manager.plugins_root(), "acme.echo", "1.0.0")
    assert _staging_empty(manager)


async def test_a_changed_permission_loses_its_grant_until_consent(api, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path, permissions=WIDER_SEND)
    response = await api.post(f"/api/plugins/{only}/update", json={})
    assert response.status_code == 200, response.text
    row = await _row(only)
    assert row["attention"]["type"] == "consent_required"
    assert row["attention"]["permissions"] == ["messages.send"]
    # The old (narrower) grant is still a valid subset: only the update drops it.
    assert row["granted_permissions"] == {"messages.read": GRANT["messages.read"]}


async def test_a_consent_hold_survives_an_incompatible_round_trip(
    api, manager, net, tmp_path, monkeypatch
):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path, permissions=WIDER, compat={"meshloom": ">=5.0.0,<5.1.0"})
    assert (await api.post(f"/api/plugins/{only}/update", json={})).status_code == 200
    assert (await _row(only))["attention"]["type"] == "consent_required"
    await manager.stop_all(5.0)

    set_version(monkeypatch, "5.1.0")
    await updates.mark_compat()
    assert (await _row(only))["status"] == "incompatible"
    set_version(monkeypatch, "5.0.3")
    await updates.mark_compat()
    row = await _row(only)
    assert row["status"] == "needs_attention"
    assert row["attention"]["type"] == "consent_required"
    await manager.load_from_db()
    assert manager.get(only) is None
    refused = await api.post(f"/api/plugins/{only}/restart")
    assert (refused.status_code, detail(refused)["code"]) == (409, "attention_required")


async def test_an_auto_update_that_would_hold_changes_nothing(api, net, tmp_path, monkeypatch):
    """The guard inside ``update_plugin`` on its own (the refresher's is bypassed)."""
    only = await _auto_setup(api, net, tmp_path, monkeypatch, permissions=WIDER)
    assert await updates.update_plugin(only, auto=True) is None
    row = await _row(only)
    assert (row["version"], row["attention"]) == ("1.0.0", None)


async def test_the_refresher_skips_a_hold_before_updating(api, net, tmp_path, monkeypatch):
    """The refresher's guard on its own (``update_plugin`` is a spy)."""
    await _auto_setup(api, net, tmp_path, monkeypatch, permissions=WIDER)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    calls: list[str] = []

    async def spy(instance_id, **_kwargs):
        calls.append(instance_id)

    monkeypatch.setattr(updates, "update_plugin", spy)
    await updates.auto_update()
    assert calls == []


async def _rollback_setup(api, manager, net, tmp_path) -> list[str]:  # noqa: F811
    ids = await _setup(api, net, tmp_path, instances=2)
    for n, instance_id in enumerate(ids):
        await PluginKvRepository.set(instance_id, "count", n)
        assert manager.get(instance_id) is not None
    await _release(net, tmp_path)
    return ids


async def _assert_rolled_back(api, manager, ids: list[str]) -> None:  # noqa: F811
    root = manager.plugins_root()
    assert installer.code_installed(root, "acme.echo", "1.0.0")
    assert not installer.code_installed(root, "acme.echo", "1.1.0")
    assert _staging_empty(manager)
    for n, instance_id in enumerate(ids):
        row = await _row(instance_id)
        assert (row["version"], row["attention"]) == ("1.0.0", None)
        assert row["config"]["greeting"] == f"hi {n}"
        assert row["granted_permissions"] == GRANT
        assert await _kv(instance_id) == {"count": n}
        assert manager.get(instance_id) is not None  # restarted


async def test_a_failed_row_write_leaves_the_old_plugin(api, manager, net, tmp_path, monkeypatch):
    ids = await _rollback_setup(api, manager, net, tmp_path)

    async def broken(_updates):
        raise RuntimeError("disk full")

    monkeypatch.setattr(PluginRepository, "update_many", staticmethod(broken))
    response = await api.post(f"/api/plugins/{ids[0]}/update", json={})
    assert (response.status_code, detail(response)["code"]) == (500, "update_failed")
    await _assert_rolled_back(api, manager, ids)


async def test_a_failed_tree_swap_leaves_the_old_plugin(api, manager, net, tmp_path, monkeypatch):
    ids = await _rollback_setup(api, manager, net, tmp_path)

    def broken(_self):
        raise OSError("read-only")

    monkeypatch.setattr(installer.PreparedInstall, "swap", broken)
    response = await api.post(f"/api/plugins/{ids[0]}/update", json={})
    assert (response.status_code, detail(response)["code"]) == (500, "update_failed")
    await _assert_rolled_back(api, manager, ids)


async def test_a_rejected_config_value_is_reported_not_silently_dropped(
    api, manager, net, tmp_path
):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path, schema=SCHEMA_INT)
    [entry] = (await api.get("/api/store/updates")).json()
    [instance] = entry["instances"]
    assert (instance["hold"], instance["dropped_fields"]) == (None, ["greeting"])

    response = await api.post(f"/api/plugins/{only}/update", json={})
    assert response.status_code == 200, response.text
    assert response.json()["attention"]["type"] == "config_dropped"
    row = await _row(only)
    assert "greeting" not in row["config"]
    assert row["attention"] == {
        "type": "config_dropped",
        "fields": ["greeting"],
        "from_version": "1.0.0",
        "to_version": "1.1.0",
    }
    assert row["status"] != "needs_attention"
    assert manager.get(only) is not None  # a notice, not a hold

    saved = await api.patch(f"/api/plugins/{only}", json={"config": {"greeting": 3}})
    assert saved.status_code == 200, saved.text
    row = await _row(only)
    assert row["attention"] is None
    clear = await PluginRepository.config_for_init(only)
    assert clear is not None and clear["token"] == f"{SECRET}-0"


async def test_a_held_instance_reports_the_dropped_value_on_its_hold(api, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path, schema=SCHEMA_INT, permissions=WIDER)
    assert (await api.post(f"/api/plugins/{only}/update", json={})).status_code == 200
    attention = (await _row(only))["attention"]
    assert attention["type"] == "consent_required"
    assert attention["config_dropped"] == ["greeting"]


# --- auto-update holds and the hourly evaluation (MSHL-393) -------------------------

# Like SCHEMA_INT, and the secret's declaration changes too: a secret is never reported.
SCHEMA_INT_SECRET = {
    "type": "object",
    "properties": {
        "greeting": {"type": "integer"},
        # The stored secret is longer: the new schema rejects it, and it is never reported.
        "token": {"type": "string", "writeOnly": True, "maxLength": 3},
    },
    "required": [],
}


async def _updated_audits() -> list:
    from app.repository.auth import AuthAuditRepository

    return [
        e for e in await AuthAuditRepository.list_recent(limit=1000) if e.action == "plugin.updated"
    ]


async def test_auto_update_holds_a_dropped_value_for_a_manual_update(
    api, manager, net, tmp_path, monkeypatch
):
    only = await _auto_setup(api, net, tmp_path, monkeypatch, schema=SCHEMA_INT)
    [entry] = (await api.get("/api/store/updates")).json()
    [instance] = entry["instances"]
    assert (instance["hold"], instance["auto_hold"]) == (None, "config_dropped")
    assert instance["dropped_fields"] == ["greeting"]

    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    before = len(net.requests)
    await updates.auto_update()
    row = await _row(only)
    assert (row["version"], row["attention"]) == ("1.0.0", None)
    assert row["config"]["greeting"] == "hi 0"
    assert net.requests[before:] == []  # held before any download

    # The manual update keeps today's behaviour: applied, with the notice.
    response = await api.post(f"/api/plugins/{only}/update", json={})
    assert response.status_code == 200, response.text
    row = await _row(only)
    assert row["version"] == "1.1.0"
    assert row["attention"]["type"] == "config_dropped"
    assert row["attention"]["fields"] == ["greeting"]
    assert manager.get(only) is not None


async def test_the_update_guard_holds_a_dropped_value_on_its_own(api, net, tmp_path, monkeypatch):
    """The guard inside ``update_plugin`` (a release cached before its schema was)."""
    only = await _auto_setup(api, net, tmp_path, monkeypatch, schema=SCHEMA_INT)
    assert await updates.update_plugin(only, auto=True) is None
    row = await _row(only)
    assert (row["version"], row["attention"]) == ("1.0.0", None)
    assert row["config"]["greeting"] == "hi 0"


async def test_the_dropped_value_is_audited(api, net, tmp_path):
    ids = await _setup(api, net, tmp_path, instances=2)
    await _release(net, tmp_path, schema=SCHEMA_INT)
    assert (await api.post(f"/api/plugins/{ids[0]}/update", json={})).status_code == 200
    audits = await _updated_audits()
    assert len(audits) == len(ids)  # the route's row and the sibling's
    for e in audits:
        assert e.details["config_dropped"] == ["greeting"]
        assert e.details["held"] is None


async def test_a_secret_is_never_reported_as_dropped(api, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await _release(net, tmp_path, schema=SCHEMA_INT_SECRET)
    [entry] = (await api.get("/api/store/updates")).json()
    [instance] = entry["instances"]
    assert instance["dropped_fields"] == ["greeting"]
    assert (await api.post(f"/api/plugins/{only}/update", json={})).status_code == 200
    row = await _row(only)
    assert row["attention"]["fields"] == ["greeting"]
    [audit_row] = await _updated_audits()
    assert audit_row.details["config_dropped"] == ["greeting"]
    assert SECRET not in str(audit_row.details) and "token" not in str(audit_row.details)


@pytest.mark.parametrize("kind", ["config_required", "review_permissions"])
async def test_leaving_incompatible_brings_a_hold_back(api, manager, net, tmp_path, kind):
    [only] = await _setup(api, net, tmp_path)
    await manager.stop_all(5.0)
    attention = (
        {"type": kind, "fields": ["greeting"]}
        if kind == "config_required"
        else {
            "type": kind,
            "added": ["contacts.read"],
        }
    )
    await PluginRepository.update(only, status="incompatible", attention=attention)
    await updates.mark_compat()
    row = await _row(only)
    assert (row["status"], row["attention"]["type"]) == ("needs_attention", kind)


async def test_leaving_incompatible_with_a_notice_only_is_installed(api, manager, net, tmp_path):
    [only] = await _setup(api, net, tmp_path)
    await manager.stop_all(5.0)
    notice = {"type": "config_dropped", "fields": ["greeting"]}
    await PluginRepository.update(only, status="incompatible", attention=notice)
    await updates.mark_compat()
    assert (await _row(only))["status"] == "installed"


async def test_the_hourly_evaluation_updates_without_refreshing(api, net, tmp_path, monkeypatch):
    only = await _auto_setup(api, net, tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    refreshed: list[int] = []

    async def refresher() -> None:
        refreshed.append(1)

    store_egress.register_store_refresher(refresher)
    updates.register()
    # Outside the window an evaluation reads the cache only: no request at all.
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 10, 0))
    before = len(net.requests)
    assert await store_egress.run_store_evaluation() is True
    assert net.requests[before:] == []
    assert (await _row(only))["version"] == "1.0.0"
    # Inside it, the cached release is applied (the install pipeline downloads it).
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    assert await store_egress.run_store_evaluation() is True
    assert refreshed == []
    assert store_egress._state.last_refresh_at is None
    assert (await _row(only))["version"] == "1.1.0"


async def test_the_hourly_tick_evaluates_between_refreshes(api, net, tmp_path, monkeypatch):
    """``_wait_evaluating`` over one refresh interval: the evaluators run each hour, the
    refreshers never, ``last_refresh_at`` stays, and outside the window nothing leaves."""
    import asyncio
    from types import SimpleNamespace

    only = await _auto_setup(api, net, tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 10, 0))
    refreshed: list[int] = []
    evaluated: list[int] = []

    async def refresher() -> None:
        refreshed.append(1)

    async def counting_auto_update() -> None:
        evaluated.append(1)
        await updates.auto_update()

    store_egress.register_store_refresher(refresher)
    store_egress.register_store_evaluator(counting_auto_update)
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(
        store_egress,
        "asyncio",
        SimpleNamespace(sleep=fake_sleep, Lock=asyncio.Lock, CancelledError=asyncio.CancelledError),
    )
    store_egress._state.last_refresh_at = 1234
    before = len(net.requests)
    await store_egress._wait_evaluating(store_egress.REFRESH_INTERVAL_S)
    hours = store_egress.REFRESH_INTERVAL_S // store_egress.EVALUATE_INTERVAL_S
    assert slept == [store_egress.EVALUATE_INTERVAL_S] * hours
    # One evaluation per hour in between; the last hour ends on the next refresh.
    assert len(evaluated) == hours - 1
    assert refreshed == []
    assert store_egress._state.last_refresh_at == 1234
    assert net.requests[before:] == []
    assert (await _row(only))["version"] == "1.0.0"


async def test_the_evaluation_runs_no_evaluator_without_the_consent(
    api, net, tmp_path, monkeypatch
):
    await _auto_setup(api, net, tmp_path, monkeypatch)
    evaluated: list[int] = []

    async def evaluator() -> None:
        evaluated.append(1)

    store_egress.register_store_evaluator(evaluator)
    await AppSettingsRepository.update(store_online=False)
    assert await store_egress.run_store_evaluation() is False
    assert evaluated == []
    await AppSettingsRepository.update(store_online=True)
    assert await store_egress.run_store_evaluation() is True
    assert evaluated == [1]


async def test_a_plain_value_now_declared_secret_is_never_reported(api, net, tmp_path):
    """A stored plain field the new schema turns into a secret is not a dropped value."""
    await _setup(api, net, tmp_path)
    schema = {
        "type": "object",
        "properties": {
            "greeting": {"type": "string", "writeOnly": True},
            "token": {"type": "string", "writeOnly": True},
        },
        "required": [],
    }
    await _release(net, tmp_path, schema=schema)
    [entry] = (await api.get("/api/store/updates")).json()
    [instance] = entry["instances"]
    assert instance["dropped_fields"] == []
    assert instance["auto_hold"] is None


async def test_the_evaluation_waits_for_the_store_consent(api, net, tmp_path, monkeypatch):
    only = await _auto_setup(api, net, tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    updates.register()
    await AppSettingsRepository.update(store_online=False)
    before = len(net.requests)
    assert await store_egress.run_store_evaluation() is False
    assert net.requests[before:] == []
    assert (await _row(only))["version"] == "1.0.0"


async def test_a_failed_auto_update_is_not_retried_every_hour(api, net, tmp_path, monkeypatch):
    from fastapi import HTTPException

    await _auto_setup(api, net, tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    calls: list[str] = []

    async def failing(instance_id, **_kwargs):
        calls.append(instance_id)
        raise HTTPException(500, {"code": "update_failed"})

    monkeypatch.setattr(updates, "update_plugin", failing)
    now = [1000.0]
    monkeypatch.setattr(updates, "monotonic", lambda: now[0])
    await updates.auto_update()
    now[0] += store_egress.EVALUATE_INTERVAL_S
    await updates.auto_update()
    assert len(calls) == 1
    now[0] += store_egress.REFRESH_INTERVAL_S
    await updates.auto_update()
    assert len(calls) == 2


async def test_an_unexpected_auto_update_error_waits_too(api, net, tmp_path, monkeypatch, caplog):
    await _auto_setup(api, net, tmp_path, monkeypatch)
    monkeypatch.setattr(updates, "clock", lambda: datetime(2026, 10, 26, 3, 0))
    calls: list[str] = []

    async def crashing(instance_id, **_kwargs):
        calls.append(instance_id)
        raise RuntimeError(f"boom {SECRET}")

    monkeypatch.setattr(updates, "update_plugin", crashing)
    now = [1000.0]
    monkeypatch.setattr(updates, "monotonic", lambda: now[0])
    with caplog.at_level("WARNING", logger=updates.logger.name):
        await updates.auto_update()
        now[0] += store_egress.EVALUATE_INTERVAL_S
        await updates.auto_update()
    assert len(calls) == 1
    assert "RuntimeError" in caplog.text
    assert SECRET not in caplog.text
    now[0] += store_egress.REFRESH_INTERVAL_S
    await updates.auto_update()
    assert len(calls) == 2
