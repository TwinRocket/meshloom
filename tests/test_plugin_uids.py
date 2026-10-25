"""Per-instance uids at the `setuid` level (MSHL-339): allocation, purge, release.

The backend is faked (no uid switch): what is checked is the bookkeeping in
`PluginUidRepository` and `PluginManager`. The real uid switch, the kill and
purge helpers run in tests/test_plugin_isolation.py where `setuid` is available.
"""

import asyncio
import subprocess
import threading
from pathlib import Path

import pytest

from app.plugins import host as host_module
from app.plugins import isolation
from app.plugins.host import PluginHostError, PluginManager
from app.plugins.isolation import setuid as setuid_module
from app.plugins.isolation.none import NoneBackend
from app.plugins.isolation.selection import Selection
from app.plugins.isolation.setuid import SetuidBackend
from app.repository.plugins import PluginRepository, PluginUidRepository


class FakeSetuid(SetuidBackend):
    purged: list[int]
    fail: set[int]

    def for_uid(self, uid: int) -> "FakeSetuid":
        bound = FakeSetuid(uid, uid)
        bound.purged, bound.fail = self.purged, self.fail
        return bound

    blocked: dict[int, tuple[str, ...]] = {}

    def purge(self, dirs=None) -> None:
        if self.uid in self.blocked:
            raise setuid_module.PurgeBlockedError(
                "plugin helper (purge) failed: not listable", self.blocked[self.uid]
            )
        if self.uid in self.fail:
            raise ValueError("plugin helper (purge) failed")
        self.purged.append(self.uid)


@pytest.fixture
def fake_setuid(monkeypatch):
    backend = FakeSetuid(10002, 10002)
    backend.purged, backend.fail = [], set()
    monkeypatch.setattr(FakeSetuid, "blocked", {})
    monkeypatch.setattr(isolation, "current", lambda: Selection(backend))
    monkeypatch.setattr(setuid_module, "uid_is_free", lambda uid: uid != 40001)
    return backend


async def _instance(label: str = "echo") -> str:
    row = await PluginRepository.create(
        plugin_id=f"test.{label}",
        label=label,
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": []},
    )
    return row["instance_id"]


async def test_each_instance_gets_its_own_stable_uid(test_db, fake_setuid, tmp_path: Path):
    manager = PluginManager(plugins_root=tmp_path)
    a, b, c = await _instance("a"), await _instance("b"), await _instance("c")
    first = await manager._instance_backend(a)
    assert first.uid == first.gid == 40000
    # 40001 has an account (not free): skipped.
    assert (await manager._instance_backend(b)).uid == 40002
    assert (await manager._instance_backend(a)).uid == 40000  # kept, not reallocated
    assert (await manager._instance_backend(c)).uid == 40003
    assert await PluginUidRepository.all() == {a: 40000, b: 40002, c: 40003}


async def test_no_free_uid_refuses_the_start(test_db, fake_setuid, monkeypatch, tmp_path):
    monkeypatch.setattr(setuid_module, "INSTANCE_UIDS", range(40000, 40001))
    manager = PluginManager(plugins_root=tmp_path)
    await manager._instance_backend(await _instance("a"))
    with pytest.raises(PluginHostError) as exc:
        await manager._instance_backend(await _instance("b"))
    assert exc.value.code == "uid_exhausted"


async def test_uninstall_purges_then_frees_the_uid(test_db, fake_setuid, tmp_path):
    manager = PluginManager(plugins_root=tmp_path)
    a, b = await _instance("a"), await _instance("b")
    await manager._instance_backend(a)
    await manager.remove(a, purge=True)
    assert fake_setuid.purged == [40000]
    assert await PluginUidRepository.get(a) is None
    # The freed uid goes to the next instance, clean.
    assert (await manager._instance_backend(b)).uid == 40000


