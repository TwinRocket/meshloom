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


def _write_tree(
    root: Path,
    version: str,
    extra_permissions: dict | None = None,
    privacy: dict | None = None,
) -> None:
    target = root / "echo"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(ECHO_SRC, target)
    manifest = json.loads((target / "manifest.json").read_text())
    manifest["id"] = "meshloom.echo"
    manifest["version"] = version
    manifest["config_schema"] = "config.schema.json"
    manifest["permissions"] = {**manifest["permissions"], **(extra_permissions or {})}
    if privacy is not None:
        manifest["privacy"] = privacy
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


# --- MSHL-345 ----------------------------------------------------------------------

READ = {"channels": "public", "direct": True, "outgoing": False}


@pytest.mark.parametrize(
    "params",
    [
        {**READ, "channels": "all"},  # broadened: every channel, same name
        {**READ, "direct": False},  # narrowed: still a different consent
    ],
)
async def test_changed_params_of_the_same_permission_hold(env, params):
    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, "1.1.0", {"messages.read": params})
    await update_bundled()
    after = await PluginRepository.get(row["instance_id"])
    assert after["version"] == "1.1.0" and _disk_version(manager) == "1.1.0"
    assert after["status"] == "needs_attention"
    assert after["attention"]["type"] == "review_permissions"
    assert after["attention"]["added"] == ["messages.read"]
    assert after["attention"]["privacy_widened"] is False
    # The old, narrower grant is not carried over to the wider declaration.
    assert "messages.read" not in after["granted_permissions"]
    assert "messages.send" in after["granted_permissions"]


@pytest.mark.parametrize(
    ("privacy", "held"),
    [
        ({"sends_to_third_parties": ["api.example.com"], "stores_message_content": False}, True),
        ({"sends_to_third_parties": [], "stores_message_content": True}, True),
    ],
)
async def test_widened_privacy_declaration_holds_and_keeps_grants(env, privacy, held):
    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, "1.1.0", privacy=privacy)
    await update_bundled()
    after = await PluginRepository.get(row["instance_id"])
    assert after["version"] == "1.1.0"
    assert after["status"] == "needs_attention"
    assert after["attention"]["type"] == "review_permissions"
    assert after["attention"]["added"] == []
    assert after["attention"]["privacy_widened"] is held
    assert after["granted_permissions"] == row["granted_permissions"]


async def test_narrowed_privacy_declaration_does_not_hold(env):
    bundled, manager = env
    _write_tree(
        bundled,
        "1.0.0",
        privacy={"sends_to_third_parties": ["api.example.com"], "stores_message_content": True},
    )
    row = await _install(manager)
    _write_tree(bundled, "1.1.0")
    await update_bundled()
    after = await PluginRepository.get(row["instance_id"])
    assert after["version"] == "1.1.0"
    assert after["status"] == "installed" and after["attention"] is None


async def test_failed_row_write_puts_the_old_tree_back(env, monkeypatch):
    bundled, manager = env
    row = await _install(manager)
    other = await PluginRepository.create(
        plugin_id="meshloom.echo",
        label="Second echo",
        kind="bundled",
        version=row["version"],
        manifest=row["manifest"],
        granted_permissions=row["granted_permissions"],
    )
    _write_tree(bundled, "1.1.0")

    async def broken(_updates):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(PluginRepository, "update_many", broken)
    await update_bundled()
    for instance_id in (row["instance_id"], other["instance_id"]):
        assert (await PluginRepository.get(instance_id))["version"] == "1.0.0"
    assert _disk_version(manager) == "1.0.0"
    assert not (manager.plugins_root() / "_staging").exists()

    # The next start retries and succeeds.
    monkeypatch.undo()
    monkeypatch.setattr(installer, "BUNDLED_ROOT", bundled)
    monkeypatch.setattr("app.plugins.bundled_update.plugin_manager", manager)
    await update_bundled()
    for instance_id in (row["instance_id"], other["instance_id"]):
        assert (await PluginRepository.get(instance_id))["version"] == "1.1.0"
    assert _disk_version(manager) == "1.1.0"


async def test_update_many_is_all_or_nothing(test_db):
    a = await PluginRepository.create(
        plugin_id="test.a", label="a", kind="store", version="1.0.0", manifest={"hooks": []}
    )
    with pytest.raises(ValueError):
        await PluginRepository.update_many(
            {a["instance_id"]: {"version": "2.0.0"}, "other": {"not_a_field": 1}}
        )
    assert (await PluginRepository.get(a["instance_id"]))["version"] == "1.0.0"


async def test_failed_restore_keeps_the_old_tree_aside(env, monkeypatch):
    bundled, manager = env
    await _install(manager)
    _write_tree(bundled, "1.1.0")
    real_rename = Path.rename

    def broken(self, target):
        # Both the swap of the new tree and the restore of the old one fail.
        if self.parent.name == "_staging":
            raise OSError("rename failed")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", broken)
    with pytest.raises(OSError):
        installer.install_bundled(installer.list_bundled()["meshloom.echo"], manager.plugins_root())
    monkeypatch.setattr(Path, "rename", real_rename)
    staging = manager.plugins_root() / "_staging"
    kept = [p for p in staging.iterdir() if p.name.startswith("previous-")]
    assert len(kept) == 1
    assert json.loads((kept[0] / "manifest.json").read_text())["version"] == "1.0.0"
    # A later install leaves it alone and puts the new tree in place.
    installer.install_bundled(installer.list_bundled()["meshloom.echo"], manager.plugins_root())
    assert _disk_version(manager) == "1.1.0"
    assert kept[0].is_dir()


