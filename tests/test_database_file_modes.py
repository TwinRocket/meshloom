"""Database files on a package install (app/services/data_permissions.py)."""

import os
import stat
from pathlib import Path

import pytest

from app.services import data_permissions


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


@pytest.fixture
def _package(monkeypatch):
    monkeypatch.setattr(data_permissions, "detect_install_kind", lambda: ("package", True))


@pytest.mark.usefixtures("_package")
@pytest.mark.parametrize(
    "name",
    [
        "meshcore.db",
        "meshcore.db-wal",
        "meshcore.db-shm",
        "meshcore.db.pre-fk-migration.bak",
    ],
)
def test_other_accounts_lose_access_to_the_database_files(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"x")
    path.chmod(0o644)
    data_permissions.tighten_database_files(tmp_path / "meshcore.db")
    assert _mode(path) == 0o640


@pytest.mark.usefixtures("_package")
def test_a_stricter_mode_is_left_alone_and_other_files_are_not_touched(tmp_path):
    strict = tmp_path / "meshcore.db"
    strict.write_bytes(b"x")
    strict.chmod(0o600)
    other = tmp_path / "meshcoreother"
    other.write_bytes(b"x")
    other.chmod(0o644)
    data_permissions.tighten_database_files(strict)
    assert _mode(strict) == 0o600
    assert _mode(other) == 0o644


@pytest.mark.usefixtures("_package")
def test_a_link_is_neither_followed_nor_changed(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_bytes(b"x")
    target.chmod(0o644)
    (tmp_path / "meshcore.db-wal").symlink_to(target)
    data_permissions.tighten_database_files(tmp_path / "meshcore.db")
    assert _mode(target) == 0o644


@pytest.mark.usefixtures("_package")
def test_a_missing_directory_is_not_an_error(tmp_path):
    data_permissions.tighten_database_files(tmp_path / "none" / "meshcore.db")


@pytest.mark.parametrize("kind", ["docker", "source", "addon"])
def test_other_install_kinds_keep_their_modes(tmp_path, monkeypatch, kind):
    """Their data folder is the owner's: a copy from the host keeps working."""
    monkeypatch.setattr(data_permissions, "detect_install_kind", lambda: (kind, False))
    path = tmp_path / "meshcore.db"
    path.write_bytes(b"x")
    path.chmod(0o644)
    data_permissions.tighten_database_files(path)
    assert _mode(path) == 0o644


@pytest.mark.usefixtures("_package")
def test_the_write_ahead_log_inherits_the_tightened_mode(tmp_path):
    """Why tightening before the first connection is enough for -wal and -shm."""
    import sqlite3

    path = tmp_path / "meshcore.db"
    first = sqlite3.connect(path)
    first.execute("PRAGMA journal_mode=WAL")
    first.execute("CREATE TABLE t(x)")
    first.commit()
    first.close()
    path.chmod(0o644)

    data_permissions.tighten_database_files(path)
    second = sqlite3.connect(path)
    second.execute("INSERT INTO t VALUES (1)")
    second.commit()
    assert _mode(tmp_path / "meshcore.db-wal") == 0o640
    assert _mode(tmp_path / "meshcore.db-shm") == 0o640
    second.close()
