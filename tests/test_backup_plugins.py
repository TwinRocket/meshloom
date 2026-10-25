"""The ``plugins`` section of the full backup (Store T12, ``app/services/backup_plugins.py``).

The guarantees under test: no secret value of an instance ever reaches the file; local
plugins come back whole and every other kind as a "to reinstall" row; every restored row is
disabled; existing rows are never changed; a bad entry is dropped on its own; a file without
the section changes nothing.
"""

import json
import shutil
import sys
import uuid
from typing import Any

import pytest

from app.models import BackupSection
from app.plugins import host as host_module
from app.repository.plugins import PluginKvRepository, PluginRepository
from app.services import backup_plugins
from app.services.backup import export_backup_v2, export_document
from app.services.backup_plugins import PLUGINS_SECTION, export_plugins
from app.services.backup_sections import restore_sections
from tests import test_plugins_api as plugins_api
from tests.test_plugins_api import ECHO_GRANT, ECHO_SRC, SECRET, code_of, install

# The plugin API tests' supervisor, bundled tree and client (fixtures by name).
api, manager, statuses = plugins_api.api, plugins_api.manager, plugins_api.statuses

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

KV_MARKER = "kv-value-5c1e9a"
LOCAL_SECRET = "local-tok-77ab02"


@pytest.fixture
def host_manager(manager, monkeypatch):
    """The on_restore hook reaches the supervisor through ``app.plugins.host``."""
    monkeypatch.setattr(host_module, "plugin_manager", manager)
    return manager


async def _populate(api, manager) -> dict[str, dict[str, Any]]:
    """A bundled instance with a secret, a local plugin with a secret, and KV data."""
    greeter = await install(
        api,
        manager,
        plugin_id="meshloom.greeter",
        config={"greeting": "Hi", "repeat": 2, "token": SECRET},
    )
    stopped = await api.patch(f"/api/plugins/{greeter['instance_id']}", json={"enabled": False})
    assert stopped.status_code == 200, stopped.text
    created = await api.post(
        "/api/plugins", json={"label": "Pling bot", "hooks": ["message"], "permissions": ECHO_GRANT}
    )
    assert created.status_code == 201, created.text
    local = created.json()
    schema = {
        "type": "object",
        "properties": {
            "city": {"type": "string"},
            "api_key": {"type": "string", "writeOnly": True},
        },
    }
    local = await PluginRepository.update(
        local["instance_id"],
        config_schema=schema,
        config={"city": "Paris", "api_key": LOCAL_SECRET},
    )
    assert local is not None
    for row in (greeter, local):
        await PluginKvRepository.set(row["instance_id"], "state", {"note": KV_MARKER})
    return {"greeter": greeter, "local": local}


async def _wipe() -> None:
    for row in await PluginRepository.get_all():
        await PluginRepository.delete(row["instance_id"])


def _entries(section) -> dict[str, dict[str, Any]]:
    return {entry["plugin_id"]: entry for entry in section.data["instances"]}


# -- export ---------------------------------------------------------------------


async def test_no_secret_and_no_plugin_data_ever_reach_the_file(backup_db, api, manager):
    rows = await _populate(api, manager)
    document = json.dumps(export_document(await export_backup_v2()))
    for value in (SECRET, LOCAL_SECRET, KV_MARKER):
        assert value not in document
    assert "********" not in json.dumps(json.loads(document)["sections"][PLUGINS_SECTION])

    section = json.loads(document)["sections"][PLUGINS_SECTION]
    assert section["sensitive"] is False and section["secrets_omitted"] is True
    entries = {entry["plugin_id"]: entry for entry in section["data"]["instances"]}
    greeter = entries["meshloom.greeter"]
    assert greeter["config"] == {"greeting": "Hi", "repeat": 2}
    assert greeter["secret_omitted"] == {"token": True}
    assert greeter["source"] is None
    local = entries[rows["local"]["plugin_id"]]
    assert local["config"] == {"city": "Paris"}
    assert local["secret_omitted"] == {"api_key": True}
    assert "Plong!" in local["source"]
    for entry in entries.values():
        assert not {"status", "attention", "last_error", "archive_sha256"} & set(entry)


