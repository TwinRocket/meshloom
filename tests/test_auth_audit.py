"""The audit log (app/auth/audit.py): one row per event, the API, the purge, no secret."""

import json
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.auth import audit, setup_code
from app.repository.auth import AuthAuditRepository, AuthSettingsRepository
from app.routers import auth as auth_router
from tests.auth_helpers import ADMIN, PASSWORD, create_user, login, make_client

# Secret fixtures typed into the flows below; none may reach an audit row.
NEW_PASSWORD = "a fresh long passphrase 42"
WRONG_PASSWORD = "not the password at all"
PRIVATE_KEY_HEX = "ab" * 64


async def _rows():
    return list(reversed(await AuthAuditRepository.list_recent(limit=1000)))


def _summary(rows):
    return [(row.action, row.outcome, row.via) for row in rows]


@pytest.fixture(autouse=True)
def _no_setup_delay(monkeypatch):
    monkeypatch.setattr(auth_router, "SETUP_FAILURE_DELAY_S", 0)


# --- Account events, through the real gate -----------------------------------


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_account_flow_writes_one_row_per_event_with_actor_via_and_outcome():
    await setup_code.prepare_first_run(existing_database=True)
    code = await setup_code.issue_setup_code()
    wrong_code = "00000000" if code != "00000000" else "11111111"
    async with make_client() as client:
        refused = await client.post(
            "/api/auth/setup",
            json={"username": ADMIN, "password": PASSWORD, "setup_code": wrong_code},
        )
        assert refused.status_code == 403
        done = await client.post(
            "/api/auth/setup", json={"username": ADMIN, "password": PASSWORD, "setup_code": code}
        )
        assert done.status_code == 200, done.text
        await client.post("/api/auth/logout")

        bad = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": WRONG_PASSWORD}
        )
        assert bad.status_code == 401
        await login(client)
        assert (
            await client.patch(
                "/api/auth/me",
                json={"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD},
            )
        ).status_code == 403
        assert (
            await client.patch(
                "/api/auth/me", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
            )
        ).status_code == 200
        assert (await client.patch("/api/auth/me", json={"display_name": "Owner"})).status_code == (
            200
        )
        assert (await client.delete("/api/auth/sessions")).status_code == 200
        assert (
            await client.patch("/api/auth/settings", json={"audit_retention_days": 400})
        ).status_code == 200
        await client.post("/api/auth/logout")

    rows = await _rows()
    assert _summary(rows) == [
        ("setup", "failure", "lan"),
        ("setup", "success", "lan"),
        ("logout", "success", "lan"),
        ("login", "failure", "lan"),
        ("login", "success", "lan"),
        ("password.changed", "failure", "lan"),
        ("password.changed", "success", "lan"),
        ("user.changed", "success", "lan"),
        ("session.revoked", "success", "lan"),
        ("settings.changed", "success", "lan"),
        ("logout", "success", "lan"),
    ]
    anonymous = {("setup", "failure"), ("login", "failure")}
    for row in rows:
        assert row.ip == "127.0.0.1"
        if (row.action, row.outcome) in anonymous:
            assert row.actor_user_id is None
        else:
            assert row.actor_user_id == 1, row
    assert rows[3].target == "user:1"  # the typed name matches the account
    assert rows[4].target == "user:1"
    assert rows[9].details == {"audit_retention_days": 400}


# --- The API ------------------------------------------------------------------


@pytest.mark.usefixtures("test_db")
async def test_audit_api_pages_newest_first_with_an_opaque_cursor(client):
    for index in range(5):
        await audit.record("login", "success", actor=audit.AuditActor(via="lan", user_id=index))
    first = await client.get("/api/auth/audit", params={"limit": 2})
    assert first.status_code == 200
    page = first.json()
    assert [entry["actor_user_id"] for entry in page["entries"]] == [4, 3]
    second = (
        await client.get("/api/auth/audit", params={"limit": 2, "cursor": page["next_cursor"]})
    ).json()
    assert [entry["actor_user_id"] for entry in second["entries"]] == [2, 1]
    last = (
        await client.get("/api/auth/audit", params={"limit": 2, "cursor": second["next_cursor"]})
    ).json()
    assert [entry["actor_user_id"] for entry in last["entries"]] == [0]
    assert last["next_cursor"] is None
    # Reading the log writes nothing.
    assert len(await _rows()) == 5


@pytest.mark.usefixtures("test_db")
@pytest.mark.parametrize("cursor", ["abc", "-1", "0", str(2**63), "²", "١٢", "1 ", "+1"])
async def test_audit_api_refuses_a_bad_cursor(client, cursor):
    response = await client.get("/api/auth/audit", params={"cursor": cursor})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "auth_audit_cursor_invalid"


@pytest.mark.usefixtures("test_db")
@pytest.mark.parametrize("role", ["member", "viewer", "kiosk"])
async def test_audit_api_is_admin_only(client, sign_in_as, role):
    sign_in_as(role)
    assert (await client.get("/api/auth/audit")).status_code == 403


@pytest.mark.usefixtures("test_db")
@pytest.mark.parametrize("days", [0, 36501])
async def test_retention_is_bounded(client, days):
    response = await client.patch("/api/auth/settings", json={"audit_retention_days": days})
    assert response.status_code == 422
    assert (await AuthSettingsRepository.get()).audit_retention_days == 366


# --- Instance-wide actions ------------------------------------------------------


@pytest.mark.usefixtures("test_db")
async def test_fanout_edits_are_not_audited(client):
    """The `bot.*` rows went with the bots (5.0.0): no fanout edit writes a row, and a
    retired bot create (410) writes none either."""
    with patch("app.routers.fanout.fanout_manager") as manager:
        manager.reload_config = AsyncMock()
        manager.remove_config = AsyncMock()
        retired = await client.post(
            "/api/fanout",
            json={"type": "bot", "name": "Echo", "config": {"code": "def bot(**kw): pass"}},
        )
        assert retired.status_code == 410
        webhook = await client.post(
            "/api/fanout",
            json={
                "type": "webhook",
                "name": "Hook",
                "config": {"url": "https://example.com/hook"},
                "enabled": False,
            },
        )
        assert webhook.status_code == 200, webhook.text
        hook_id = webhook.json()["id"]
        assert (
            await client.patch(f"/api/fanout/{hook_id}", json={"name": "Hook 2"})
        ).status_code == 200
        assert (await client.delete(f"/api/fanout/{hook_id}")).status_code == 200

    assert await _rows() == []


@pytest.mark.usefixtures("test_db")
async def test_refused_factory_reset_and_key_import_write_a_failure_row(client):
    with patch(
        "app.routers.radio.radio_manager.require_connected",
        side_effect=HTTPException(status_code=503, detail="Radio not connected"),
    ):
        reset = await client.post("/api/radio/factory-reset", json={"confirm_name": "node"})
        key = await client.put("/api/radio/private-key", json={"private_key": PRIVATE_KEY_HEX})
    assert reset.status_code == key.status_code == 503
    rows = await _rows()
    assert _summary(rows) == [
        ("factory_reset", "error", "lan"),
        ("private_key.imported", "error", "lan"),
    ]
    assert [row.details for row in rows] == [{"status": 503}, {"status": 503}]


@pytest.mark.usefixtures("test_db")
async def test_update_trigger_is_audited_when_refused(client):
    response = await client.post("/api/updates/apply")
    assert response.status_code == 409
    [row] = await _rows()
    assert (row.action, row.outcome, row.actor_user_id) == ("update.triggered", "failure", 1)
    assert row.details["trigger"] == "manual"


@pytest.mark.usefixtures("test_db")
async def test_v1_restore_writes_one_row(client):
    ok = await client.post("/api/settings/backup/restore", json={"confirm": True})
    assert ok.status_code == 200, ok.text
    refused = await client.post("/api/settings/backup/restore", json={"confirm": False})
    assert refused.status_code == 400
    assert _summary(await _rows()) == [
        ("backup.restored", "success", "lan"),
        ("backup.restored", "failure", "lan"),
    ]


# --- record() and audited() -----------------------------------------------------


@pytest.mark.usefixtures("test_db")
async def test_audited_maps_the_block_outcome():
    actor = audit.AuditActor(via="lan", user_id=7, ip="10.0.0.2")
    async with audit.audited(actor, "factory_reset") as scope:
        scope.details["identity"] = "abcdef"
    with pytest.raises(HTTPException):
        async with audit.audited(actor, "factory_reset"):
            raise HTTPException(status_code=422)
    with pytest.raises(RuntimeError):
        async with audit.audited(actor, "factory_reset"):
            raise RuntimeError("boom")
    with pytest.raises(HTTPException):
        async with audit.audited(actor, "factory_reset"):
            raise HTTPException(status_code=403)
    rows = await _rows()
    assert [(row.outcome, row.details) for row in rows] == [
        ("success", {"identity": "abcdef"}),
        ("failure", {"status": 422}),
        ("error", {}),
        ("refused", {"status": 403}),
    ]
    assert all((row.actor_user_id, row.ip, row.via) == (7, "10.0.0.2", "lan") for row in rows)


@pytest.mark.usefixtures("test_db")
async def test_record_never_raises_and_refuses_secret_details(caplog):
    written = await audit.record(
        "login", "success", actor=audit.SYSTEM_ACTOR, details={"password": "x"}
    )
    assert written is False
    assert await _rows() == []
    with patch.object(AuthAuditRepository, "add", AsyncMock(side_effect=OSError("disk"))):
        assert await audit.record("login", "success", actor=audit.SYSTEM_ACTOR) is False


def test_vocabulary_covers_the_ticket_events():
    for action in (
        "login",
        "logout",
        "session.revoked",
        "user.created",
        "user.changed",
        "user.disabled",
        "user.deleted",
        "password.changed",
        "setup",
        "token.created",
        "token.revoked",
        "pairing.used",
        "totp.enrolled",
        "totp.disabled",
        "passkey.added",
        "passkey.removed",
        "plugin.created",
        "plugin.changed",
        "plugin.disabled",
        "plugin.installed",
        "plugin.updated",
        "plugin.removed",
        "plugin.enabled",
        "plugin.config_changed",
        "plugins.paused",
        "plugins.resumed",
        "store.repository_added",
        "store.repository_removed",
        "backup.restored",
        "private_key.imported",
        "factory_reset",
        "update.triggered",
        "remote.linked",
        "remote.enabled",
        "remote.disabled",
    ):
        assert action in audit.AUDIT_ACTIONS
    assert "delayed" in audit.AUDIT_OUTCOMES


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_a_failed_login_never_stores_an_unknown_typed_name():
    await create_user()
    async with make_client() as client:
        # The password typed into the username field, then a wrong password.
        await client.post("/api/auth/login", json={"username": PASSWORD, "password": "x" * 12})
        await client.post(
            "/api/auth/login", json={"username": f"  {ADMIN.upper()} ", "password": "x" * 12}
        )
    rows = await _rows()
    assert [(row.action, row.outcome, row.target) for row in rows] == [
        ("login", "failure", audit.UNKNOWN_USERNAME_TARGET),
        ("login", "failure", "user:1"),
    ]
    assert all(PASSWORD not in json.dumps(row.model_dump()) for row in rows)


@pytest.mark.usefixtures("test_db")
async def test_a_failed_auto_update_writes_an_error_row(monkeypatch):
    from types import SimpleNamespace

    from app.repository import AppSettingsRepository
    from app.services import install_kind, oss_updates, update_apply

    monkeypatch.setattr(
        oss_updates, "get_update_status", lambda: {"update_available": True, "latest": "9.9.9"}
    )
    monkeypatch.setattr(install_kind, "detect_install_kind", lambda: ("deb", True))
    monkeypatch.setattr(
        AppSettingsRepository,
        "get",
        AsyncMock(
            return_value=SimpleNamespace(
                auto_update=True,
                auto_update_window_start="00:00",
                auto_update_window_end="23:59",
                auto_update_weekdays=[],
            )
        ),
    )
    monkeypatch.setattr(oss_updates, "in_window", lambda *args: True)
    monkeypatch.setattr(update_apply, "expire_stale_applying_job", lambda: {})
    monkeypatch.setattr(update_apply, "start_apply", AsyncMock(side_effect=OSError("helper")))
    with pytest.raises(OSError):
        await oss_updates._maybe_auto_apply()
    [row] = await _rows()
    assert (row.action, row.outcome, row.via) == ("update.triggered", "error", "system")
    assert row.details == {"trigger": "auto", "target": "9.9.9"}


# --- Retention purge --------------------------------------------------------------


@pytest.mark.usefixtures("test_db")
async def test_purge_honours_the_retention_setting():
    now = int(time.time())
    day = audit.SECONDS_PER_DAY
    for age_days in (400, 367, 365, 1):
        await AuthAuditRepository.add("login", "success", ts=now - age_days * day)
    assert await audit.purge_audit(now=now) == 2  # 366 days by default
    assert len(await _rows()) == 2
    await AuthSettingsRepository.update(audit_retention_days=30)
    assert await audit.purge_audit(now=now) == 1
    assert [row.ts for row in await _rows()] == [now - day]


@pytest.mark.usefixtures("test_db")
async def test_purge_deletes_in_bounded_batches():
    now = int(time.time())
    for _ in range(7):
        await AuthAuditRepository.add("login", "success", ts=now - 400 * audit.SECONDS_PER_DAY)
    await AuthAuditRepository.add("login", "success", ts=now)
    assert await AuthAuditRepository.purge_older_than(now - 1, batch_size=3) == 7
    assert len(await _rows()) == 1


@pytest.mark.usefixtures("test_db")
async def test_the_housekeeping_loop_runs_the_purge(monkeypatch):
    from app.services import stale_contacts

    calls = []

    async def fake_purge():
        calls.append("audit")
        return 0

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) > 1:
            raise RuntimeError("stop")

    monkeypatch.setattr(stale_contacts, "purge_audit", fake_purge)
    monkeypatch.setattr(stale_contacts.asyncio, "sleep", fake_sleep)
    with pytest.raises(RuntimeError, match="stop"):
        await stale_contacts._stale_purge_loop()
    assert calls == ["audit"]