async def test_a_failed_purge_keeps_the_uid_taken(test_db, fake_setuid, tmp_path):
    manager = PluginManager(plugins_root=tmp_path)
    a, b = await _instance("a"), await _instance("b")
    await manager._instance_backend(a)
    fake_setuid.fail.add(40000)
    await manager.remove(a, purge=True)
    await PluginRepository.delete(a)
    assert await PluginUidRepository.orphans() == {a: 40000}
    assert (await manager._instance_backend(b)).uid == 40002

    # The next startup tries again.
    fake_setuid.fail.clear()
    await manager.reap_uids()
    assert await PluginUidRepository.orphans() == {}
    assert 40000 in fake_setuid.purged


async def test_a_stop_without_purge_keeps_the_uid(test_db, fake_setuid, tmp_path):
    manager = PluginManager(plugins_root=tmp_path)
    a = await _instance("a")
    await manager._instance_backend(a)
    await manager.remove(a)
    assert fake_setuid.purged == []
    assert await PluginUidRepository.get(a) == 40000


async def test_startup_purges_leftovers_and_the_shared_uid(
    test_db, fake_setuid, monkeypatch, tmp_path
):
    manager = PluginManager(plugins_root=tmp_path)
    a, b = await _instance("a"), await _instance("b")
    await manager._instance_backend(a)
    await manager._instance_backend(b)
    owners = {40002, 10002}  # b crashed and left a work dir; so did the shared uid
    monkeypatch.setattr(setuid_module, "owns_something", lambda uid, dirs=None: uid in owners)
    await manager.reap_uids()
    assert sorted(fake_setuid.purged) == [10002, 40002]
    assert await PluginUidRepository.all() == {a: 40000, b: 40002}


async def test_blocking_dirs_reach_health_until_a_purge_succeeds(
    test_db, fake_setuid, monkeypatch, tmp_path
):
    """MSHL-349: a directory the purge cannot list keeps the uid; health names it
    (admins only) until the uid is purged cleanly."""
    from app.plugins import lifecycle

    broadcasts: list[bool] = []
    monkeypatch.setattr(host_module, "_broadcast_health", lambda: broadcasts.append(True))
    manager = PluginManager(plugins_root=tmp_path)
    monkeypatch.setattr(lifecycle, "plugin_manager", manager)
    a, b = await _instance("a"), await _instance("b")
    await manager._instance_backend(a)
    await manager._instance_backend(b)
    FakeSetuid.blocked.update({40000: ("/tmp/x",), 40002: ("/tmp/x", "/tmp/y")})
    await manager.remove(a, purge=True)
    await manager.remove(b, purge=True)
    assert await PluginUidRepository.get(a) == 40000  # kept
    assert manager.purge_blocked() == ["/tmp/x", "/tmp/y"]
    assert broadcasts == [True, True]  # once per change
    health = lifecycle.health_fields()
    assert health["plugins_purge_blocked"] == ["/tmp/x", "/tmp/y"]
    assert lifecycle.without_plugin_errors(health)["plugins_purge_blocked"] == []

    # The administrator removed /tmp/y: the next attempt frees b, /tmp/x stays.
    del FakeSetuid.blocked[40002]
    await PluginRepository.delete(a)
    await PluginRepository.delete(b)
    await manager.reap_uids()
    assert manager.purge_blocked() == ["/tmp/x"]
    assert await PluginUidRepository.orphans() == {a: 40000}
    FakeSetuid.blocked.clear()
    await manager.reap_uids()
    assert manager.purge_blocked() == []
    assert lifecycle.health_fields()["plugins_purge_blocked"] == []
    assert len(broadcasts) == 4


async def test_a_helper_timeout_keeps_the_uid(test_db, fake_setuid, monkeypatch, tmp_path):
    """MSHL-349: a purge helper that hangs past its timeout keeps the uid taken."""

    def hang(self, dirs=None):
        raise subprocess.TimeoutExpired(["python"], setuid_module.HELPER_TIMEOUT)

    manager = PluginManager(plugins_root=tmp_path)
    a = await _instance("a")
    await manager._instance_backend(a)
    monkeypatch.setattr(FakeSetuid, "purge", hang)
    await manager.remove(a, purge=True)
    assert await PluginUidRepository.get(a) == 40000  # kept
    await PluginRepository.delete(a)
    await manager.reap_uids()  # no crash at startup either
    assert await PluginUidRepository.orphans() == {a: 40000}
    assert manager.purge_blocked() == []