# --- MSHL-347 ----------------------------------------------------------------------


def _bundled() -> installer.BundledPlugin:
    return installer.list_bundled()["meshloom.echo"]


async def test_restore_previous_failure_keeps_the_new_tree_and_the_old_one_aside(env, monkeypatch):
    bundled, manager = env
    await _install(manager)
    _write_tree(bundled, "1.1.0")
    root = manager.plugins_root()
    previous = installer.swap_bundled(_bundled(), root)
    assert previous is not None and _disk_version(manager) == "1.1.0"
    real_rename = Path.rename

    def broken(self, target):
        if self == previous:
            raise OSError("restore failed")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", broken)
    with pytest.raises(OSError):
        installer.restore_previous(_bundled(), root, previous)
    monkeypatch.setattr(Path, "rename", real_rename)
    # The new tree went back in place, the old one stays aside, nothing else is left.
    assert _disk_version(manager) == "1.1.0"
    assert json.loads((previous / "manifest.json").read_text())["version"] == "1.0.0"
    assert [p.name for p in (root / "_staging").iterdir()] == [previous.name]


async def test_restore_previous_is_a_noop_without_an_old_tree(env):
    _, manager = env
    await _install(manager)
    installer.restore_previous(_bundled(), manager.plugins_root(), None)
    assert _disk_version(manager) == "1.0.0"


async def test_row_write_and_restore_both_failing_refuse_to_start(env, monkeypatch):
    from app.plugins.host import CODE_MISMATCH, PluginHostError

    bundled, manager = env
    row = await _install(manager)
    _write_tree(bundled, "1.1.0")
    real_write = PluginRepository.update_many

    async def broken_write(_updates):
        raise RuntimeError("database is locked")

    def broken_restore(*_args):
        raise OSError("restore failed")

    monkeypatch.setattr(PluginRepository, "update_many", broken_write)
    monkeypatch.setattr(installer, "restore_previous", broken_restore)
    await update_bundled()
    stored = await PluginRepository.get(row["instance_id"])
    assert stored["version"] == "1.0.0" and _disk_version(manager) == "1.1.0"
    assert not manager.code_matches(stored)

    # Fail closed: the new code never runs under the old manifest and grants.
    monkeypatch.setattr(manager, "disabled_source", lambda: None)
    with pytest.raises(PluginHostError) as refused:
        await manager.start(row["instance_id"])
    assert refused.value.code == CODE_MISMATCH
    assert manager.get(row["instance_id"]) is None
    status = manager.statuses()[row["instance_id"]]
    assert status["status"] == "error" and status["last_error"] == CODE_MISMATCH

    # The next start writes the rows (the tree already is the shipped version).
    monkeypatch.setattr(PluginRepository, "update_many", real_write)
    await update_bundled()
    stored = await PluginRepository.get(row["instance_id"])
    assert stored["version"] == "1.1.0" and manager.code_matches(stored)


async def test_code_matches_is_fail_closed(env):
    _, manager = env
    row = await _install(manager)
    assert manager.code_matches(row)
    assert not manager.code_matches({**row, "version": "9.9.9"})
    assert not manager.code_matches({**row, "plugin_id": "meshloom.other"})
    assert manager.code_matches({**row, "kind": "local"})  # local code is written per start
    manifest = manager.plugins_root() / "meshloom.echo" / "manifest.json"
    manifest.write_text("{not json")
    assert not manager.code_matches(row)
    manifest.unlink()
    assert not manager.code_matches(row)


def test_sweep_staging_removes_crash_leftovers_and_keeps_needed_old_trees(tmp_path):
    root = tmp_path / "plugins"
    staging = root / "_staging"
    (staging / "abc123" / "sub").mkdir(parents=True)  # a half-extracted tree
    (staging / "abc123" / "sub" / "f").write_text("x")
    (staging / "stray-file").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")
    (staging / "link").symlink_to(outside, target_is_directory=True)

    def old_tree(name: str, plugin_id: str) -> Path:
        tree = staging / name
        tree.mkdir()
        (tree / "manifest.json").write_text(json.dumps({"id": plugin_id, "version": "1.0.0"}))
        return tree

    replaced = old_tree("previous-1", "meshloom.echo")
    orphan = old_tree("previous-2", "meshloom.gone")
    bogus = old_tree("previous-3", "../escape")
    (root / "meshloom.echo").mkdir()
    (root / "meshloom.echo" / "manifest.json").write_text("{}")

    installer.sweep_staging(root)

    assert sorted(p.name for p in staging.iterdir()) == [orphan.name, bogus.name]
    assert not replaced.exists()
    assert (outside / "keep").is_file()  # the symlink went, never followed


def test_sweep_staging_leaves_a_symlinked_staging_alone(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "abc").mkdir(parents=True)
    (root / "_staging").symlink_to(elsewhere, target_is_directory=True)
    installer.sweep_staging(root)
    assert (elsewhere / "abc").is_dir()
    installer.sweep_staging(tmp_path / "missing")  # no plugins dir: nothing to do


async def test_startup_sweeps_the_staging_directory(env, monkeypatch):
    _, manager = env
    monkeypatch.setattr("app.plugins.lifecycle.plugin_manager", manager)
    monkeypatch.setattr("app.plugins.isolation.startup", lambda *_a, **_k: None)
    leftover = manager.plugins_root() / "_staging" / "deadbeef"
    leftover.mkdir(parents=True)

    async def load() -> None:
        return None

    monkeypatch.setattr(manager, "load_from_db", load)
    await lifecycle.start()
    assert not (manager.plugins_root() / "_staging").exists()