async def test_no_instance_no_section(backup_db):
    assert await export_plugins() is None
    assert PLUGINS_SECTION not in export_document(await export_backup_v2()).get("sections", {})


# -- restore --------------------------------------------------------------------


async def test_round_trip_restores_local_whole_and_the_others_to_reinstall(
    backup_db, api, host_manager
):
    rows = await _populate(api, host_manager)
    await api.patch(f"/api/plugins/{rows['greeter']['instance_id']}", json={"enabled": True})
    section = await export_plugins()
    assert section is not None
    await _wipe()
    warnings = await restore_sections({PLUGINS_SECTION: section})
    assert sorted(warnings) == sorted(
        ["plugins_restored:1", "plugins_to_reinstall:1", "plugin_secrets_missing:2"]
    )
    restored = {row["plugin_id"]: row for row in await PluginRepository.get_all()}
    assert set(restored) == {"meshloom.greeter", rows["local"]["plugin_id"]}
    for row in restored.values():
        assert row["enabled"] is False
        assert row["status"] == "needs_attention"
        assert row["secret_fields"] == []
    local = restored[rows["local"]["plugin_id"]]
    assert local["instance_id"] == rows["local"]["instance_id"]
    assert local["attention"] == {
        "type": "restored",
        "was_enabled": False,
        "secrets_missing": ["api_key"],
    }
    assert local["source"] == rows["local"]["source"]
    assert local["config"] == {"city": "Paris"}
    assert local["granted_permissions"] == ECHO_GRANT
    greeter = restored["meshloom.greeter"]
    assert greeter["attention"] == {
        "type": "reinstall",
        "was_enabled": True,
        "secrets_missing": ["token"],
    }
    assert greeter["config"] == {"greeting": "Hi", "repeat": 2}
    for row in restored.values():
        assert await PluginKvRepository.get(row["instance_id"], "state") is None
    async with backup_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM plugin_secrets") as cursor:
            assert (await cursor.fetchone())[0] == 0


async def test_a_reinstall_row_never_starts(backup_db, api, manager):
    manifest = json.loads((ECHO_SRC / "manifest.json").read_text())
    entry = {
        "instance_id": str(uuid.uuid4()),
        "plugin_id": manifest["id"],
        "kind": "store",
        "label": "Echo",
        "version": manifest["version"],
        "enabled": True,
        "manifest": manifest,
        "granted_permissions": ECHO_GRANT,
        "repository": "https://store.example/acme/echo",
    }
    warnings = await restore_sections({PLUGINS_SECTION: BackupSection(data={"instances": [entry]})})
    assert warnings == ["plugins_to_reinstall:1"]
    row = await PluginRepository.get(entry["instance_id"])
    assert row is not None and row["repository"] == entry["repository"]
    assert row["granted_permissions"] == ECHO_GRANT

    for body in ({"enabled": True}, {"enabled": True, "granted_permissions": ECHO_GRANT}):
        response = await api.patch(f"/api/plugins/{entry['instance_id']}", json=body)
        assert response.status_code == 409, response.text
        assert code_of(response) == "plugin_reinstall_required"
    assert manager.get(entry["instance_id"]) is None


async def test_a_restored_local_plugin_starts_once_reviewed(backup_db, api, manager):
    created = await api.post(
        "/api/plugins", json={"label": "Local", "hooks": ["message"], "permissions": ECHO_GRANT}
    )
    section = await export_plugins()
    assert section is not None
    await _wipe()
    await restore_sections({PLUGINS_SECTION: section})
    iid = created.json()["instance_id"]
    refused = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert refused.status_code == 409 and code_of(refused) == "attention_required"
    accepted = await api.patch(
        f"/api/plugins/{iid}", json={"enabled": True, "granted_permissions": ECHO_GRANT}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] != "needs_attention"


