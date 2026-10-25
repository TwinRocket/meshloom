"""Host-wide leases of the per-instance uids at the `setuid` level (MSHL-357).

Two Meshloom installs on one kernel share the uid space: each install's leases
live in the shared temp dirs (here tmp dirs standing in for them), so another
install never takes, nor purges, a uid this one holds. No uid switch is needed.
"""

import os
from pathlib import Path

import pytest

from app.plugins import isolation
from app.plugins.host import PluginHostError, PluginManager
from app.plugins.isolation import setuid as setuid_module
from app.plugins.isolation import uid_lease
from app.plugins.isolation.selection import Selection
from app.plugins.isolation.setuid import PurgeBlockedError, SetuidBackend
from app.plugins.isolation.uid_lease import LEASE_DIR_NAME, UidLeaseError, UidLeases, UidLeaseTaken
from app.repository.plugins import PluginRepository, PluginUidRepository

A = "a" * 32
B = "b" * 32


@pytest.fixture
def domains(tmp_path: Path) -> list[Path]:
    out = [tmp_path / "tmp", tmp_path / "shm"]
    for path in out:
        path.mkdir()
    return out


def _leases(install: str, domains: list[Path]) -> UidLeases:
    return UidLeases(install, lambda: domains)


def _backend(install_dir: Path, domains: list[Path], monkeypatch) -> SetuidBackend:
    """A `setuid` backend as `prepare_data_dir` leaves it, without the file model."""
    monkeypatch.setattr(setuid_module, "lease_domains", lambda: domains)
    install_dir.mkdir(exist_ok=True)
    backend = SetuidBackend(10002, 10002)
    backend.data_dir = install_dir
    backend.leases = UidLeases(uid_lease.install_id(install_dir), setuid_module.lease_domains)
    return backend


# The install id.


def test_the_install_id_is_created_once_and_private(tmp_path: Path):
    first = uid_lease.install_id(tmp_path)
    assert len(first) == 32 and int(first, 16) >= 0
    assert uid_lease.install_id(tmp_path) == first
    assert (tmp_path / uid_lease.INSTALL_ID_FILE).stat().st_mode & 0o777 == 0o600
    other = tmp_path / "other"
    other.mkdir()
    assert uid_lease.install_id(other) != first


def test_a_malformed_install_id_fails_closed(tmp_path: Path):
    (tmp_path / uid_lease.INSTALL_ID_FILE).write_text("not-an-id\n")
    with pytest.raises(ValueError):
        uid_lease.install_id(tmp_path)


# Leases between installs.


def test_two_installs_never_hold_the_same_uid(domains):
    a, b = _leases(A, domains), _leases(B, domains)
    a.acquire(40000)
    with pytest.raises(UidLeaseTaken) as exc:
        b.acquire(40000)
    assert str(domains[0] / LEASE_DIR_NAME / "40000") in exc.value.paths
    b.acquire(40001)
    for domain in domains:
        assert (domain / LEASE_DIR_NAME / "40000").read_text().strip() == A
        assert (domain / LEASE_DIR_NAME / "40001").read_text().strip() == B
    a.acquire(40000)  # idempotent


def test_a_lease_taken_in_one_domain_only_is_refused_and_rolled_back(domains):
    """Another install leased the uid in the second domain only (its first was
    cleared): the lease this call wrote in the first domain goes."""
    other = _leases(B, domains[1:])
    other.acquire(40000)
    a = _leases(A, domains)
    with pytest.raises(UidLeaseTaken):
        a.acquire(40000)
    assert not (domains[0] / LEASE_DIR_NAME / "40000").exists()
    assert (domains[1] / LEASE_DIR_NAME / "40000").read_text().strip() == B


def test_release_frees_the_uid_for_another_install(domains):
    a, b = _leases(A, domains), _leases(B, domains)
    a.acquire(40000)
    a.release(40000)
    assert not (domains[0] / LEASE_DIR_NAME / "40000").exists()
    b.acquire(40000)


