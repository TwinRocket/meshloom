"""Local accounts repository (app/repository/auth.py)."""

import logging

import pytest

from app.auth.models import TrustedNetworkRule
from app.auth.tokens import hash_token, new_token
from app.log_redaction import clamp_quiet_third_party_loggers
from app.repository.auth import (
    MAX_SESSIONS_PER_USER,
    AuthAuditRepository,
    AuthSessionRepository,
    AuthSettingsRepository,
    InvalidUsernameError,
    UsernameTakenError,
    UserRepository,
)

HASH = "$argon2id$v=19$m=8,t=1,p=1$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaGhhc2hoYXNoaGFzaA"


@pytest.mark.asyncio
async def test_usernames_are_unique_case_insensitively(test_db):
    user = await UserRepository.create("  Alice ", "admin", password_hash=HASH, now=10)
    assert user.username == "Alice"
    assert user.has_password and user.created_at == user.updated_at == 10
    for clash in ("alice", "ALICE", "Alice", "Ａlice"):  # fullwidth A folds under NFKC
        with pytest.raises(UsernameTakenError):
            await UserRepository.create(clash, "member")
    await UserRepository.create("Élise", "viewer")
    with pytest.raises(UsernameTakenError):
        await UserRepository.create("éLISE", "viewer")
    with pytest.raises(UsernameTakenError):
        await UserRepository.create("Élise", "viewer")  # decomposed accent
    assert [u.username for u in await UserRepository.list_all()] == ["Alice", "Élise"]
    found = await UserRepository.get_by_username("aLiCe")
    assert found is not None and found.id == user.id


@pytest.mark.asyncio
async def test_bad_usernames_and_roles_are_refused(test_db):
    for bad in ("", "   ", "a" * 65, "bad\nname", "tab\tname"):
        with pytest.raises(InvalidUsernameError):
            await UserRepository.create(bad, "member")
    with pytest.raises(ValueError):
        await UserRepository.create("bob", "owner")  # type: ignore[arg-type]
    assert await UserRepository.get_by_username("") is None


@pytest.mark.asyncio
async def test_password_hash_is_nullable_and_never_on_the_user_model(test_db):
    kiosk = await UserRepository.create("kiosk", "kiosk", provider="system")
    ingress = await UserRepository.create("ha:abc", "member", provider="ingress", external_id="abc")
    assert not kiosk.has_password and not ingress.has_password
    assert {"password_hash", "totp_secret"}.isdisjoint(kiosk.model_dump())
    by_external = await UserRepository.get_by_external_id("ingress", "abc")
    assert by_external is not None and by_external.id == ingress.id

    local = await UserRepository.create("carol", "member", password_hash=HASH)
    credentials = await UserRepository.get_credentials("CAROL")
    assert credentials is not None and credentials.password_hash is not None
    assert credentials.password_hash.get_secret_value() == HASH
    assert HASH not in repr(credentials) and HASH not in credentials.model_dump_json()
    assert (await UserRepository.get_credentials("kiosk")).password_hash is None  # type: ignore[union-attr]
    assert await UserRepository.get_credentials("nobody") is None

    assert await UserRepository.set_password_hash(local.id, None)
    updated = await UserRepository.get(local.id)
    assert updated is not None and not updated.has_password and not updated.must_change_password


@pytest.mark.asyncio
async def test_must_change_password_only_on_accounts_with_a_password_to_change(test_db):
    from app.repository.auth import MustChangeNotApplicableError

    kiosk = await UserRepository.create("wall", "kiosk")
    ha = await UserRepository.create("ha", "member", provider="ingress", external_id="x1")
    local = await UserRepository.create("dana", "member", password_hash=HASH)
    for build in (
        lambda: UserRepository.create("wall2", "kiosk", must_change_password=True),
        lambda: UserRepository.create("nopw", "member", must_change_password=True),
        lambda: UserRepository.update(kiosk.id, must_change_password=True),
        lambda: UserRepository.update(ha.id, must_change_password=True),
        lambda: UserRepository.set_password_hash(kiosk.id, HASH, must_change_password=True),
        lambda: UserRepository.set_password_hash(ha.id, HASH, must_change_password=True),
        lambda: UserRepository.set_password_hash(local.id, None, must_change_password=True),
    ):
        with pytest.raises(MustChangeNotApplicableError):
            await build()
    assert await UserRepository.get_by_username("wall2") is None
    assert all(not u.must_change_password for u in await UserRepository.list_all())
    kept = await UserRepository.get(kiosk.id)
    assert kept is not None and not kept.has_password

    # A flagged account that becomes a kiosk loses the flag.
    assert await UserRepository.set_password_hash(local.id, HASH, must_change_password=True)
    flagged = await UserRepository.update(local.id, role="kiosk")
    assert flagged is not None and not flagged.must_change_password


