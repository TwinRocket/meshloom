"""``meshloom-admin list-bans / unban / whitelist`` (MSHL-422) against a database file,
including an unban that applies to a running service at once."""

from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from app.auth import ip_ban
from app.repository.auth import AuthSettingsRepository
from tests.conftest import patched_database
from tests.test_cli import _init_db, _query, run

PUBLIC = "198.51.100.20"
FAR = 4_000_000_000


@pytest.fixture
def dbfile(tmp_path):
    path = tmp_path / "meshcore.db"
    _init_db(path)
    return path


def _ban_row(path, address: str = PUBLIC, expires_at: int = FAR) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO ip_bans (address, banned_at, expires_at, failures, route)"
        " VALUES (?, 1, ?, 10, 'login')",
        (address, expires_at),
    )
    conn.commit()
    conn.close()


def _audit(path) -> list[tuple]:
    return _query(path, "SELECT action, target, outcome FROM auth_audit ORDER BY id")


def test_list_bans_shows_only_running_bans(dbfile, capsys):
    assert run(dbfile, "list-bans") == 0
    assert "No address is banned" in capsys.readouterr().out
    _ban_row(dbfile)
    _ban_row(dbfile, "203.0.113.1", expires_at=5)  # over
    assert run(dbfile, "list-bans") == 0
    out = capsys.readouterr().out
    assert PUBLIC in out and "203.0.113.1" not in out
    assert run(dbfile, "list-bans", "--json") == 0
    [ban] = json.loads(capsys.readouterr().out)
    assert ban["address"] == PUBLIC and ban["failures"] == 10
    assert _audit(dbfile) == []  # reading is not audited


def test_unban_one_and_all(dbfile, capsys):
    _ban_row(dbfile)
    _ban_row(dbfile, "2001:db8:1:2::/64")
    assert run(dbfile, "unban", "::ffff:198.51.100.20") == 0
    assert "unbanned" in capsys.readouterr().out
    assert run(dbfile, "unban", PUBLIC) == 1
    assert "is not banned" in capsys.readouterr().err
    assert run(dbfile, "unban", "nope") == 1
    assert run(dbfile, "unban", "--all") == 0
    assert "1 ban(s) lifted" in capsys.readouterr().out
    assert _query(dbfile, "SELECT COUNT(*) FROM ip_bans") == [(0,)]
    audited = [row for row in _audit(dbfile) if row[2] == "success"]
    assert audited[0] == ("ip.unbanned", f"ip:{PUBLIC}", "success")
    assert [row[0] for row in audited] == ["ip.unbanned", "ip.unbanned"]


def test_whitelist_add_list_remove(dbfile, capsys):
    assert run(dbfile, "whitelist", "add", "203.0.113.0/24", "--note", "office") == 0
    assert run(dbfile, "whitelist", "add", "203.0.113.0/24") == 0
    assert "already" in capsys.readouterr().out
    assert run(dbfile, "whitelist", "add", "garbage") == 1
    assert run(dbfile, "whitelist", "list", "--json") == 0
    out = capsys.readouterr().out
    [entry] = json.loads(out[out.index("[") :])
    assert entry["network"] == "203.0.113.0/24" and entry["note"] == "office"
    assert run(dbfile, "whitelist", "remove", "203.0.113.0/24") == 0
    assert run(dbfile, "whitelist", "remove", "203.0.113.0/24") == 1
    assert run(dbfile, "whitelist", "list") == 0
    assert "empty" in capsys.readouterr().out
    actions = [row[0] for row in _audit(dbfile) if row[2] == "success"]
    assert actions == ["ip.whitelisted", "ip.unwhitelisted"]


def test_an_old_database_without_the_tables_is_explained(dbfile, capsys):
    conn = sqlite3.connect(dbfile)
    conn.execute("DROP TABLE ip_bans")
    conn.commit()
    conn.close()
    assert run(dbfile, "list-bans") == 1
    assert "start Meshloom once" in capsys.readouterr().err


@pytest.mark.parametrize("lift", [("unban", PUBLIC), ("whitelist", "add", f"{PUBLIC}/32")])
def test_the_cli_lifts_a_ban_the_running_service_holds_at_once(dbfile, capsys, lift):
    """The service keeps the ban in memory; the CLI writes from another process."""

    async def service_bans() -> int | None:
        async with patched_database(str(dbfile)):
            await AuthSettingsRepository.update(ip_ban_max_failures=3)
            for _ in range(3):
                ip_ban.note_failure(PUBLIC, via="lan", proxied=False, route="login")
            await ip_ban.settle()
            return await ip_ban.check(PUBLIC)

    async def service_checks() -> int | None:
        async with patched_database(str(dbfile)):  # no load(): only the memory of the ban
            return await ip_ban.check(PUBLIC)

    assert asyncio.run(service_bans()) is not None
    assert PUBLIC in ip_ban._bans
    assert run(dbfile, *lift) == 0
    assert asyncio.run(service_checks()) is None
