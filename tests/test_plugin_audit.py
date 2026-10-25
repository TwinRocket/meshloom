# ruff: noqa: F811 (pytest fixtures imported from tests.test_plugins_api)
"""Plugin writes in the auth audit log (MSHL-215).

Each write records the actor, ``plugin:<instance_id>``, the plugin id and the names
of the changed fields: never a config value, a secret or the plugin source.
"""

import json
import sys

import pytest

from app.repository.auth import AuthAuditRepository
from tests.test_plugins_api import (  # noqa: F401 (fixtures)
    ECHO_GRANT,
    SECRET,
    api,
    install,
    manager,
    statuses,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

SOURCE_MARK = "audit-source-marker-41c7"


async def _plugin_rows():
    rows = list(reversed(await AuthAuditRepository.list_recent(limit=1000)))
    return [r for r in rows if r.action.startswith("plugin.")]


def _dump(rows) -> str:
    return json.dumps([[r.action, r.target, r.details] for r in rows])


async def test_install_toggle_config_and_remove_are_audited_without_values(api, manager):
    created = await install(
        api, manager, "meshloom.greeter", config={"greeting": "hi", "token": SECRET}
    )
    iid = created["instance_id"]
    await api.patch(f"/api/plugins/{iid}", json={"enabled": False})
    await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    await api.patch(
        f"/api/plugins/{iid}", json={"config": {"greeting": "yo", "token": SECRET}, "label": "G"}
    )
    assert (await api.delete(f"/api/plugins/{iid}")).status_code == 204

    rows = await _plugin_rows()
    assert [(r.action, r.outcome) for r in rows] == [
        ("plugin.installed", "success"),
        ("plugin.disabled", "success"),
        ("plugin.enabled", "success"),
        ("plugin.config_changed", "success"),
        ("plugin.changed", "success"),
        ("plugin.removed", "success"),
    ]
    for row in rows:
        assert row.target == f"plugin:{iid}" and row.actor_user_id == 1
        assert row.details["plugin_id"] == "meshloom.greeter"
    assert rows[3].details["fields"] == ["config"]
    assert rows[4].details["fields"] == ["label"]
    dumped = _dump(rows)
    assert SECRET not in dumped and "yo" not in dumped and '"G"' not in dumped


async def test_local_plugin_source_updates_are_audited_without_the_source(api, manager):
    created = (
        await api.post(
            "/api/plugins", json={"label": "Local", "hooks": ["message"], "permissions": ECHO_GRANT}
        )
    ).json()
    iid = created["instance_id"]
    source = f"async def on_message(event):\n    return {{'v': '{SOURCE_MARK}'}}\n"
    assert (await api.put(f"/api/plugins/{iid}/source", json={"source": source})).is_success
    bad = await api.put(f"/api/plugins/{iid}/source", json={"source": "def broken(:\n"})
    assert bad.status_code == 400

    rows = await _plugin_rows()
    assert [(r.action, r.outcome) for r in rows] == [
        ("plugin.created", "success"),
        ("plugin.updated", "success"),
        ("plugin.updated", "failure"),
    ]
    assert rows[0].target == f"plugin:{iid}"
    assert rows[1].details["fields"] == ["source"]
    assert rows[1].details["source_bytes"] == len(source.encode("utf-8"))
    assert rows[2].details["source_bytes"] == len(b"def broken(:\n")
    assert SOURCE_MARK not in _dump(rows) and "broken" not in _dump(rows)


async def test_member_writes_are_refused_before_any_plugin_change(api, manager, sign_in_as):
    created = await install(api, manager)
    sign_in_as("member")
    response = await api.patch(f"/api/plugins/{created['instance_id']}", json={"enabled": False})
    assert response.status_code == 403
    # The permission gate answers before the route: no plugin row, no change.
    assert all(r.action != "plugin.disabled" for r in await _plugin_rows())