async def test_the_hook_brings_back_a_bundled_plugin_this_release_ships(
    backup_db, api, host_manager
):
    rows = await _populate(api, host_manager)
    section = await export_plugins()
    assert section is not None
    await _wipe()
    # Another host: the bundled code is not written out yet.
    shutil.rmtree(host_manager.plugins_root() / "meshloom.greeter")
    await restore_sections({PLUGINS_SECTION: section})
    reinstall = await PluginRepository.get(rows["greeter"]["instance_id"])
    assert reinstall is not None and reinstall["attention"]["type"] == "reinstall"

    await backup_plugins._after_restore("json")
    greeter = await PluginRepository.get(rows["greeter"]["instance_id"])
    assert greeter is not None
    assert greeter["attention"]["type"] == "restored"
    assert greeter["attention"]["secrets_missing"] == ["token"]
    assert greeter["manifest"]["id"] == "meshloom.greeter"
    assert greeter["config"] == {"greeting": "Hi", "repeat": 2}
    assert greeter["enabled"] is False
    assert (host_manager.plugins_root() / "meshloom.greeter" / "manifest.json").is_file()


async def test_existing_rows_are_never_changed(backup_db, api, manager):
    rows = await _populate(api, manager)
    section = await export_plugins()
    assert section is not None
    before = await PluginRepository.get_all()

    # The same instances: kept as they are.
    warnings = await restore_sections({PLUGINS_SECTION: section})
    assert sorted(warnings) == sorted(
        [f"plugin_kept:{rows['local']['plugin_id']}", "plugin_kept:meshloom.greeter"]
    )
    assert await PluginRepository.get_all() == before

    # The local plugin under another instance id: a new plugin id beside the existing one.
    local = _entries(section)[rows["local"]["plugin_id"]]
    copy = {**local, "instance_id": str(uuid.uuid4())}
    # An instance id another plugin already uses: a new instance id.
    taken = {**_entries(section)["meshloom.greeter"], "plugin_id": "meshloom.echo"}
    taken["manifest"] = {**taken["manifest"], "id": "meshloom.echo"}
    taken["instance_id"] = rows["local"]["instance_id"]
    warnings = await restore_sections(
        {PLUGINS_SECTION: BackupSection(data={"instances": [copy, taken]})}
    )
    assert "plugins_restored:1" in warnings
    after = await PluginRepository.get_all()
    assert [row for row in after if row["instance_id"] in {r["instance_id"] for r in before}] == (
        before
    )
    new = [row for row in after if row["instance_id"] not in {r["instance_id"] for r in before}]
    assert len(new) == 2
    new_local = next(row for row in new if row["kind"] == "local")
    assert new_local["plugin_id"] != rows["local"]["plugin_id"]
    assert new_local["plugin_id"].startswith("local.pling-bot-")
    assert new_local["manifest"]["id"] == new_local["plugin_id"]
    assert new_local["instance_id"] == copy["instance_id"]
    echo = next(row for row in new if row["plugin_id"] == "meshloom.echo")
    assert echo["instance_id"] != rows["local"]["instance_id"]


async def test_kept_data_goes_back_only_to_the_same_plugin(backup_db, api, manager):
    """MSHL-366: an uninstall with keep_data, then a restore. The same instance gets its
    data back; an entry of another plugin under that instance id gets a new id."""
    rows = await _populate(api, manager)
    section = await export_plugins()
    assert section is not None
    greeter_id = rows["greeter"]["instance_id"]
    local_id = rows["local"]["instance_id"]
    for iid in (greeter_id, local_id):
        assert (await api.delete(f"/api/plugins/{iid}?keep_data=true")).status_code == 204

    greeter = _entries(section)["meshloom.greeter"]
    # Another plugin claiming the local instance's id (and so its kept data).
    claim = {**greeter, "plugin_id": "meshloom.echo", "instance_id": local_id}
    claim["manifest"] = {**claim["manifest"], "id": "meshloom.echo"}
    await restore_sections({PLUGINS_SECTION: BackupSection(data={"instances": [greeter, claim]})})
    restored = {row["plugin_id"]: row for row in await PluginRepository.get_all()}
    assert restored["meshloom.greeter"]["instance_id"] == greeter_id
    assert await PluginKvRepository.get(greeter_id, "state") == {"note": KV_MARKER}
    echo = restored["meshloom.echo"]
    assert echo["instance_id"] != local_id
    assert await PluginKvRepository.get(echo["instance_id"], "state") is None
    async with backup_db.readonly() as conn:
        async with conn.execute("SELECT instance_id FROM plugin_kept_data") as cursor:
            assert [r[0] for r in await cursor.fetchall()] == [local_id]


