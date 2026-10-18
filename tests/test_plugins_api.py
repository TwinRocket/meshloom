"""The plugin instances API (``app/routers/plugins.py``) against the real supervisor.

Children are real (the echo plugin of ``tests/plugin_dev``); the manager is a fresh one
with short budgets, wired into the router. Bundled packages live in a temporary tree.
"""

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.auth.ws_events import WsEvent
from app.config import settings
from app.events import dump_ws_event
from app.plugins import host as host_module
from app.plugins import installer
from app.plugins.host import Budgets, PluginManager
from app.repository.plugins import PluginKvRepository, PluginRepository
from app.secret_fields import REDACTED

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

ECHO_SRC = Path(__file__).parent / "plugin_dev" / "echo"
SECRET = "tok-9d2f6b1c7a"

FAST = Budgets(
    init_timeout=10.0,
    event_timeout=5.0,
    cancel_grace=0.6,
    shutdown_grace=1.0,
    stop_timeout=5.0,
    backoff_initial=0.05,
    backoff_max=0.2,
    stagger=0.05,
    message_settle=0.0,
)

GREETER_SCHEMA = {
    "type": "object",
    "properties": {
        "greeting": {"type": "string", "title": "Greeting"},
        "repeat": {"type": "integer", "minimum": 1, "maximum": 5},
        "token": {"type": "string", "title": "Token", "writeOnly": True},
    },
    "required": ["greeting"],
}