# --- No secret in any row -----------------------------------------------------------


@pytest.mark.real_auth
@pytest.mark.usefixtures("test_db", "fast_argon2")
async def test_no_audit_row_holds_a_known_secret():
    await setup_code.prepare_first_run(existing_database=True)
    code = await setup_code.issue_setup_code()
    async with make_client() as client:
        await client.post(
            "/api/auth/setup",
            json={"username": ADMIN, "password": PASSWORD, "setup_code": code},
        )
        await client.post("/api/auth/logout")
        await client.post("/api/auth/login", json={"username": ADMIN, "password": WRONG_PASSWORD})
        await login(client)
        session_cookie = client.cookies.get("meshloom_session")
        await client.patch(
            "/api/auth/me", json={"current_password": PASSWORD, "new_password": NEW_PASSWORD}
        )
        with patch("app.routers.radio.radio_manager.require_connected"):
            await client.put("/api/radio/private-key", json={"private_key": PRIVATE_KEY_HEX})
    await create_user("second", password=WRONG_PASSWORD, role="member")

    secrets = [PASSWORD, NEW_PASSWORD, WRONG_PASSWORD, code, PRIVATE_KEY_HEX, session_cookie]
    rows = await _rows()
    assert len(rows) >= 6
    for row in rows:
        text = json.dumps(row.model_dump())
        for secret in secrets:
            assert secret not in text, (row.action, row.outcome)