@pytest.mark.asyncio
async def test_update_and_admin_count(test_db):
    admin = await UserRepository.create("root", "admin")
    other = await UserRepository.create("ops", "admin")
    assert await UserRepository.count_enabled_admins() == 2
    changed = await UserRepository.update(other.id, disabled=True, display_name=" Ops ", now=99)
    assert changed is not None and changed.disabled and changed.display_name == "Ops"
    # Never backwards, and at least one second on: the known-device cookies key on it.
    assert changed.updated_at == other.updated_at + 1
    assert await UserRepository.count_enabled_admins() == 1
    demoted = await UserRepository.update(admin.id, role="viewer")
    assert demoted is not None and demoted.role == "viewer"
    with pytest.raises(ValueError):
        await UserRepository.update(admin.id, role="root")  # type: ignore[arg-type]
    assert await UserRepository.update(12345, role="member") is None


@pytest.mark.asyncio
async def test_updated_at_moves_on_every_write_even_in_the_same_second(test_db):
    user = await UserRepository.create("erin", "member", password_hash=HASH, now=500)
    first = await UserRepository.update(user.id, display_name="E", now=500)
    assert first is not None and first.updated_at == 501
    assert await UserRepository.set_password_hash(user.id, HASH, now=500)
    second = await UserRepository.get(user.id)
    assert second is not None and second.updated_at == 502
    later = await UserRepository.update(user.id, display_name="Erin", now=900)
    assert later is not None and later.updated_at == 900


@pytest.mark.asyncio
async def test_keep_an_admin_refuses_and_rolls_back(test_db):
    from app.repository.auth import LastAdminError

    admin = await UserRepository.create("root", "admin")
    for change in ({"role": "member"}, {"disabled": True}):
        with pytest.raises(LastAdminError):
            await UserRepository.update(admin.id, keep_an_admin=True, **change)  # type: ignore[arg-type]
        kept = await UserRepository.get(admin.id)
        assert kept is not None and kept.role == "admin" and not kept.disabled
    with pytest.raises(LastAdminError):
        await UserRepository.delete(admin.id, keep_an_admin=True)
    assert await UserRepository.get(admin.id) is not None
    # Without the flag (meshloom-admin disable-user --force) the change goes through.
    assert (await UserRepository.update(admin.id, disabled=True)).disabled  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_sessions_lookup_expiry_touch_and_revoke(test_db):
    user = await UserRepository.create("dave", "member")
    token = new_token()
    session = await AuthSessionRepository.create(
        user.id, hash_token(token), lifetime_s=100, ip="192.0.2.1", via="lan", now=1000
    )
    assert session.expires_at == 1100 and session.last_seen_at == 1000
    assert "token" not in session.model_dump()

    found = await AuthSessionRepository.get_active(hash_token(token), now=1050)
    assert found is not None and found.id == session.id
    assert await AuthSessionRepository.get_active(hash_token(new_token()), now=1050) is None
    assert await AuthSessionRepository.get_active(hash_token(token), now=1100) is None

    assert not await AuthSessionRepository.touch(session.id, now=1030)  # within a minute
    assert await AuthSessionRepository.touch(session.id, now=1060, expires_at=5000)
    found = await AuthSessionRepository.get_active(hash_token(token), now=2000)
    assert found is not None and found.last_seen_at == 1060

    assert await AuthSessionRepository.revoke(session.id, now=2001)
    assert not await AuthSessionRepository.revoke(session.id, now=2002)
    assert await AuthSessionRepository.get_active(hash_token(token), now=2003) is None
    assert not await AuthSessionRepository.touch(session.id, now=9000)