async def test_other_levels_allocate_nothing(test_db, monkeypatch, tmp_path):
    backend = NoneBackend()
    monkeypatch.setattr(isolation, "current", lambda: Selection(backend))
    manager = PluginManager(plugins_root=tmp_path)
    a = await _instance("a")
    assert await manager._instance_backend(a) is backend
    await manager.remove(a, purge=True)
    await manager.reap_uids()
    assert await PluginUidRepository.all() == {}


def test_host_signals_through_the_instance_backend():
    sent: list[tuple[int, int]] = []

    class Recorder(NoneBackend):
        def signal_group(self, pgid, sig, *, wait=False):
            sent.append((pgid, sig))

    class Proc:
        pid = 4321
        returncode = None

    process = host_module.PluginProcess(
        Path("/nonexistent"), host_module.LogRing(1024, 128), None, backend=Recorder()
    )
    process._proc = Proc()
    process.kill("stopped", 15)
    assert sent == [(4321, 15)]
    assert process.kill_reason == "stopped"


async def test_the_free_uid_check_runs_off_the_event_loop(test_db):
    """`getpwuid` may ask LDAP: it must not block the loop under the write lock."""
    loop_thread = threading.current_thread()
    seen: list[threading.Thread] = []

    def is_free(uid: int) -> bool:
        seen.append(threading.current_thread())
        return True

    a = await _instance("a")
    assert await PluginUidRepository.allocate(a, range(40000, 40003), is_free) == 40000
    assert seen and all(thread is not loop_thread for thread in seen)


async def test_a_uid_taken_meanwhile_is_skipped(test_db):
    """The pick happens outside the transaction: a row that took it in between
    makes the allocation go on to the next free uid, never share one."""
    a, b = await _instance("a"), await _instance("b")
    raced = False

    def is_free(uid: int) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            # Another instance takes 40000 between the pick and the insert.
            asyncio.run_coroutine_threadsafe(
                PluginUidRepository.allocate(b, [40000], lambda _uid: True), loop
            ).result(timeout=10)
        return True

    loop = asyncio.get_running_loop()
    assert await PluginUidRepository.allocate(a, range(40000, 40003), is_free) == 40001
    assert await PluginUidRepository.all() == {a: 40001, b: 40000}


async def test_a_pick_lost_to_the_same_instance_is_released(test_db):
    """MSHL-359: a concurrent call stored another uid for this instance between the
    pick (which took its lease) and the insert: the pick is given back. A pick
    another instance stored is not: that row owns it now."""
    a, b = await _instance("a"), await _instance("b")
    released: list[int] = []
    raced = False

    def is_free(uid: int) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            # The same instance gets 40005 from a concurrent start.
            asyncio.run_coroutine_threadsafe(
                PluginUidRepository.allocate(a, [40005], lambda _uid: True), loop
            ).result(timeout=10)
        return True

    loop = asyncio.get_running_loop()
    uid = await PluginUidRepository.allocate(
        a, range(40000, 40003), is_free, release=released.append
    )
    assert uid == 40005
    assert released == [40000]

    # Lost to another instance: no release (its lease is that row's).
    raced = False

    def other_takes_it(uid: int) -> bool:
        nonlocal raced
        if not raced:
            raced = True
            asyncio.run_coroutine_threadsafe(
                PluginUidRepository.allocate(b, [40000], lambda _uid: True), loop
            ).result(timeout=10)
        return True

    c = await _instance("c")
    released.clear()
    assert (
        await PluginUidRepository.allocate(
            c, range(40000, 40003), other_takes_it, release=released.append
        )
        == 40001
    )
    assert released == []


async def test_concurrent_allocations_never_share_a_uid(test_db):
    ids = [await _instance(f"i{n}") for n in range(6)]
    uids = await asyncio.gather(
        *(PluginUidRepository.allocate(i, range(40000, 40010), lambda _uid: True) for i in ids)
    )
    assert sorted(uids) == list(range(40000, 40006))