def test_a_running_copy_of_the_same_install_is_refused(domains):
    """The same data dir copied and started twice: the lock decides, not the id."""
    a, copy = _leases(A, domains), _leases(A, domains)
    a.acquire(40000)
    with pytest.raises(UidLeaseTaken, match="running"):
        copy.acquire(40000)


def test_a_stale_lease_comes_back_to_its_install_after_a_crash(domains):
    a = _leases(A, domains)
    a.acquire(40000)
    a.close()  # what a crash does: locks gone, files kept
    assert (domains[0] / LEASE_DIR_NAME / "40000").exists()
    with pytest.raises(UidLeaseTaken):
        _leases(B, domains).acquire(40000)  # still not another install's
    restarted = _leases(A, domains)
    restarted.acquire(40000)
    restarted.release(40000)
    assert not (domains[0] / LEASE_DIR_NAME / "40000").exists()


def test_a_cleared_temp_dir_gets_its_leases_written_again(domains):
    """A reboot (tmpfs) or a cleanup removed the lease directory: the next
    acquire of a held uid writes it again."""
    a = _leases(A, domains)
    a.acquire(40000)
    lease_dir = domains[0] / LEASE_DIR_NAME
    (lease_dir / "40000").unlink()
    lease_dir.rmdir()
    a.acquire(40000)
    assert (lease_dir / "40000").read_text().strip() == A
    with pytest.raises(UidLeaseTaken):
        _leases(B, domains).acquire(40000)


@pytest.mark.parametrize("mode", [0o770, 0o707])
def test_a_lease_directory_others_can_write_fails_closed(domains, mode):
    lease_dir = domains[0] / LEASE_DIR_NAME
    lease_dir.mkdir(mode=0o700)
    lease_dir.chmod(mode)
    with pytest.raises(UidLeaseError) as exc:
        _leases(A, domains).acquire(40000)
    assert not isinstance(exc.value, UidLeaseTaken)
    assert exc.value.paths == (str(lease_dir),)