@pytest.mark.asyncio
async def test_sessions_are_capped_per_user_and_purged(test_db):
    user = await UserRepository.create("erin", "member")
    other = await UserRepository.create("frank", "member")
    ids = []
    for i in range(MAX_SESSIONS_PER_USER + 2):
        created = await AuthSessionRepository.create(
            user.id, hash_token(f"t{i}"), lifetime_s=10_000, now=100 + i
        )
        ids.append(created.id)
    await AuthSessionRepository.create(other.id, hash_token("o"), lifetime_s=10_000, now=100)

    active = await AuthSessionRepository.list_for_user(user.id, now=200)
    assert len(active) == MAX_SESSIONS_PER_USER
    assert active[0].id == ids[-1]
    assert {s.id for s in active} == set(ids[2:])

    keep = ids[-1]
    revoked = await AuthSessionRepository.revoke_all_for_user(
        user.id, except_session_id=keep, now=300
    )
    assert len(revoked) == MAX_SESSIONS_PER_USER - 1
    assert keep not in revoked
    assert [s.id for s in await AuthSessionRepository.list_for_user(user.id, now=300)] == [keep]

    # Revoked ones go; the two live sessions stay until they expire.
    assert await AuthSessionRepository.purge(now=300) == MAX_SESSIONS_PER_USER + 1
    assert await AuthSessionRepository.purge(now=20_000) == 2


@pytest.mark.asyncio
async def test_deleting_a_user_deletes_its_sessions(test_db):
    user = await UserRepository.create("gina", "member")
    await AuthSessionRepository.create(user.id, hash_token("g"), lifetime_s=100, now=1)
    assert await UserRepository.delete(user.id)
    assert await AuthSessionRepository.get_active(hash_token("g"), now=2) is None
    assert not await UserRepository.delete(user.id)
    # Ids are not reused after a delete (AUTOINCREMENT): audit rows keep pointing
    # at the deleted account, never at a new one.
    assert (await UserRepository.create("gina", "member")).id > user.id


@pytest.mark.asyncio
async def test_audit_rows_page_purge_and_refuse_secrets(test_db):
    for i in range(5):
        await AuthAuditRepository.add(
            "login",
            "success" if i % 2 else "failure",
            actor_user_id=1,
            ip="192.0.2.9",
            details={"reason": "bad_password", "attempt": i},
            ts=1000 + i,
        )
    page = await AuthAuditRepository.list_recent(limit=2)
    assert [e.ts for e in page] == [1004, 1003]
    older = await AuthAuditRepository.list_recent(limit=10, before_id=page[-1].id)
    assert [e.ts for e in older] == [1002, 1001, 1000]
    assert older[0].details == {"attempt": 2, "reason": "bad_password"}

    for secret in (
        {"password": "x"},
        {"newPassword": "x"},
        {"session_token": "x"},
        {"nested": {"token_hash": "x"}},
        {"items": [{"totp": "1"}]},
    ):
        with pytest.raises(ValueError):
            await AuthAuditRepository.add("login", "failure", details=secret)
    assert len(await AuthAuditRepository.list_recent()) == 5

    assert await AuthAuditRepository.purge_older_than(1003) == 3
    assert [e.ts for e in await AuthAuditRepository.list_recent()] == [1004, 1003]


@pytest.mark.asyncio
async def test_auth_settings_defaults_update_and_setup_code(test_db):
    settings = await AuthSettingsRepository.get()
    assert settings.audit_retention_days == 366
    assert settings.session_lifetime_days == 30
    assert settings.host_guard_mode == "observe"
    assert settings.ingress_default_role == "member"
    assert settings.trusted_networks == [] and not settings.setup_code_set
    assert not settings.lan_login_free

    updated = await AuthSettingsRepository.update(
        trusted_networks=[TrustedNetworkRule(cidr="192.168.1.7/24", user_id=3)],
        trusted_proxies=["127.0.0.1"],
        host_guard_mode="enforce",
        lan_login_free=True,
    )
    assert updated.trusted_networks[0].cidr == "192.168.1.0/24"
    assert updated.trusted_proxies == ["127.0.0.1/32"]
    assert updated.host_guard_mode == "enforce" and updated.lan_login_free
    assert updated.audit_retention_days == 366
    assert await AuthSettingsRepository.get() == updated

    with pytest.raises(ValueError):
        await AuthSettingsRepository.update(trusted_proxies=["not an ip"])
    with pytest.raises(ValueError):
        await AuthSettingsRepository.update(audit_retention_days=0)
    assert await AuthSettingsRepository.get() == updated

    code_hash = hash_token("setup-code")
    await AuthSettingsRepository.set_setup_code_hash(code_hash)
    assert await AuthSettingsRepository.get_setup_code_hash() == code_hash
    assert (await AuthSettingsRepository.get()).setup_code_set
    assert code_hash not in (await AuthSettingsRepository.get()).model_dump_json()
    await AuthSettingsRepository.set_setup_code_hash(None)
    assert not (await AuthSettingsRepository.get()).setup_code_set