async def test_a_bad_entry_is_dropped_on_its_own(backup_db, api, manager):
    rows = await _populate(api, manager)
    section = await export_plugins()
    assert section is not None
    good = _entries(section)[rows["local"]["plugin_id"]]
    await _wipe()
    wrong_id = {**good, "instance_id": str(uuid.uuid4()), "plugin_id": "local.other-abcdef"}
    no_source = {**good, "instance_id": str(uuid.uuid4()), "source": None}
    bad_schema = {**good, "instance_id": str(uuid.uuid4()), "config_schema": {"type": 3}}
    bad_python = {**good, "instance_id": str(uuid.uuid4()), "source": "def broken(:\n"}
    leaked = {
        **good,
        "instance_id": str(uuid.uuid4()),
        # A file written by hand with the mask or a secret in config: never stored.
        "config": {"city": "Lyon", "api_key": "hand-written"},
    }
    entries = [
        good,
        good,
        "junk",
        {"plugin_id": "local.x-abcdef"},
        wrong_id,
        no_source,
        bad_schema,
        bad_python,
    ]
    warnings = await restore_sections(
        {PLUGINS_SECTION: BackupSection(data={"instances": [*entries, leaked]})}
    )
    dropped = [w for w in warnings if w.startswith("plugin_dropped:")]
    assert len(dropped) == 7
    assert "plugin_dropped:invalid" in dropped
    assert "plugins_restored:2" in warnings
    restored = await PluginRepository.get_all()
    assert len(restored) == 2
    assert all(row["enabled"] is False for row in restored)
    assert not any("hand-written" in json.dumps(row) for row in restored)
    async with backup_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM plugin_secrets") as cursor:
            assert (await cursor.fetchone())[0] == 0


async def test_a_file_without_the_section_changes_nothing(backup_db, api, manager):
    await _populate(api, manager)
    before = await PluginRepository.get_all()
    assert await restore_sections({}) == []
    assert await PluginRepository.get_all() == before


async def test_restore_names_the_supervisor_network_in_the_addon(
    backup_db, api, manager, monkeypatch, caplog
):
    """MSHL-355: an entry whose ``lan_hosts`` lists a supervisor-network address is dropped
    (fail closed) in the add-on with its own warning; elsewhere it is restored."""
    rows = await _populate(api, manager)
    section = await export_plugins()
    assert section is not None
    good = _entries(section)[rows["local"]["plugin_id"]]
    await _wipe()
    http = {"hosts": ["api.example.org"], "lan_hosts": ["172.30.32.2"]}
    manifest = {**good["manifest"], "permissions": {"http": http}}
    entry = {**good, "instance_id": str(uuid.uuid4()), "manifest": manifest}
    other = {**entry, "instance_id": str(uuid.uuid4())}
    other["manifest"] = {**manifest, "permissions": {"http": {"lan_hosts": ["10.0.0.1x"]}}}
    data = BackupSection(data={"instances": [entry, other]})
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "addon")
    warnings = await restore_sections({PLUGINS_SECTION: data})
    pid = good["plugin_id"]
    assert f"plugin_dropped_supervisor:{pid}" in warnings
    assert f"plugin_dropped:{pid}" in warnings  # the other manifest problem: generic reason
    assert not any(w.startswith("plugins_restored") for w in warnings)
    assert await PluginRepository.get_all() == []
    assert "172.30.32.2" not in caplog.text
    monkeypatch.setenv("MESHLOOM_INSTALL_KIND", "package")
    warnings = await restore_sections({PLUGINS_SECTION: BackupSection(data={"instances": [entry]})})
    assert "plugins_restored:1" in warnings