def _bundled_tree(root: Path) -> None:
    """``meshloom.echo`` (no config) and ``meshloom.greeter`` (a required field and a secret)."""
    for name, schema in (("echo", None), ("greeter", GREETER_SCHEMA)):
        target = root / name
        shutil.copytree(ECHO_SRC, target)
        manifest = json.loads((target / "manifest.json").read_text())
        manifest["id"] = f"meshloom.{name}"
        manifest["name"] = {"en": name.title(), "fr": name.title()}
        if schema is not None:
            manifest["config_schema"] = "config.schema.json"
            (target / "config.schema.json").write_text(json.dumps(schema))
        (target / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def statuses(monkeypatch):
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(host_module, "_broadcast_status", lambda payload: seen.append(payload))
    return seen


@pytest.fixture
def senders(monkeypatch):
    """The radio send path: the API must never reach it."""
    from app.routers import messages

    channel = AsyncMock(side_effect=AssertionError("reached the radio"))
    direct = AsyncMock(side_effect=AssertionError("reached the radio"))
    monkeypatch.setattr(messages, "send_channel_message", channel)
    monkeypatch.setattr(messages, "send_direct_message", direct)
    return channel, direct


@pytest.fixture
async def manager(test_db, tmp_path, statuses, monkeypatch):
    from app.routers import plugins as router

    bundled = tmp_path / "bundled"
    _bundled_tree(bundled)
    monkeypatch.setattr(installer, "BUNDLED_ROOT", bundled)
    mgr = PluginManager(
        FAST,
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    monkeypatch.setattr(router, "plugin_manager", mgr)
    yield mgr
    await mgr.stop_all(5.0)


@pytest.fixture
async def api(manager):
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


ECHO_GRANT = {
    "messages.read": {"channels": "public", "direct": True, "outgoing": False},
    "messages.send": {"targets": "reply"},
}


async def wait_until(predicate, timeout: float = 15.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


async def install(api, manager, plugin_id="meshloom.echo", **extra) -> dict[str, Any]:
    body = {"kind": "bundled", "plugin_id": plugin_id, "granted_permissions": ECHO_GRANT, **extra}
    response = await api.post("/api/plugins/install", json=body)
    assert response.status_code == 201, response.text
    data = response.json()
    if data["enabled"]:
        instance = manager.get(data["instance_id"])
        assert instance is not None
        await wait_until(lambda: instance.status == "running", what="running")
    return data


def code_of(response) -> str:
    return response.json()["detail"]["code"]


# --- access ----------------------------------------------------------------------


async def test_reads_need_member_and_writes_admin(api, manager, sign_in_as):
    created = await install(api, manager)
    iid = created["instance_id"]
    for role, status in (("kiosk", 403), ("viewer", 403)):
        sign_in_as(role)
        assert (await api.get("/api/plugins")).status_code == status
        assert (await api.get("/api/plugins/bundled")).status_code == status
    sign_in_as("member")
    listed = await api.get("/api/plugins")
    assert listed.status_code == 200
    (view,) = listed.json()
    # A member sees the instance, not its config, code, errors or scope.
    assert view["config"] == {} and view["source"] is None and view["last_error"] is None
    calls = [
        api.post("/api/plugins/install", json={}),
        api.post("/api/plugins", json={"label": "x"}),
        api.patch(f"/api/plugins/{iid}", json={"label": "x"}),
        api.put(f"/api/plugins/{iid}/source", json={"source": ""}),
        api.post(f"/api/plugins/{iid}/restart"),
        api.post(f"/api/plugins/{iid}/check"),
        api.post(f"/api/plugins/{iid}/test", json={"text": "x"}),
        api.get(f"/api/plugins/{iid}/logs"),
        api.get(f"/api/plugins/{iid}/diagnostic"),
        api.delete(f"/api/plugins/{iid}"),
    ]
    for response in await asyncio.gather(*calls):
        assert response.status_code == 403, response.request.url


async def test_host_lock_refuses_writes_but_not_reads_or_delete(api, manager, monkeypatch):
    created = await install(api, manager)
    iid = created["instance_id"]
    monkeypatch.setattr(settings, "disable_plugins", True)
    writes = [
        api.post(
            "/api/plugins/install",
            json={"kind": "bundled", "plugin_id": "meshloom.echo", "granted_permissions": {}},
        ),
        api.post("/api/plugins", json={"label": "x"}),
        api.patch(f"/api/plugins/{iid}", json={"label": "x"}),
        api.put(f"/api/plugins/{iid}/source", json={"source": ""}),
        api.post(f"/api/plugins/{iid}/restart"),
        api.post(f"/api/plugins/{iid}/check"),
        api.post(f"/api/plugins/{iid}/test", json={"text": "x"}),
    ]
    for response in await asyncio.gather(*writes):
        assert response.status_code == 403, response.request.url
        assert code_of(response) == "plugins_disabled"
        assert response.json()["detail"]["params"]["source"] == "env"
    assert (await api.get("/api/plugins")).status_code == 200
    assert (await api.get(f"/api/plugins/{iid}/logs")).status_code == 200
    assert (await api.delete(f"/api/plugins/{iid}")).status_code == 204


# --- 404s ------------------------------------------------------------------------


async def test_unknown_instance_is_404_on_every_route(api, manager):
    iid = "00000000-0000-0000-0000-000000000000"
    responses = [
        api.patch(f"/api/plugins/{iid}", json={"label": "x"}),
        api.put(f"/api/plugins/{iid}/source", json={"source": ""}),
        api.post(f"/api/plugins/{iid}/restart"),
        api.post(f"/api/plugins/{iid}/check"),
        api.post(f"/api/plugins/{iid}/test", json={"text": "x"}),
        api.get(f"/api/plugins/{iid}/logs"),
        api.get(f"/api/plugins/{iid}/diagnostic"),
        api.delete(f"/api/plugins/{iid}"),
    ]
    for response in await asyncio.gather(*responses):
        assert response.status_code == 404, response.request.url
        assert code_of(response) == "plugin_not_found"


# --- bundled and install ---------------------------------------------------------


async def test_bundled_lists_the_shipped_plugins_with_their_instance_count(api, manager):
    listed = (await api.get("/api/plugins/bundled")).json()
    assert [p["plugin_id"] for p in listed] == ["meshloom.echo", "meshloom.greeter"]
    greeter = listed[1]
    assert greeter["config_schema"]["required"] == ["greeting"]
    assert greeter["permissions"]["messages.send"] == {"targets": "reply"}
    assert set(greeter["privacy"]) == {"sends_to_third_parties", "stores_message_content"}
    assert greeter["instances"] == 0
    await install(api, manager, "meshloom.echo")
    await install(api, manager, "meshloom.echo", label="Second")
    listed = (await api.get("/api/plugins/bundled")).json()
    assert listed[0]["instances"] == 2


async def test_bundled_is_empty_without_the_tree(api, manager, monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "BUNDLED_ROOT", tmp_path / "absent")
    assert (await api.get("/api/plugins/bundled")).json() == []


async def test_install_starts_the_process(api, manager, statuses):
    created = await install(api, manager, label="My echo")
    assert created["kind"] == "bundled" and created["label"] == "My echo"
    assert created["status"] in {"installed", "starting", "running"}
    assert (manager.plugins_root() / "meshloom.echo" / "manifest.json").is_file()
    [view] = (await api.get("/api/plugins")).json()
    assert view["status"] == "running"
    assert any(
        s["status"] == "running" and s["instance_id"] == view["instance_id"] for s in statuses
    )


async def test_install_requires_consent_covering_the_manifest(api, manager):
    for grant, missing in (
        ({}, ["messages.read", "messages.send"]),
        ({"messages.read": ECHO_GRANT["messages.read"]}, ["messages.send"]),
        # A narrower grant is not consent to what the manifest declares.
        (
            {**ECHO_GRANT, "messages.read": {"channels": "public", "direct": False}},
            ["messages.read"],
        ),
    ):
        response = await api.post(
            "/api/plugins/install",
            json={"kind": "bundled", "plugin_id": "meshloom.echo", "granted_permissions": grant},
        )
        assert response.status_code == 400
        assert code_of(response) == "consent_required"
        assert response.json()["detail"]["params"]["missing"] == missing
    assert (await api.get("/api/plugins")).json() == []


async def test_install_unknown_bundled_plugin_is_404(api, manager):
    response = await api.post(
        "/api/plugins/install",
        json={"kind": "bundled", "plugin_id": "meshloom.nope", "granted_permissions": {}},
    )
    assert response.status_code == 404 and code_of(response) == "plugin_not_bundled"


async def test_install_only_accepts_the_bundled_kind(api, manager):
    response = await api.post(
        "/api/plugins/install",
        json={"kind": "store", "plugin_id": "meshloom.echo", "granted_permissions": ECHO_GRANT},
    )
    assert response.status_code == 422


async def test_install_missing_required_config_needs_attention_and_does_not_start(
    api, manager, statuses
):
    response = await api.post(
        "/api/plugins/install",
        json={
            "kind": "bundled",
            "plugin_id": "meshloom.greeter",
            "granted_permissions": ECHO_GRANT,
            "config": {"token": SECRET},
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["enabled"] is False
    assert data["status"] == "needs_attention"
    assert data["attention"] == {"type": "config_required", "fields": ["greeting"]}
    assert manager.get(data["instance_id"]) is None
    assert SECRET not in response.text
    assert data["config"]["token"] == REDACTED
    assert statuses[-1]["attention"]["type"] == "config_required"


@pytest.mark.parametrize(
    "config",
    [{"greeting": "hi", "repeat": 9}, {"greeting": "hi", "nope": 1}, {"greeting": 3}],
)
async def test_install_invalid_config_is_400_without_the_value(api, manager, config):
    response = await api.post(
        "/api/plugins/install",
        json={
            "kind": "bundled",
            "plugin_id": "meshloom.greeter",
            "granted_permissions": ECHO_GRANT,
            "config": {**config, "token": SECRET},
        },
    )
    assert response.status_code == 400
    assert code_of(response) == "config_invalid"
    assert SECRET not in response.text
    assert response.json()["detail"]["params"]["issues"]
    assert (await api.get("/api/plugins")).json() == []


async def test_install_over_the_instance_cap_is_409_and_leaves_nothing(api, manager, monkeypatch):
    monkeypatch.setattr(manager, "_mem_total", lambda: 1)  # the minimum cap is 3
    for _ in range(3):
        await install(api, manager)
    response = await api.post(
        "/api/plugins/install",
        json={"kind": "bundled", "plugin_id": "meshloom.echo", "granted_permissions": ECHO_GRANT},
    )
    assert response.status_code == 409 and code_of(response) == "instance_cap_reached"
    assert len((await api.get("/api/plugins")).json()) == 3


# --- patch -----------------------------------------------------------------------


async def test_patch_enabling_starts_and_disabling_stops(api, manager, statuses):
    created = await install(api, manager)
    iid = created["instance_id"]
    instance = manager.get(iid)
    assert instance is not None
    pid = instance.pid

    off = await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    assert off.status_code == 200 and off.json()["status"] == "disabled"
    assert manager.get(iid) is None
    assert statuses[-1]["status"] == "disabled"

    on = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert on.status_code == 200
    started = manager.get(iid)
    assert started is not None and started.pid != pid
    await wait_until(lambda: started.status == "running", what="running")


async def test_patch_config_secrets_are_write_only_and_the_instance_restarts(api, manager):
    created = await install(
        api, manager, "meshloom.greeter", config={"greeting": "hi", "token": SECRET}
    )
    iid = created["instance_id"]
    first = manager.get(iid)
    assert first is not None
    pid = first.pid

    # Omitted, null and the mask keep the stored secret; plain fields are replaced.
    for token in (None, REDACTED):
        response = await api.patch(
            f"/api/plugins/{iid}", json={"config": {"greeting": "yo", "token": token}}
        )
        assert response.status_code == 200, response.text
        assert response.json()["config"] == {"greeting": "yo", "token": REDACTED}
        assert SECRET not in response.text
    assert (await PluginRepository.config_for_init(iid))["token"] == SECRET  # type: ignore[index]
    restarted = manager.get(iid)
    assert restarted is not None and restarted.pid != pid

    response = await api.patch(f"/api/plugins/{iid}", json={"config": {"greeting": "yo"}})
    assert response.json()["config"]["token"] == REDACTED
    assert (await PluginRepository.config_for_init(iid))["token"] == SECRET  # type: ignore[index]

    response = await api.patch(
        f"/api/plugins/{iid}", json={"config": {"greeting": "yo", "token": ""}}
    )
    assert response.json()["config"] == {"greeting": "yo"}
    assert "token" not in (await PluginRepository.config_for_init(iid) or {})
    assert SECRET not in (await api.get("/api/plugins")).text


async def test_patch_invalid_config_is_refused_and_changes_nothing(api, manager):
    created = await install(api, manager, "meshloom.greeter", config={"greeting": "hi"})
    iid = created["instance_id"]
    for config in ({"greeting": "hi", "repeat": 99}, {"greeting": "hi", "other": 1}):
        response = await api.patch(f"/api/plugins/{iid}", json={"config": config})
        assert response.status_code == 400, config
        assert code_of(response) == "config_invalid"
    # Dropping a required field of a running instance is refused too.
    response = await api.patch(f"/api/plugins/{iid}", json={"config": {}})
    assert response.status_code == 409 and code_of(response) == "config_required"
    assert (await api.get("/api/plugins")).json()[0]["config"] == {"greeting": "hi"}


async def test_patch_masked_secret_with_no_stored_value_is_refused(api, manager):
    created = await install(api, manager, "meshloom.greeter", config={"greeting": "hi"})
    response = await api.patch(
        f"/api/plugins/{created['instance_id']}",
        json={"config": {"greeting": "hi", "token": REDACTED}},
    )
    assert response.status_code == 400 and code_of(response) == "secret_required"


async def test_patch_completing_the_config_clears_the_attention_then_enable_starts(api, manager):
    response = await api.post(
        "/api/plugins/install",
        json={
            "kind": "bundled",
            "plugin_id": "meshloom.greeter",
            "granted_permissions": ECHO_GRANT,
        },
    )
    iid = response.json()["instance_id"]
    blocked = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert blocked.status_code == 409 and code_of(blocked) == "config_required"
    assert blocked.json()["detail"]["params"]["fields"] == ["greeting"]
    assert manager.get(iid) is None

    filled = await api.patch(
        f"/api/plugins/{iid}", json={"config": {"greeting": "hi"}, "enabled": True}
    )
    assert filled.status_code == 200, filled.text
    assert filled.json()["attention"] is None
    instance = manager.get(iid)
    assert instance is not None
    await wait_until(lambda: instance.status == "running", what="running")


async def test_patch_grants_are_a_subset_of_the_manifest(api, manager):
    created = await install(api, manager)
    iid = created["instance_id"]
    for grant in (
        {"http": {"hosts": ["example.com"]}},  # not declared
        {"messages.send": {"targets": "any"}},  # wider than declared
        {"messages.read": {"channels": "all", "direct": True, "outgoing": False}},
        {"nope": {}},
    ):
        response = await api.patch(f"/api/plugins/{iid}", json={"granted_permissions": grant})
        assert response.status_code == 400, grant
        assert code_of(response) == "permission_exceeds_manifest"
    narrower = {"messages.read": {"channels": "public", "direct": False, "outgoing": False}}
    response = await api.patch(f"/api/plugins/{iid}", json={"granted_permissions": narrower})
    assert response.status_code == 200
    assert response.json()["granted_permissions"] == narrower


async def test_patch_quotas_never_above_the_caps(api, manager):
    created = await install(api, manager)
    iid = created["instance_id"]
    for quotas in (
        {"messages.send": {"per_hour": 61}},
        {"messages.send": {"per_day": 501}},
        {"messages.send": {"per_hour": 0}},
        {"messages.send": {"per_hour": True}},
        {"messages.send": {"burst": 1}},
        {"http": {}},
    ):
        response = await api.patch(f"/api/plugins/{iid}", json={"quotas": quotas})
        assert response.status_code == 400, quotas
        assert code_of(response) == "quota_invalid"
    ok = {"messages.send": {"per_hour": 5, "per_day": 20}}
    response = await api.patch(f"/api/plugins/{iid}", json={"quotas": ok})
    assert response.status_code == 200 and response.json()["quotas"] == ok


async def test_patch_scope_label_flags_and_order(api, manager):
    created = await install(api, manager)
    iid = created["instance_id"]
    for scope in ({"messages": "some"}, {"other": 1}, {"messages": {"channels": 3}}):
        response = await api.patch(f"/api/plugins/{iid}", json={"scope": scope})
        assert response.status_code == 400 and code_of(response) == "scope_invalid"
    scope = {"messages": {"channels": ["AB" * 16], "contacts": "none"}}
    response = await api.patch(
        f"/api/plugins/{iid}",
        json={"scope": scope, "label": "Renamed", "auto_update": True, "sort_order": 7},
    )
    body = response.json()
    assert response.status_code == 200
    assert (body["scope"], body["label"], body["auto_update"], body["sort_order"]) == (
        scope,
        "Renamed",
        True,
        7,
    )
    assert (await api.patch(f"/api/plugins/{iid}", json={"sort_order": -1})).status_code == 422
    assert (await api.patch(f"/api/plugins/{iid}", json={"id": "x"})).status_code == 422


# --- local plugins ---------------------------------------------------------------


async def test_create_local_plugin_from_a_template(api, manager):
    response = await api.post(
        "/api/plugins",
        json={"label": "Pling bot", "hooks": ["message"], "permissions": ECHO_GRANT},
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["kind"] == "local" and data["enabled"] is False
    assert data["plugin_id"].startswith("local.pling-bot-")
    assert "Plong!" in data["source"]
    assert data["granted_permissions"] == ECHO_GRANT
    assert manager.get(data["instance_id"]) is None

    blank = await api.post("/api/plugins", json={"label": "Blank", "template": "blank"})
    assert "Plong!" not in blank.json()["source"]


async def test_create_local_plugin_rejects_a_bad_manifest(api, manager):
    response = await api.post(
        "/api/plugins",
        json={"label": "Bad", "permissions": {"notifications.send": {}}},
    )
    assert response.status_code == 400 and code_of(response) == "manifest_invalid"
    assert (await api.post("/api/plugins", json={"label": ""})).status_code == 422


async def test_put_source_validates_then_restarts_a_running_local_plugin(api, manager):
    created = (
        await api.post(
            "/api/plugins", json={"label": "Local", "hooks": ["message"], "permissions": ECHO_GRANT}
        )
    ).json()
    iid = created["instance_id"]
    enabled = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    first = manager.get(iid)
    assert first is not None
    await wait_until(lambda: first.status == "running", what="running")
    pid = first.pid

    bad = await api.put(f"/api/plugins/{iid}/source", json={"source": "def broken(:\n"})
    assert bad.status_code == 400 and code_of(bad) == "source_syntax_error"
    assert bad.json()["detail"]["params"]["line"] == 1
    assert manager.get(iid).pid == pid  # type: ignore[union-attr]

    source = "async def on_message(event):\n    return {'v': 2}\n"
    ok = await api.put(f"/api/plugins/{iid}/source", json={"source": source})
    assert ok.status_code == 200, ok.text
    assert ok.json()["instance"]["source"] == source
    second = manager.get(iid)
    assert second is not None and second.pid != pid
    await wait_until(lambda: second.status == "running", what="running again")


async def test_put_source_scan_reports_review_reasons(api, manager):
    created = (await api.post("/api/plugins", json={"label": "Scan"})).json()
    result = await api.put(
        f"/api/plugins/{created['instance_id']}/source",
        json={"source": "import subprocess\nprint('x')\n"},
    )
    codes = {r["code"] for r in result.json()["scan"]}
    assert {"import_not_allowed", "print_call"} <= codes


async def test_put_source_is_local_only_and_bounded(api, manager):
    bundled = await install(api, manager)
    response = await api.put(f"/api/plugins/{bundled['instance_id']}/source", json={"source": ""})
    assert response.status_code == 409 and code_of(response) == "not_local"
    local = (await api.post("/api/plugins", json={"label": "Big"})).json()
    url = f"/api/plugins/{local['instance_id']}/source"
    # Over 1 MiB of characters is cut by the body model; multibyte text by the byte check.
    assert (await api.put(url, json={"source": "x = 1\n" * 200_000})).status_code == 422
    huge = await api.put(url, json={"source": "# " + "é" * 600_000})
    assert huge.status_code == 400 and code_of(huge) == "source_too_large"


# --- actions ---------------------------------------------------------------------


async def test_restart_replaces_the_process(api, manager):
    created = await install(api, manager)
    iid = created["instance_id"]
    pid = manager.get(iid).pid  # type: ignore[union-attr]
    response = await api.post(f"/api/plugins/{iid}/restart")
    assert response.status_code == 200
    again = manager.get(iid)
    assert again is not None and again.pid != pid
    await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    refused = await api.post(f"/api/plugins/{iid}/restart")
    assert refused.status_code == 409 and code_of(refused) == "plugin_disabled"


async def test_check_validates_the_package_and_scans_without_running_it(api, manager):
    created = await install(api, manager)
    result = await api.post(f"/api/plugins/{created['instance_id']}/check")
    assert result.status_code == 200, result.text
    assert result.json()["ok"] is True

    (manager.plugins_root() / "meshloom.echo" / "manifest.json").write_text("{}")
    broken = await api.post(f"/api/plugins/{created['instance_id']}/check")
    assert broken.json()["ok"] is False and broken.json()["errors"]

    local = (await api.post("/api/plugins", json={"label": "L"})).json()
    await api.put(f"/api/plugins/{local['instance_id']}/source", json={"source": "eval('1')\n"})
    checked = (await api.post(f"/api/plugins/{local['instance_id']}/check")).json()
    assert checked["ok"] is True
    assert "eval_call" in {w["code"] for w in checked["warnings"]}


async def test_test_returns_the_captured_reply_and_never_reaches_the_radio(api, manager, senders):
    created = await install(api, manager)
    response = await api.post(
        f"/api/plugins/{created['instance_id']}/test", json={"text": "hello mesh"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["truncated"] is False
    assert len(body["sends"]) == 1
    assert "hello mesh" in json.dumps(body["sends"])
    channel, direct = senders
    channel.assert_not_awaited()
    direct.assert_not_awaited()


async def test_test_validation_and_state_errors(api, manager, senders):
    created = await install(api, manager)
    iid = created["instance_id"]
    url = f"/api/plugins/{iid}/test"
    assert (await api.post(url, json={"text": ""})).status_code == 422
    assert (await api.post(url, json={"text": "x" * 2000})).status_code == 422
    big = await api.post(url, json={"text": "é" * 600})
    assert big.status_code == 400 and code_of(big) == "test_text_too_long"
    both = await api.post(url, json={"text": "x", "channel": "AA" * 16, "contact": "bb" * 32})
    assert both.status_code == 400 and code_of(both) == "test_target_ambiguous"
    unknown_channel = await api.post(url, json={"text": "x", "channel": "AA" * 16})
    assert unknown_channel.status_code == 404 and code_of(unknown_channel) == "channel_not_found"
    unknown_contact = await api.post(url, json={"text": "x", "contact": "bb" * 32})
    assert unknown_contact.status_code == 404 and code_of(unknown_contact) == "contact_not_found"
    await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    stopped = await api.post(url, json={"text": "x"})
    assert stopped.status_code == 409 and code_of(stopped) == "instance_not_running"


async def test_test_scrubs_secrets_from_the_captured_output(api, manager, senders):
    local = (
        await api.post(
            "/api/plugins", json={"label": "Leaky", "hooks": ["message"], "permissions": ECHO_GRANT}
        )
    ).json()
    iid = local["instance_id"]
    await api.put(
        f"/api/plugins/{iid}/source",
        json={
            "source": "from meshloom_plugin import host\n"
            "async def on_message(event):\n"
            f"    await host().call('messages.send', {{'to': 'reply', 'text': 'k={SECRET}'}})\n"
            f"    return {{'leak': '{SECRET}'}}\n"
        },
    )
    # Secrets are the schema's: give the local instance a schema with one.
    await PluginRepository.update(
        iid,
        config_schema={
            "type": "object",
            "properties": {"token": {"type": "string", "writeOnly": True}},
        },
        config={"token": SECRET},
    )
    await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    instance = manager.get(iid)
    assert instance is not None
    await wait_until(lambda: instance.status == "running", what="running")
    response = await api.post(f"/api/plugins/{iid}/test", json={"text": "hi"})
    assert response.status_code == 200, response.text
    assert SECRET not in response.text
    assert "****" in response.text


# --- logs and diagnostic ---------------------------------------------------------


async def test_logs_are_bounded_truncated_and_redacted(api, manager):
    created = await install(
        api, manager, "meshloom.greeter", config={"greeting": "hi", "token": SECRET}
    )
    iid = created["instance_id"]
    instance = manager.get(iid)
    assert instance is not None
    instance.log.feed(f"leaking {SECRET} here\n".encode())
    instance.log.feed(b"\n".join(b"line %d" % n for n in range(600)) + b"\n")
    instance.log.feed(b"x" * 1500 + b"\n")

    response = await api.get(f"/api/plugins/{iid}/logs")
    body = response.json()
    assert response.status_code == 200
    assert len(body["lines"]) == 200 and body["truncated"] is True
    assert all(len(line) <= 1020 for line in body["lines"])
    assert body["lines"][-1].endswith("[truncated]")
    assert (await api.get(f"/api/plugins/{iid}/logs?limit=3")).json()["lines"][:2] == [
        "line 598",
        "line 599",
    ]
    assert (await api.get(f"/api/plugins/{iid}/logs?limit=501")).status_code == 422
    assert (await api.get(f"/api/plugins/{iid}/logs?limit=0")).status_code == 422

    everything = (await api.get(f"/api/plugins/{iid}/logs?limit=500")).text
    assert SECRET not in everything
    # The secret line fell out of the last 500 lines: check the redaction on a short log.
    instance.log.feed(f"token={SECRET} again\n".encode())
    assert SECRET not in (await api.get(f"/api/plugins/{iid}/logs?limit=1")).text
    assert "****" in (await api.get(f"/api/plugins/{iid}/logs?limit=1")).text


async def test_logs_of_a_stopped_instance_are_empty(api, manager):
    created = await install(api, manager)
    await api.patch(f"/api/plugins/{created['instance_id']}", json={"enabled": False})
    body = (await api.get(f"/api/plugins/{created['instance_id']}/logs")).json()
    assert body == {"lines": [], "truncated": False}


async def test_diagnostic_has_versions_and_no_secret(api, manager):
    created = await install(
        api, manager, "meshloom.greeter", config={"greeting": "hi", "token": SECRET}
    )
    iid = created["instance_id"]
    instance = manager.get(iid)
    assert instance is not None
    for n in range(80):
        instance.log.feed(f"line {n} {SECRET}\n".encode())
    await instance._set_error("handler_error: boom " + SECRET)

    response = await api.get(f"/api/plugins/{iid}/diagnostic")
    assert response.status_code == 200
    body = response.json()
    assert body["plugin_id"] == "meshloom.greeter"
    assert body["plugin_version"] == "1.0.0"
    assert body["python_version"] and body["meshloom_version"] and body["isolation"]
    assert len(body["logs"]) == 50
    assert body["last_error"].startswith("handler_error")
    assert body["granted_permissions"] == ["messages.read", "messages.send"]
    assert SECRET not in response.text
    assert "hi" not in json.dumps({k: v for k, v in body.items() if k != "logs"}).split('"')[0]
    assert SECRET not in (await api.get("/api/plugins")).text


async def test_plugin_logs_are_not_in_the_debug_snapshot(api, manager):
    created = await install(api, manager)
    marker = "PLUGIN-LOG-MARKER-5c1e"
    manager.get(created["instance_id"]).log.feed(f"{marker}\n".encode())  # type: ignore[union-attr]
    response = await api.get("/api/debug")
    assert response.status_code == 200
    assert marker not in response.text
    assert created["instance_id"] not in response.text


# --- delete ----------------------------------------------------------------------


async def test_delete_stops_the_process_and_can_keep_the_data(api, manager):
    created = await install(api, manager)
    iid = created["instance_id"]
    await PluginKvRepository.set(iid, "k", {"v": 1})
    instance = manager.get(iid)
    assert instance is not None
    pid = instance.pid

    assert (await api.delete(f"/api/plugins/{iid}?keep_data=true")).status_code == 204
    assert manager.get(iid) is None
    assert (await api.get("/api/plugins")).json() == []
    assert await PluginKvRepository.keys(iid) == ["k"]
    assert pid is not None
    with pytest.raises(ProcessLookupError):
        await wait_until(lambda: _gone(pid), what="process gone")
        import os

        os.kill(pid, 0)

    again = await install(api, manager)
    await PluginKvRepository.set(again["instance_id"], "k", 1)
    assert (await api.delete(f"/api/plugins/{again['instance_id']}")).status_code == 204
    assert await PluginKvRepository.keys(again["instance_id"]) == []


def _gone(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


async def test_delete_a_local_plugin_removes_its_directory(api, manager):
    created = (await api.post("/api/plugins", json={"label": "Gone"})).json()
    iid = created["instance_id"]
    await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    directory = manager.plugins_root() / "local" / iid
    await wait_until(directory.is_dir, what="local directory")
    await api.delete(f"/api/plugins/{iid}")
    assert not directory.exists()


# --- websocket -------------------------------------------------------------------


async def test_plugin_status_events_follow_status_changes_and_validate(api, manager, statuses):
    created = await install(api, manager)
    iid = created["instance_id"]
    await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    seen = [s["status"] for s in statuses if s["instance_id"] == iid]
    assert "running" in seen and seen[-1] == "disabled"
    for payload in statuses:
        assert {"instance_id", "status", "last_error", "attention"} <= set(payload)
        # The payload is a valid `plugin_status` frame (the model every emit goes through).
        json.loads(dump_ws_event(WsEvent.PLUGIN_STATUS, payload))


# --- review round 1 (PR 255) -----------------------------------------------------


async def _greeter(api, manager) -> str:
    created = await install(api, manager, "meshloom.greeter", config={"greeting": "hi"})
    return created["instance_id"]


async def test_clearing_a_required_field_on_a_stopped_instance_blocks_the_enable(api, manager):
    iid = await _greeter(api, manager)
    await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    cleared = await api.patch(f"/api/plugins/{iid}", json={"config": {}})
    assert cleared.status_code == 200
    assert cleared.json()["attention"] == {"type": "config_required", "fields": ["greeting"]}
    assert cleared.json()["status"] == "needs_attention"
    enable = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert enable.status_code == 409 and code_of(enable) == "config_required"
    assert manager.get(iid) is None

    # The flag is not trusted either: a row with the field gone and no attention.
    await PluginRepository.update(iid, attention=None, status="disabled", config={})
    again = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert again.status_code == 409 and code_of(again) == "config_required"
    await PluginRepository.update(iid, enabled=True)
    restart = await api.post(f"/api/plugins/{iid}/restart")
    assert restart.status_code == 409 and code_of(restart) == "config_required"


@pytest.mark.parametrize("status", ["revoked", "incompatible"])
async def test_revoked_and_incompatible_never_start(api, manager, status):
    iid = await _greeter(api, manager)
    await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    await PluginRepository.update(iid, status=status)
    refused = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert refused.status_code == 409 and code_of(refused) == f"plugin_{status}"
    assert manager.get(iid) is None
    await PluginRepository.update(iid, enabled=True)
    restart = await api.post(f"/api/plugins/{iid}/restart")
    assert restart.status_code == 409 and code_of(restart) == f"plugin_{status}"
    assert manager.get(iid) is None


async def test_other_attention_needs_an_acknowledgement(api, manager):
    iid = await _greeter(api, manager)
    await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    await PluginRepository.update(
        iid, status="needs_attention", attention={"type": "review_permissions"}
    )
    refused = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert refused.status_code == 409 and code_of(refused) == "attention_required"
    assert refused.json()["detail"]["params"]["type"] == "review_permissions"
    assert manager.get(iid) is None
    granted = (await api.get("/api/plugins")).json()[0]["granted_permissions"]
    ok = await api.patch(
        f"/api/plugins/{iid}", json={"enabled": True, "granted_permissions": granted}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["attention"] is None
    instance = manager.get(iid)
    assert instance is not None
    await wait_until(lambda: instance.status == "running", what="running")


async def test_last_error_and_summary_are_scrubbed_of_secrets(api, manager):
    created = await install(
        api, manager, "meshloom.greeter", config={"greeting": "hi", "token": SECRET}
    )
    iid = created["instance_id"]
    instance = manager.get(iid)
    assert instance is not None
    await instance._set_error(f"handler_error: boom {SECRET}")
    await instance.set_summary(f"token {SECRET}")
    # A value stored before the secret was known to the supervisor still reads clean.
    instance.last_error = f"handler_error: {SECRET}"
    instance.summary = f"s {SECRET}"
    for response in (
        await api.get("/api/plugins"),
        await api.patch(f"/api/plugins/{iid}", json={"sort_order": 3}),
    ):
        assert SECRET not in response.text


async def test_local_plugin_permissions_are_editable_through_the_manifest(api, manager):
    created = (await api.post("/api/plugins", json={"label": "Perms"})).json()
    iid = created["instance_id"]
    assert created["granted_permissions"] == {}
    response = await api.patch(f"/api/plugins/{iid}", json={"granted_permissions": ECHO_GRANT})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["granted_permissions"] == ECHO_GRANT
    assert body["manifest"]["permissions"] == ECHO_GRANT
    bad = await api.patch(
        f"/api/plugins/{iid}", json={"granted_permissions": {"notifications.send": {}}}
    )
    assert bad.status_code == 400 and code_of(bad) == "manifest_invalid"


@pytest.mark.parametrize("status", ["revoked", "incompatible"])
async def test_frozen_status_survives_the_config_required_detour(api, manager, status):
    iid = await _greeter(api, manager)
    await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    await PluginRepository.update(iid, status=status)

    cleared = await api.patch(f"/api/plugins/{iid}", json={"config": {}})
    assert cleared.status_code == 200
    assert cleared.json()["status"] == status and cleared.json()["attention"] is None

    filled = await api.patch(
        f"/api/plugins/{iid}", json={"config": {"greeting": "x"}, "enabled": True}
    )
    assert filled.status_code == 409 and code_of(filled) == f"plugin_{status}"
    assert manager.get(iid) is None
    row = await PluginRepository.get(iid)
    assert row["status"] == status and row["enabled"] is False  # type: ignore[index]

    # Same for restart, even with the config complete and the row enabled.
    await PluginRepository.update(iid, enabled=True)
    restart = await api.post(f"/api/plugins/{iid}/restart")
    assert restart.status_code == 409 and code_of(restart) == f"plugin_{status}"
    assert (await PluginRepository.get(iid))["status"] == status  # type: ignore[index]
    assert manager.get(iid) is None