@pytest.mark.asyncio
async def test_nothing_secret_is_logged(test_db, caplog):
    """Even at DEBUG: aiosqlite dumps statement parameters there, so the app
    clamps it to INFO (``app/log_redaction.py``)."""
    clamp_quiet_third_party_loggers()
    caplog.set_level(logging.DEBUG)
    token = new_token()
    user = await UserRepository.create("hal", "admin", password_hash=HASH)
    await UserRepository.get_credentials("hal")
    await AuthSessionRepository.create(user.id, hash_token(token), lifetime_s=60)
    await AuthSettingsRepository.set_setup_code_hash(hash_token("code"))
    with pytest.raises(UsernameTakenError) as excinfo:
        await UserRepository.create("HAL", "admin", password_hash=HASH)
    assert HASH not in str(excinfo.value)
    for secret in (HASH, token, hash_token(token), hash_token("code")):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_touch_never_revives_an_expired_or_revoked_session(test_db):
    user = await UserRepository.create("ivy", "member")
    expired = await AuthSessionRepository.create(user.id, hash_token("e"), lifetime_s=10, now=0)
    assert not await AuthSessionRepository.touch(expired.id, now=100, expires_at=10_000)
    assert await AuthSessionRepository.get_active(hash_token("e"), now=101) is None

    revoked = await AuthSessionRepository.create(user.id, hash_token("r"), lifetime_s=10_000, now=0)
    await AuthSessionRepository.revoke(revoked.id, now=1)
    assert not await AuthSessionRepository.touch(revoked.id, now=100, expires_at=20_000)
    assert await AuthSessionRepository.get_active(hash_token("r"), now=101) is None


@pytest.mark.asyncio
async def test_a_new_session_is_never_its_own_eviction_victim(test_db):
    user = await UserRepository.create("jo", "member")
    for i in range(MAX_SESSIONS_PER_USER):
        await AuthSessionRepository.create(user.id, hash_token(f"s{i}"), lifetime_s=10**6, now=1000)
    # Clock went backwards: the newest session sorts as the oldest.
    newest = await AuthSessionRepository.create(
        user.id, hash_token("new"), lifetime_s=10**6, now=500
    )
    assert newest.revoked_at is None
    active = await AuthSessionRepository.list_for_user(user.id, now=1001)
    assert len(active) == MAX_SESSIONS_PER_USER and newest.id in {s.id for s in active}


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "password2",
        "newPassword1",
        "oldpassword",
        "PASS_WORD".replace("_", ""),
        "new-Password",
        "apiKey",
        "api_key",
        "privateKey",
        "setup_code",
        "setupCode",
        "recovery_codes",
        "seed",
        "instance_seed",
        "otp",
        "TOTP",
        "totp_code",
        "client_secret",
        "sessionToken",
        "token_hash",
        "Authorization",
        "cookie",
        "code",
        "pin",
        "pin_code",
        "x_pin",
        "blePin",
        "newPin",
        "radio_ble_pin",
        "api-key",
        "x_auth_token",
        "bearer",
        "APIKey",
        "privatekey",
        "session_id",
        "pass word",
        "a.password",
        "\uff50\uff41\uff53\uff53\uff57\uff4f\uff52\uff44",  # fullwidth "password"
        "p\u00e4ssword",
        "\u0440\u0430ssword",  # Cyrillic look-alike of "password"
        "r\u043ele",  # Cyrillic "o" in an otherwise harmless "role"
    ],
)
@pytest.mark.asyncio
async def test_audit_refuses_secret_like_keys(test_db, key):
    with pytest.raises(ValueError):
        await AuthAuditRepository.add("login", "failure", details={key: "x"})
    with pytest.raises(ValueError):
        await AuthAuditRepository.add("login", "failure", details={"outer": [{key: "x"}]})


