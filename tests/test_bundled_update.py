"""Bundled plugins update in place at start (MSHL-337, ``app/plugins/bundled_update.py``)."""

import json
import shutil
import sys
from pathlib import Path

import pytest

from app.config import settings
from app.plugins import installer, lifecycle
from app.plugins.bundled_update import update_bundled
from app.plugins.host import Budgets, PluginManager
from app.repository.plugins import PluginKvRepository, PluginRepository

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

ECHO_SRC = Path(__file__).parent / "plugin_dev" / "echo"
SCHEMA = {
    "type": "object",
    "properties": {"greeting": {"type": "string"}, "token": {"type": "string", "writeOnly": True}},
}


def _write_tree(root: Path, version: str, extra_permissions: dict | None = None) -> None:
    target = root / "echo"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ECHO_SRC, target)
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["id"] = "meshloom.echo"
    manifest["version"] = version
    manifest["config_schema"] = "config.schema.json"
    manifest["permissions"] = {**manifest["permissions"], **(extra_permissions or {})}
    (target / "config.schema.json").write_text(json.dumps(SCHEMA))
    (target / "manifest.json").write_text(json.dumps(manifest))


@pytest.fixture
def env(test_db, tmp_path, monkeypatch):
    bundled = tmp_path / "bundled"
    _write_tree(bundled, "1.0.0")
    monkeypatch.setattr(installer, "BUNDLED_ROOT", bundled)
    manager = PluginManager(
        Budgets(stagger=0.01), plugins_root=tmp_path / "plugins", cpu_reader=None
    )
    monkeypatch.setattr("app.plugins.bundled_update.plugin_manager", manager)
    return bundled, manager


async def _install(manager: PluginManager) -> dict:
    bundled = installer.list_bundled()["meshloom.echo"]
    installer.install_bundled(bundled, manager.plugins_root())
    manifest = bundled.package.manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    manifest["permissions"] = bundled.package.manifest.permissions.declared()
    row = await PluginRepository.create(
        plugin_id="meshloom.echo",
        label="My echo",
        kind="bundled",
        version=manifest["version"],
        manifest=manifest,
        config_schema=bundled.config_schema,
        config={"greeting": "hi", "token": "s3cret-value"},
        scope={"messages": "all"},
        granted_permissions=manifest["permissions"],
        enabled=True,
    )
    await PluginKvRepository.set(row["instance_id"], "k", {"n": 1})
    return row


def _disk_version(manager: PluginManager) -> str:
    path = manager.plugins_root() / "meshloom.echo" / "manifest.json"
    return json.loads(path.read_text())["version"]


async def test_same_permissions_updates_and_keeps_running(env):
    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, "1.1.0")
    await update_bundled()
    after = await PluginRepository.get(row["instance_id"])
    assert after["version"] == "1.1.0"
    assert after["manifest"]["version"] == "1.1.0"
    assert _disk_version(manager) == "1.1.0"
    assert after["enabled"] and after["status"] == "installed" and after["attention"] is None
    assert after["granted_permissions"] == row["granted_permissions"]
    assert after["config"]["greeting"] == "hi" and after["scope"] == {"messages": "all"}
    assert await PluginRepository.config_for_init(row["instance_id"]) is not None
    assert (await PluginRepository.config_for_init(row["instance_id"]))["token"] == "s3cret-value"
    assert await PluginKvRepository.get(row["instance_id"], "k") == {"n": 1}
    assert after["label"] == "My echo"


async def test_added_permission_updates_and_holds(env):
    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, "1.1.0", {"contacts.read": {}})
    await update_bundled()
    after = await PluginRepository.get(row["instance_id"])
    assert after["version"] == "1.1.0" and _disk_version(manager) == "1.1.0"
    assert after["status"] == "needs_attention"
    assert after["attention"]["type"] == "review_permissions"
    assert after["attention"]["added"] == ["contacts.read"]
    assert "contacts.read" not in after["granted_permissions"]
    assert "messages.read" in after["granted_permissions"]
    assert after["enabled"]  # accepting through PATCH starts it
    assert after["config"]["greeting"] == "hi"
    assert await PluginKvRepository.get(row["instance_id"], "k") == {"n": 1}


@pytest.mark.parametrize("packaged", ["1.0.0", "0.9.0"])
async def test_same_or_older_is_a_noop(env, packaged):
    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, packaged)
    await update_bundled()
    after = await PluginRepository.get(row["instance_id"])
    assert after["version"] == "1.0.0" and _disk_version(manager) == "1.0.0"
    assert after["updated_at"] == row["updated_at"] and after["attention"] is None


async def test_update_is_idempotent(env):
    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, "1.1.0", {"contacts.read": {}})
    await update_bundled()
    first = await PluginRepository.get(row["instance_id"])
    await update_bundled()
    assert await PluginRepository.get(row["instance_id"]) == first


async def test_corrupt_package_keeps_old_version(env, caplog):
    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, "1.1.0")
    (bundled / "echo" / "manifest.json").write_text("{not json")
    await update_bundled()
    after = await PluginRepository.get(row["instance_id"])
    assert after["version"] == "1.0.0" and _disk_version(manager) == "1.0.0"
    assert after["status"] == "installed"


async def test_failed_install_keeps_old_tree(env, monkeypatch):
    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, "1.1.0")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(installer, "extract_package", boom)
    await update_bundled()
    after = await PluginRepository.get(row["instance_id"])
    assert after["version"] == "1.0.0" and _disk_version(manager) == "1.0.0"


async def test_failed_swap_restores_the_old_tree(env, monkeypatch):
    bundled, manager = env
    await _install(manager)
    _write_tree(bundled, "1.1.0")
    real_rename = Path.rename

    def flaky(self, target):
        if self.parent.name == "_staging" and not self.name.startswith("previous-"):
            raise OSError("swap failed")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    with pytest.raises(OSError):
        installer.install_bundled(installer.list_bundled()["meshloom.echo"], manager.plugins_root())
    monkeypatch.setattr(Path, "rename", real_rename)
    assert _disk_version(manager) == "1.0.0"


async def test_startup_continues_after_a_broken_update(env, monkeypatch):
    bundled, manager = env
    monkeypatch.setattr("app.plugins.lifecycle.plugin_manager", manager)
    monkeypatch.setattr("app.plugins.isolation.startup", lambda *_a, **_k: None)
    await _install(manager)
    _write_tree(bundled, "1.1.0")
    (bundled / "echo" / "plugin.py").unlink()
    started: list[bool] = []

    async def load() -> None:
        started.append(True)

    monkeypatch.setattr(manager, "load_from_db", load)
    await lifecycle.start()
    assert started == [True] and _disk_version(manager) == "1.0.0"


async def test_host_lock_skips_the_update(env, monkeypatch):
    bundled, manager = env
    monkeypatch.setattr("app.plugins.lifecycle.plugin_manager", manager)
    row = await _install(manager)
    _write_tree(bundled, "1.1.0")
    monkeypatch.setattr(settings, "disable_plugins", True)
    await lifecycle.start()
    assert (await PluginRepository.get(row["instance_id"]))["version"] == "1.0.0"
    assert _disk_version(manager) == "1.0.0"


async def test_frozen_and_reinstall_rows_are_left_alone(env):
    bundled, manager = env
    row = await _install(manager)
    await PluginRepository.update(row["instance_id"], status="revoked")
    _write_tree(bundled, "1.1.0")
    await update_bundled()
    assert (await PluginRepository.get(row["instance_id"]))["version"] == "1.0.0"