def test_a_symlinked_lease_directory_fails_closed(domains, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir(mode=0o700)
    (domains[0] / LEASE_DIR_NAME).symlink_to(target)
    with pytest.raises(UidLeaseError):
        _leases(A, domains).acquire(40000)
    assert list(target.iterdir()) == []


@pytest.mark.skipif(os.geteuid() != 0, reason="chown needs root")
def test_a_lease_directory_of_another_owner_fails_closed(domains):
    lease_dir = domains[0] / LEASE_DIR_NAME
    lease_dir.mkdir(mode=0o700)
    os.chown(lease_dir, 40123, 40123)
    with pytest.raises(UidLeaseError):
        _leases(A, domains).acquire(40000)


# The backend.


def test_each_install_starts_its_search_at_its_own_offset(tmp_path, domains, monkeypatch):
    for name, value in (("a", "00000007" + "0" * 24), ("b", "0000270f" + "0" * 24)):
        (tmp_path / name).mkdir()
        (tmp_path / name / uid_lease.INSTALL_ID_FILE).write_text(value + "\n")
    a = _backend(tmp_path / "a", domains, monkeypatch)
    b = _backend(tmp_path / "b", domains, monkeypatch)
    ca, cb = a.uid_candidates(), b.uid_candidates()
    assert sorted(ca) == sorted(cb) == list(setuid_module.INSTANCE_UIDS)
    assert (ca[0], ca[-1]) == (40007, 40006)
    assert (cb[0], cb[1]) == (49999, 40000)
    assert SetuidBackend(10002, 10002).uid_candidates() == setuid_module.INSTANCE_UIDS


def test_the_purge_refuses_a_uid_another_install_leased(tmp_path, domains, monkeypatch):
    other = _backend(tmp_path / "other", domains, monkeypatch)
    other.claim_uid(40000)
    mine = _backend(tmp_path / "mine", domains, monkeypatch).for_uid(40000)
    helper_calls: list[str] = []
    monkeypatch.setattr(SetuidBackend, "_helper", lambda self, op, *a: helper_calls.append(op))
    with pytest.raises(PurgeBlockedError) as exc:
        mine.purge(domains)
    assert helper_calls == []  # nothing removed
    assert str(domains[0] / LEASE_DIR_NAME / "40000") in exc.value.paths


# Two installs through the host (two databases, one kernel).


@pytest.fixture
def leasing_setuid(tmp_path, domains, monkeypatch):
    """Install "mine" under test; `other` is a second install on the same kernel."""
    monkeypatch.setattr(setuid_module, "uid_is_free", lambda uid: True)
    monkeypatch.setattr(setuid_module, "owns_something", lambda uid, dirs=None: False)
    mine = _backend(tmp_path / "mine", domains, monkeypatch)
    other = _backend(tmp_path / "other", domains, monkeypatch)
    # Same starting point for both: the leases alone must keep them apart.
    monkeypatch.setattr(SetuidBackend, "uid_candidates", lambda self: range(40000, 40004))
    monkeypatch.setattr(isolation, "current", lambda: Selection(mine))
    return mine, other


async def _instance(label: str) -> str:
    row = await PluginRepository.create(
        plugin_id=f"test.{label}",
        label=label,
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": []},
    )
    return row["instance_id"]


async def test_two_installs_get_different_uids(test_db, leasing_setuid, tmp_path):
    mine, other = leasing_setuid
    other.claim_uid(40000)  # the other install's first instance
    manager = PluginManager(plugins_root=tmp_path / "plugins")
    a = await _instance("a")
    assert (await manager._instance_backend(a)).uid == 40001
    assert await PluginUidRepository.all() == {a: 40001}


async def test_a_stored_uid_another_install_leased_refuses_the_start(
    test_db, leasing_setuid, tmp_path
):
    """A row from before the leases (both installs allocated 40000): whoever
    leases it first keeps it, the other refuses to start that instance."""
    mine, other = leasing_setuid
    manager = PluginManager(plugins_root=tmp_path / "plugins")
    a = await _instance("a")
    await PluginUidRepository.allocate(a, [40000], lambda _uid: True)
    other.claim_uid(40000)
    with pytest.raises(PluginHostError) as exc:
        await manager._instance_backend(a)
    assert exc.value.code == "uid_leased"


async def test_startup_never_purges_another_installs_uid(
    test_db, leasing_setuid, monkeypatch, tmp_path
):
    """The orphan uid is leased by another install: no purge, the uid stays taken
    and health names the lease until it is gone."""
    mine, other = leasing_setuid
    manager = PluginManager(plugins_root=tmp_path / "plugins")
    a = await _instance("a")
    await PluginUidRepository.allocate(a, [40000], lambda _uid: True)
    await PluginRepository.delete(a)
    other.claim_uid(40000)
    purged: list[int] = []
    monkeypatch.setattr(SetuidBackend, "_helper", lambda self, op, *a: purged.append(self.uid))
    await manager.reap_uids()
    lease = str(tmp_path / "tmp" / LEASE_DIR_NAME / "40000")
    assert purged == []
    assert await PluginUidRepository.orphans() == {a: 40000}
    assert lease in manager.purge_blocked()

    # The other install freed it (its purge succeeded): the next start purges it.
    other.release_uid(40000)
    await manager.reap_uids()
    assert purged == [40000]
    assert await PluginUidRepository.orphans() == {}
    assert manager.purge_blocked() == []
    assert not Path(lease).exists()  # released with the row


async def test_startup_takes_back_the_leases_of_kept_uids(test_db, leasing_setuid, tmp_path):
    """After a crash (locks gone) or a cleared temp dir: the uids this install
    keeps are leased again before anything starts."""
    mine, other = leasing_setuid
    manager = PluginManager(plugins_root=tmp_path / "plugins")
    a = await _instance("a")
    await manager._instance_backend(a)
    mine.leases.close()
    for domain in (tmp_path / "tmp", tmp_path / "shm"):
        (domain / LEASE_DIR_NAME / "40000").unlink()
    await manager.reap_uids()
    assert (tmp_path / "tmp" / LEASE_DIR_NAME / "40000").read_text().strip() == (
        mine.leases.install_id
    )
    with pytest.raises(UidLeaseTaken):
        other.claim_uid(40000)