@pytest.mark.parametrize(
    "key",
    [
        "user_id",
        "role",
        "ip",
        "reason",
        "count",
        "attempt",
        "ping",
        "pinned",
        "target-host",
        "oldRole",
        "Reason",
    ],
)
@pytest.mark.asyncio
async def test_audit_accepts_harmless_keys(test_db, key):
    await AuthAuditRepository.add("settings", "success", details={key: 1})
    (entry,) = await AuthAuditRepository.list_recent(limit=1)
    assert entry.details == {key: 1}


@pytest.mark.asyncio
async def test_audit_keeps_harmless_keys_and_bounds_sizes(test_db):
    await AuthAuditRepository.add(
        "login", "failure", details={"reason": "bad_password", "attempt": 3, "role": "admin"}
    )
    big = {"reason": "x" * 5000}
    await AuthAuditRepository.add(
        "a" * 500, "o" * 500, target="t" * 5000, ip="i" * 500, via="v" * 500, details=big
    )
    newest, first = await AuthAuditRepository.list_recent()
    assert first.details == {"attempt": 3, "reason": "bad_password", "role": "admin"}
    assert newest.details == {"bytes": len('{"reason":""}') + 5000, "truncated": True}
    assert len(newest.action) == 64 and len(newest.outcome) == 32
    assert len(newest.target or "") == 256 and len(newest.ip or "") == 64
    assert len(newest.via or "") == 32


@pytest.mark.asyncio
async def test_settings_writes_fail_loudly_without_the_row(test_db):
    from app.migrations import run_migrations, set_version
    from app.repository.auth import AuthSettingsMissingError

    async with test_db.tx() as conn:
        await conn.execute("DELETE FROM auth_settings")
    with pytest.raises(AuthSettingsMissingError):
        await AuthSettingsRepository.set_setup_code_hash(hash_token("c"))
    with pytest.raises(AuthSettingsMissingError):
        await AuthSettingsRepository.update(lan_login_free=True)
    with pytest.raises(AuthSettingsMissingError):
        await AuthSettingsRepository.get()
    with pytest.raises(AuthSettingsMissingError):
        await AuthSettingsRepository.get_setup_code_hash()

    # The migration (and SCHEMA_TABLES at every start) puts the row back.
    async with test_db.tx() as conn:
        await set_version(conn, 100)
        await run_migrations(conn)
    await AuthSettingsRepository.set_setup_code_hash(hash_token("c"))
    assert await AuthSettingsRepository.get_setup_code_hash() == hash_token("c")


@pytest.mark.asyncio
async def test_the_first_ingress_admin_and_its_grant_share_one_transaction(test_db):
    """MSHL-300: the insert and ``ingress_admin_granted`` commit together, so a
    failure between them can never leave an ingress admin without the flag."""
    from app.repository.auth import AuthSettingsMissingError

    async with test_db.tx() as conn:
        await conn.execute("DELETE FROM auth_settings")
    with pytest.raises(AuthSettingsMissingError):
        await UserRepository.create(
            "ha", "admin", provider="ingress", external_id="x1", grant_ingress_admin=True
        )
    assert await UserRepository.get_by_external_id("ingress", "x1") is None

    async with test_db.tx() as conn:
        await conn.execute("INSERT INTO auth_settings (id) VALUES (1)")
    assert not await AuthSettingsRepository.ingress_admin_granted()
    user = await UserRepository.create(
        "ha", "admin", provider="ingress", external_id="x1", grant_ingress_admin=True
    )
    assert user.role == "admin"
    assert await AuthSettingsRepository.ingress_admin_granted()
    # Without the flag, a create leaves it alone.
    async with test_db.tx() as conn:
        await conn.execute("UPDATE auth_settings SET ingress_admin_granted = 0")
    await UserRepository.create("ha2", "member", provider="ingress", external_id="x2")
    assert not await AuthSettingsRepository.ingress_admin_granted()


@pytest.mark.asyncio
async def test_device_cookie_key_is_created_once_and_rotated(test_db):
    first = await AuthSettingsRepository.device_cookie_key()
    assert len(first) == 32
    assert await AuthSettingsRepository.device_cookie_key() == first
    await AuthSettingsRepository.rotate_device_cookie_key()
    second = await AuthSettingsRepository.device_cookie_key()
    assert len(second) == 32 and second != first
    # Never part of the settings model (GET /auth/settings).
    assert "device_cookie_key" not in (await AuthSettingsRepository.get()).model_dump()
