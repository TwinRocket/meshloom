"""The e-mail code and sign-in links (MSHL-426): ``app/auth/email_factor.py``,
``app/routers/auth_email.py`` and the ``method: "email"`` step of ``POST /auth/login/2fa``.

The SMTP send itself is ``send_message()`` of the notification e-mail transport
(``tests/test_transport_email.py``); here ``email_factor.deliver`` is replaced by a
mailbox that records each mail.
"""

from __future__ import annotations

import asyncio
import re

import pyotp
import pytest

from app.auth import email_factor, totp
from app.notifications.channels import ChannelCreate
from app.repository.auth import AuthAuditRepository, TotpRepository, UserRepository
from app.repository.email_factor import EmailFactorRepository, EmailSignInRepository
from app.repository.notification_channels import NotificationChannelRepository
from tests.auth_helpers import ADMIN, PASSWORD, create_user, login, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

ADDRESS = "alice@example.org"


class Mailbox:
    def __init__(self) -> None:
        self.mails: list[tuple[str, str, str]] = []
        self.fail: str | None = None

    async def deliver(self, address: str, subject: str, html: str) -> None:
        await email_factor._profile()  # no profile: EmailUnavailable, like the real one
        if self.fail:
            raise email_factor.EmailSendFailed(self.fail)
        self.mails.append((address, subject, html))

    def last_code(self) -> str:
        found = re.search(r">([0-9]{6})<", self.mails[-1][2])
        assert found, self.mails[-1][2]
        return found.group(1)

    def last_link(self) -> str:
        found = re.search(r'href="([^"]+)"', self.mails[-1][2])
        assert found
        return found.group(1)


@pytest.fixture(autouse=True)
def mailbox(monkeypatch):
    email_factor.reset_state()
    box = Mailbox()
    monkeypatch.setattr(email_factor, "deliver", box.deliver)
    yield box
    email_factor.reset_state()


@pytest.fixture
def clock(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(email_factor, "clock", lambda: now["t"])
    monkeypatch.setattr(totp.pending_logins, "_clock", lambda: now["t"])
    return now


async def _smtp_profile(enabled: bool = True) -> str:
    channel = await NotificationChannelRepository.create(
        ChannelCreate(
            type="email",
            name="Mail",
            enabled=enabled,
            config={"host": "93.184.215.14", "port": 587, "from": "m@example.org", "to": "x@y.org"},
        )
    )
    await EmailSignInRepository.set_channel_id(channel.id)
    return channel.id


async def _verified(client, mailbox: Mailbox, address: str = ADDRESS) -> None:
    sent = await client.post(
        "/api/auth/email/address", json={"password": PASSWORD, "address": address}
    )
    assert sent.status_code == 200, sent.text
    assert mailbox.mails[-1][0] == address
    verified = await client.post("/api/auth/email/verify", json={"code": mailbox.last_code()})
    assert verified.status_code == 200, verified.text
    assert verified.json()["address"] == address


async def _opt_in(client, **flags) -> dict:
    response = await client.patch("/api/auth/email", json={"password": PASSWORD, **flags})
    assert response.status_code == 200, response.text
    return response.json()


async def _account_with_email_code(mailbox: Mailbox, username: str = ADMIN):
    user = await UserRepository.get_by_username(username) or await create_user(username)
    async with make_client() as client:
        await login(client, username)
        await _verified(client, mailbox)
        await _opt_in(client, otp_enabled=True)
    email_factor.account_sends.clear()
    return user


def _code_of(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


# --- Without an SMTP profile ---------------------------------------------------------


async def test_without_an_smtp_profile_everything_is_refused_with_a_clear_error():
    await create_user()
    async with make_client() as client:
        await login(client)
        status = (await client.get("/api/auth/email")).json()
        assert status["available"] is False and status["applicable"] is True
        refused = await client.post(
            "/api/auth/email/address", json={"password": PASSWORD, "address": ADDRESS}
        )
        assert refused.status_code == 503 and _code_of(refused) == "auth_email_unavailable"
        link = await client.post("/api/auth/magic-link", json={"username": ADMIN})
        assert link.status_code == 503 and _code_of(link) == "auth_email_unavailable"
        assert (await client.get("/api/auth/session")).json()["methods"]["magic_link"] is False


async def test_a_disabled_channel_is_no_smtp_profile():
    await create_user()
    await _smtp_profile(enabled=False)
    assert await email_factor.available() is False


async def test_only_a_native_email_channel_can_be_picked():
    await create_user()
    channel_id = await _smtp_profile()
    async with make_client() as client:
        await login(client)
        view = (await client.get("/api/auth/email/settings")).json()
        assert view["channel_id"] == channel_id and view["available"] is True
        assert [c["id"] for c in view["channels"]] == [channel_id]
        bad = await client.patch("/api/auth/email/settings", json={"channel_id": "builtin:push"})
        assert bad.status_code == 400 and _code_of(bad) == "auth_email_channel_invalid"
        off = await client.patch("/api/auth/email/settings", json={"channel_id": None})
        assert off.json()["available"] is False


# --- The address -----------------------------------------------------------------------


async def test_an_address_is_stored_only_once_its_code_checks(mailbox):
    await create_user()
    await _smtp_profile()
    async with make_client() as client:
        await login(client)
        for bad in ("a@b.org, c@d.org", "no-at-sign", "a@b.org\nc@d.org"):
            refused = await client.post(
                "/api/auth/email/address", json={"password": PASSWORD, "address": bad}
            )
            assert refused.status_code in (400, 422), bad
        wrong_password = await client.post(
            "/api/auth/email/address", json={"password": "nope", "address": ADDRESS}
        )
        assert wrong_password.status_code == 403
        sent = await client.post(
            "/api/auth/email/address", json={"password": PASSWORD, "address": ADDRESS}
        )
        assert sent.json()["email_hint"] == "a***@example.org"
        assert (await client.get("/api/auth/email")).json()["pending_address"] == ADDRESS
        wrong = await client.post("/api/auth/email/verify", json={"code": "000000"})
        if mailbox.last_code() != "000000":
            assert wrong.status_code == 400
        assert (await client.get("/api/auth/email")).json()["address"] is None
        ok = await client.post("/api/auth/email/verify", json={"code": mailbox.last_code()})
        assert ok.status_code == 200 and ok.json()["address"] == ADDRESS
        again = await client.post("/api/auth/email/verify", json={"code": mailbox.last_code()})
        assert again.status_code == 409


async def test_a_second_mail_within_a_minute_is_refused(mailbox):
    await create_user()
    await _smtp_profile()
    async with make_client() as client:
        await login(client)
        body = {"password": PASSWORD, "address": ADDRESS}
        assert (await client.post("/api/auth/email/address", json=body)).status_code == 200
        again = await client.post("/api/auth/email/address", json=body)
        assert again.status_code == 429 and again.headers["retry-after"]
        assert len(mailbox.mails) == 1


async def test_with_totp_on_adding_the_email_factor_needs_a_totp_code(mailbox, monkeypatch):
    at = 1_800_000_015.0
    monkeypatch.setattr(totp, "clock", lambda: at)
    user = await create_user()
    secret = totp.new_secret()
    await TotpRepository.set_pending_secret(user.id, secret)
    assert await TotpRepository.enable(user.id, secret, 0, [])
    await _smtp_profile()
    async with make_client() as client:
        challenge = (await login(client)).json()["second_factor"]
        assert "email" not in challenge["methods"]
        signed = await client.post(
            "/api/auth/login/2fa",
            json={"pending_token": challenge["pending_token"], "code": pyotp.TOTP(secret).at(at)},
        )
        assert signed.status_code == 200, signed.text
        body = {"password": PASSWORD, "address": ADDRESS}
        refused = await client.post("/api/auth/email/address", json=body)
        assert refused.status_code == 403 and _code_of(refused) == "auth_2fa_required"
        code = pyotp.TOTP(secret).at(at + totp.PERIOD_S)
        monkeypatch.setattr(totp, "clock", lambda: at + totp.PERIOD_S)
        sent = await client.post("/api/auth/email/address", json={**body, "code": code})
        assert sent.status_code == 200, sent.text


# --- The e-mail code after the password ---------------------------------------------------


async def test_the_email_code_is_the_second_step_and_works_once(mailbox, clock):
    await _smtp_profile()
    await _account_with_email_code(mailbox)
    async with make_client() as client:
        body = (await login(client)).json()
        assert body["authenticated"] is False
        challenge = body["second_factor"]
        assert challenge["methods"] == ["email"]
        assert challenge["email_hint"] == "a***@example.org"
        token = challenge["pending_token"]
        # A TOTP-looking code without method email is not accepted (no TOTP here).
        totp_try = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": "123456"}
        )
        assert totp_try.status_code == 401 and _code_of(totp_try) == "auth_2fa_invalid"
        sent = await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        assert sent.status_code == 200, sent.text
        assert sent.json()["expires_in"] == 600
        code = mailbox.last_code()
        done = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": code, "method": "email"}
        )
        assert done.status_code == 200, done.text
        assert done.json()["authenticated"] is True
    async with make_client() as other:
        token = (await login(other)).json()["second_factor"]["pending_token"]
        replay = await other.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": code, "method": "email"}
        )
        assert replay.status_code == 401 and _code_of(replay) == "auth_2fa_invalid"
    rows = [r.model_dump() for r in await AuthAuditRepository.list_recent(limit=100)]
    assert any(r["action"] == "login" and r["details"].get("factor") == "email" for r in rows)
    assert code not in str(rows)


async def test_the_email_code_expires_after_ten_minutes(mailbox, clock):
    await _smtp_profile()
    await _account_with_email_code(mailbox)
    async with make_client() as client:
        token = (await login(client)).json()["second_factor"]["pending_token"]
        await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        code = mailbox.last_code()
        clock["t"] += email_factor.CODE_TTL_S + 1
        late = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": code, "method": "email"}
        )
        assert late.status_code == 401


async def test_five_wrong_email_codes_void_the_pending_login(mailbox, clock):
    await _smtp_profile()
    await _account_with_email_code(mailbox)
    async with make_client() as client:
        token = (await login(client)).json()["second_factor"]["pending_token"]
        await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        code = mailbox.last_code()
        wrong = "111111" if code != "111111" else "222222"
        for _ in range(totp.PENDING_MAX_ATTEMPTS):
            response = await client.post(
                "/api/auth/login/2fa",
                json={"pending_token": token, "code": wrong, "method": "email"},
            )
            assert response.status_code in (401, 429)
        after = await client.post(
            "/api/auth/login/2fa", json={"pending_token": token, "code": code, "method": "email"}
        )
        assert after.status_code in (401, 429)
        assert (await client.get("/api/auth/session")).json()["authenticated"] is False


async def test_resends_are_limited_per_minute_and_per_pending_login(mailbox, clock):
    await _smtp_profile()
    await _account_with_email_code(mailbox)
    async with make_client() as client:
        token = (await login(client)).json()["second_factor"]["pending_token"]
        first = await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        assert first.status_code == 200
        soon = await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        assert soon.status_code == 429 and _code_of(soon) == "auth_rate_limited"
        for _ in range(email_factor.PENDING_MAX_SENDS - 1):
            clock["t"] += email_factor.SEND_INTERVAL_S + 1
            ok = await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
            assert ok.status_code == 200
        clock["t"] += email_factor.SEND_INTERVAL_S + 1
        over = await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        assert over.status_code == 429 and _code_of(over) == "auth_email_send_limit"


async def test_a_failed_send_says_so_and_another_client_cannot_ask(mailbox, clock):
    await _smtp_profile()
    await _account_with_email_code(mailbox)
    async with make_client() as client:
        token = (await login(client)).json()["second_factor"]["pending_token"]
    async with make_client(client=("192.0.2.9", 4000)) as stranger:
        foreign = await stranger.post("/api/auth/login/2fa/email", json={"pending_token": token})
        assert foreign.status_code == 401
    mailbox.fail = "send_failed"
    async with make_client() as client:
        failed = await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        assert failed.status_code == 502 and _code_of(failed) == "auth_email_send_failed"


async def test_a_pending_login_without_the_email_flag_gets_no_code(mailbox):
    """An OpenID Connect or sign-in link step issues pending logins with email off."""
    await _smtp_profile()
    user = await _account_with_email_code(mailbox)
    current = await UserRepository.get(user.id)
    assert current is not None
    token = totp.pending_logins.issue(
        current.id, current.updated_at, totp.client_binding("127.0.0.1", "python-httpx/0")
    )
    async with make_client() as client:
        refused = await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        assert refused.status_code == 401


async def test_turning_the_email_code_off_needs_only_the_password(mailbox):
    await _smtp_profile()
    await _account_with_email_code(mailbox)
    async with make_client() as client:
        token = (await login(client)).json()["second_factor"]["pending_token"]
        await client.post("/api/auth/login/2fa/email", json={"pending_token": token})
        await client.post(
            "/api/auth/login/2fa",
            json={"pending_token": token, "code": mailbox.last_code(), "method": "email"},
        )
        wrong = await client.patch("/api/auth/email", json={"password": "x", "otp_enabled": False})
        assert wrong.status_code == 403
        assert (await _opt_in(client, otp_enabled=False))["otp_enabled"] is False
    async with make_client() as client:
        assert (await login(client)).json()["authenticated"] is True
        removed = await client.request("DELETE", "/api/auth/email", json={"password": PASSWORD})
        assert removed.status_code == 200
        assert (
            await EmailFactorRepository.get((await UserRepository.get_by_username(ADMIN)).id)
            is None
        )  # type: ignore[union-attr]


async def test_turning_a_feature_on_with_totp_on_needs_a_totp_code(mailbox, monkeypatch):
    at = 1_800_000_015.0
    monkeypatch.setattr(totp, "clock", lambda: at)
    user = await create_user()
    await _smtp_profile()
    async with make_client() as client:
        await login(client)
        await _verified(client, mailbox)
    secret = totp.new_secret()
    await TotpRepository.set_pending_secret(user.id, secret)
    assert await TotpRepository.enable(user.id, secret, 0, [])
    async with make_client() as client:
        challenge = (await login(client)).json()["second_factor"]
        signed = await client.post(
            "/api/auth/login/2fa",
            json={"pending_token": challenge["pending_token"], "code": pyotp.TOTP(secret).at(at)},
        )
        assert signed.status_code == 200, signed.text
        for flag in ("otp_enabled", "magic_link_enabled"):
            refused = await client.patch("/api/auth/email", json={"password": PASSWORD, flag: True})
            assert refused.status_code == 403, refused.text
            assert _code_of(refused) == "auth_2fa_required"
    factor = await EmailFactorRepository.get(user.id)
    assert factor is not None and not factor.otp_enabled and not factor.magic_link_enabled


async def test_turning_a_feature_on_without_an_smtp_profile_is_refused(mailbox):
    user = await create_user()
    await _smtp_profile()
    async with make_client() as client:
        await login(client)
        await _verified(client, mailbox)
        await EmailSignInRepository.set_channel_id(None)
        for flag in ("otp_enabled", "magic_link_enabled"):
            refused = await client.patch("/api/auth/email", json={"password": PASSWORD, flag: True})
            assert refused.status_code == 503, refused.text
            assert _code_of(refused) == "auth_email_unavailable"
    factor = await EmailFactorRepository.get(user.id)
    assert factor is not None and not factor.otp_enabled and not factor.magic_link_enabled


async def test_forgetting_the_address_needs_the_current_password(mailbox):
    user = await create_user()
    await _smtp_profile()
    async with make_client() as client:
        await login(client)
        await _verified(client, mailbox)
        refused = await client.request("DELETE", "/api/auth/email", json={"password": "nope"})
        assert refused.status_code == 403, refused.text
    factor = await EmailFactorRepository.get(user.id)
    assert factor is not None and factor.address == ADDRESS


async def test_turning_the_email_code_on_signs_the_other_sessions_out(mailbox):
    await create_user()
    await _smtp_profile()
    async with make_client() as other:
        await login(other)
        async with make_client() as client:
            await login(client)
            await _verified(client, mailbox)
            # Sign-in links alone sign nobody out.
            await _opt_in(client, magic_link_enabled=True)
            assert (await _session(other))["authenticated"] is True
            await _opt_in(client, otp_enabled=True)
            assert (await _session(client))["authenticated"] is True
        assert (await _session(other))["authenticated"] is False
    rows = await AuthAuditRepository.list_recent(limit=50)
    changed = [r for r in rows if r.action == "email_factor.changed" and r.outcome == "success"]
    assert changed[0].details["sessions_revoked"] == 1


async def test_an_expired_address_code_is_refused(mailbox, clock):
    user = await create_user()
    await _smtp_profile()
    async with make_client() as client:
        await login(client)
        sent = await client.post(
            "/api/auth/email/address", json={"password": PASSWORD, "address": ADDRESS}
        )
        assert sent.status_code == 200, sent.text
        clock["t"] += email_factor.CODE_TTL_S + 1
        late = await client.post("/api/auth/email/verify", json={"code": mailbox.last_code()})
        assert late.status_code in (400, 409), late.text
        assert (await client.get("/api/auth/email")).json()["address"] is None
    assert await EmailFactorRepository.get(user.id) is None


async def _session(client) -> dict:
    return (await client.get("/api/auth/session")).json()


# --- Sign-in links ------------------------------------------------------------------------


async def _account_with_links(mailbox: Mailbox):
    user = await create_user()
    async with make_client() as client:
        await login(client)
        await _verified(client, mailbox)
        await _opt_in(client, magic_link_enabled=True)
    email_factor.account_sends.clear()
    mailbox.mails.clear()
    return user


# A loopback address: this machine, so the mailed link may hang from it.
LOCAL = "http://127.0.0.1:8000"


def _link_client(**headers: str):
    client = make_client(base_url=LOCAL)
    client.headers.update(headers)
    return client


async def _wait_for_mail(mailbox: Mailbox) -> None:
    for _ in range(100):
        if mailbox.mails:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no mail")


async def test_a_sign_in_link_opens_one_session(mailbox, clock):
    await _smtp_profile()
    await _account_with_links(mailbox)
    async with _link_client() as client:
        assert (await client.get("/api/auth/session")).json()["methods"]["magic_link"] is True
        sent = await client.post(
            "/api/auth/magic-link", json={"username": ADMIN, "base": LOCAL + "/"}
        )
        assert sent.status_code == 200 and sent.json()["sent"] is True
        await _wait_for_mail(mailbox)
        link = mailbox.last_link()
        assert link.startswith(LOCAL + "/#magic_link=")
        token = link.split("=", 1)[1]
        done = await client.post("/api/auth/magic-link/verify", json={"token": token})
        assert done.status_code == 200, done.text
        assert done.json()["authenticated"] is True
        assert "meshloom_device=" not in done.headers.get("set-cookie", "")
        again = await client.post("/api/auth/magic-link/verify", json={"token": token})
        assert again.status_code == 401 and _code_of(again) == "auth_magic_link_invalid"


async def test_a_sign_in_link_expires_after_fifteen_minutes(mailbox, clock):
    await _smtp_profile()
    await _account_with_links(mailbox)
    async with _link_client() as client:
        await client.post("/api/auth/magic-link", json={"username": ADMIN})
        await _wait_for_mail(mailbox)
        token = mailbox.last_link().split("=", 1)[1]
        clock["t"] += email_factor.LINK_TTL_S + 1
        late = await client.post("/api/auth/magic-link/verify", json={"token": token})
        assert late.status_code == 401


async def test_the_answer_is_the_same_for_unknown_or_opted_out_names(mailbox):
    await _smtp_profile()
    await _account_with_links(mailbox)
    await create_user("bob", role="member")
    async with _link_client() as client:
        answers = [
            (await client.post("/api/auth/magic-link", json={"username": name})).json()
            for name in ("nobody", "bob")
        ]
        assert answers == [{"sent": True, "expires_in": 900}] * 2
        await asyncio.sleep(0.05)
        assert mailbox.mails == []


async def test_with_totp_on_a_sign_in_link_leads_to_the_totp_step(mailbox):
    await _smtp_profile()
    user = await _account_with_links(mailbox)
    secret = totp.new_secret()
    await TotpRepository.set_pending_secret(user.id, secret)
    assert await TotpRepository.enable(user.id, secret, 0, [])
    # Enabling TOTP moved updated_at: send the link afterwards.
    async with _link_client() as client:
        await client.post("/api/auth/magic-link", json={"username": ADMIN})
        await _wait_for_mail(mailbox)
        token = mailbox.last_link().split("=", 1)[1]
        body = (await client.post("/api/auth/magic-link/verify", json={"token": token})).json()
        assert body["authenticated"] is False
        assert body["second_factor"]["methods"] == ["totp", "recovery"]


async def test_an_account_change_voids_a_sign_in_link(mailbox):
    await _smtp_profile()
    user = await _account_with_links(mailbox)
    async with _link_client() as client:
        await client.post("/api/auth/magic-link", json={"username": ADMIN})
        await _wait_for_mail(mailbox)
        token = mailbox.last_link().split("=", 1)[1]
        await UserRepository.update(user.id, display_name="Changed")
        refused = await client.post("/api/auth/magic-link/verify", json={"token": token})
        assert refused.status_code == 401


async def _ask_link(client, **extra) -> dict:
    response = await client.post("/api/auth/magic-link", json={"username": ADMIN, **extra})
    assert response.status_code == 200, response.text
    return response.json()


async def _no_mail(mailbox: Mailbox) -> None:
    await asyncio.sleep(0.05)
    assert mailbox.mails == []


@pytest.mark.parametrize(
    ("base_url", "host"),
    [
        ("http://test", "evil.example"),
        # An IP literal that is not the address this connection reached.
        (LOCAL, "203.0.113.9"),
        (LOCAL, "192.168.1.66:8000"),
        # A local-looking name that is not this machine's.
        (LOCAL, "evil.local"),
    ],
)
async def test_a_forged_host_or_base_never_ends_up_in_the_mailed_link(mailbox, base_url, host):
    await _smtp_profile()
    await _account_with_links(mailbox)
    async with make_client(base_url=base_url) as client:
        client.headers["host"] = host
        for base in (f"http://{host}/", None):
            refused = await client.post(
                "/api/auth/magic-link", json={"username": ADMIN, "base": base}
            )
            assert refused.status_code == 400, refused.text
            assert _code_of(refused) == "auth_magic_link_origin_untrusted"
    await _no_mail(mailbox)


async def test_with_a_public_address_the_link_uses_it_whatever_the_host(mailbox):
    from app.repository.auth import AuthSettingsRepository

    await _smtp_profile()
    await _account_with_links(mailbox)
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.org/radio"])
    async with make_client() as client:
        client.headers["host"] = "evil.example"
        await _ask_link(client, base="http://evil.example/")
        await _wait_for_mail(mailbox)
        assert mailbox.last_link().startswith("https://mesh.example.org/radio/#magic_link=")
        assert "evil" not in mailbox.mails[-1][2]


async def test_the_machine_name_and_its_local_name_are_trusted(mailbox, clock, monkeypatch):
    from app.middleware import host_guard

    monkeypatch.setattr(host_guard, "_machine_names", lambda: frozenset({"meshbox"}))
    await _smtp_profile()
    await _account_with_links(mailbox)
    for host in ("meshbox", "meshbox.local:8000"):
        async with make_client() as client:
            client.headers["host"] = host
            await _ask_link(client, base=f"http://{host}/")
            await _wait_for_mail(mailbox)
            assert mailbox.last_link().startswith(f"http://{host}/#magic_link=")
        mailbox.mails.clear()
        clock["t"] += email_factor.SEND_INTERVAL_S + 1


async def test_a_verified_address_without_links_gets_no_mail(mailbox):
    await _smtp_profile()
    user = await _account_with_links(mailbox)
    assert await EmailFactorRepository.set_flags(user.id, magic_link_enabled=False)
    factor = await EmailFactorRepository.get(user.id)
    assert factor is not None and factor.address == ADDRESS
    async with _link_client() as client:
        assert await _ask_link(client) == {"sent": True, "expires_in": 900}
    await _no_mail(mailbox)


async def test_a_link_sent_before_links_are_turned_off_is_refused(mailbox, test_db):
    await _smtp_profile()
    user = await _account_with_links(mailbox)
    async with _link_client() as client:
        await _ask_link(client)
        await _wait_for_mail(mailbox)
        token = mailbox.last_link().split("=", 1)[1]
        # Only the opt-in changes (no account generation bump): the verify check alone.
        async with test_db.tx() as conn:
            await conn.execute(
                "UPDATE user_email_factor SET magic_link_enabled = 0 WHERE user_id = ?",
                (user.id,),
            )
        refused = await client.post("/api/auth/magic-link/verify", json={"token": token})
        assert refused.status_code == 401 and _code_of(refused) == "auth_magic_link_invalid"


async def test_sign_in_links_are_limited_per_account(mailbox, clock):
    await _smtp_profile()
    await _account_with_links(mailbox)
    # Each request from another client address: only the per-account limit applies.
    for index in range(email_factor.SEND_MAX_PER_HOUR):
        async with make_client(base_url=LOCAL, client=(f"10.0.0.{index + 1}", 5000)) as client:
            await _ask_link(client)
            await _wait_for_mail(mailbox)
            if index == 0:
                mailbox.mails.clear()
                # A second request within the minute: same answer, no mail.
                assert await _ask_link(client) == {"sent": True, "expires_in": 900}
                await _no_mail(mailbox)
        mailbox.mails.clear()
        clock["t"] += email_factor.SEND_INTERVAL_S + 1
    async with make_client(base_url=LOCAL, client=("10.0.0.99", 5000)) as client:
        assert await _ask_link(client) == {"sent": True, "expires_in": 900}
    await _no_mail(mailbox)


async def test_the_request_path_never_reads_the_account(mailbox, monkeypatch):
    """Timing: the user and factor lookups and the audit row (an insert and a commit)
    run in the background task, so the answer does the same work for any name. Held
    here until the answer is back: were any of them on the request path, it would
    hang."""
    await _smtp_profile()
    await _account_with_links(mailbox)
    gate = asyncio.Event()
    touched: list[str] = []

    def held(name, original):
        async def wrapper(*args, **kwargs):
            touched.append(name)
            await gate.wait()
            return await original(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(
        UserRepository, "get_by_username", held("user", UserRepository.get_by_username)
    )
    monkeypatch.setattr(EmailFactorRepository, "get", held("factor", EmailFactorRepository.get))
    monkeypatch.setattr(AuthAuditRepository, "add", held("audit", AuthAuditRepository.add))
    async with _link_client() as client:
        for name in (ADMIN, "nobody"):
            answer = await asyncio.wait_for(
                client.post("/api/auth/magic-link", json={"username": name}), timeout=2
            )
            assert answer.json() == {"sent": True, "expires_in": 900}
        assert mailbox.mails == []
        gate.set()
        await _wait_for_mail(mailbox)
    assert mailbox.mails[-1][0] == ADDRESS
    assert touched.count("user") == 2 and "audit" in touched
    rows = await AuthAuditRepository.list_recent(limit=50)
    pending = [row for row in rows if row.action == "login" and row.outcome == "pending"]
    assert len(pending) == 1 and pending[0].details == {"method": "magic_link"}
    assert pending[0].ip == "127.0.0.1"


async def test_link_requests_are_limited_per_client_address(mailbox):
    await _smtp_profile()
    await _account_with_links(mailbox)
    async with make_client(base_url=LOCAL, client=("10.0.0.5", 5000)) as client:
        for _ in range(email_factor.ADDRESS_MAX_PER_HOUR):
            # Any name: the limit is per address, before any account is read.
            assert await _ask_link(client, username="nobody") == {"sent": True, "expires_in": 900}
        over = await client.post("/api/auth/magic-link", json={"username": ADMIN})
        assert over.status_code == 429 and _code_of(over) == "auth_rate_limited"
        assert int(over.headers["retry-after"]) > 0
    await _no_mail(mailbox)
    async with make_client(base_url=LOCAL, client=("10.0.0.6", 5000)) as other:
        assert await _ask_link(other) == {"sent": True, "expires_in": 900}
        await _wait_for_mail(mailbox)


async def test_sign_in_link_redemptions_follow_the_login_delays_per_address(mailbox, monkeypatch):
    from app.auth import ratelimit

    # A frozen clock: the delay of the pair never runs out during the test.
    monkeypatch.setattr(ratelimit.login_limiter, "_clock", lambda: 5000.0)
    await _smtp_profile()
    await _account_with_links(mailbox)
    async with _link_client() as client:
        await _ask_link(client)
        await _wait_for_mail(mailbox)
    token = mailbox.last_link().split("=", 1)[1]
    async with make_client(base_url=LOCAL, client=("10.0.0.5", 5000)) as guesser:
        for index in range(ratelimit.PAIR_FREE_FAILURES):
            bad = await guesser.post("/api/auth/magic-link/verify", json={"token": f"x{index}"})
            assert bad.status_code == 401 and _code_of(bad) == "auth_magic_link_invalid"
        # Even the right token waits now, and is not used up by the refusal.
        delayed = await guesser.post("/api/auth/magic-link/verify", json={"token": token})
        assert delayed.status_code == 429 and _code_of(delayed) == "auth_rate_limited"
    async with make_client(base_url=LOCAL, client=("10.0.0.6", 5000)) as other:
        done = await other.post("/api/auth/magic-link/verify", json={"token": token})
        assert done.status_code == 200, done.text
        assert done.json()["authenticated"] is True


async def test_the_session_offers_links_only_where_the_origin_is_trusted(mailbox):
    from app.repository.auth import AuthSettingsRepository

    await _smtp_profile()
    await _account_with_links(mailbox)
    async with _link_client(host="192.168.1.66:8000") as client:
        # A LAN address that is not the one this connection reached: POST would be 400.
        assert (await _session(client))["methods"]["magic_link"] is False
        refused = await client.post("/api/auth/magic-link", json={"username": ADMIN})
        assert refused.status_code == 400
        assert _code_of(refused) == "auth_magic_link_origin_untrusted"
    async with _link_client() as client:
        assert (await _session(client))["methods"]["magic_link"] is True
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.org"])
    async with _link_client(host="192.168.1.66:8000") as client:
        assert (await _session(client))["methods"]["magic_link"] is True
        await _ask_link(client)
        await _wait_for_mail(mailbox)
    assert mailbox.last_link().startswith("https://mesh.example.org/#magic_link=")


def _relay_client(visitor: str):
    """Requests as the relay dispatches them: the visitor's address in ``client``
    and ``scope["state"]["via"] = "relay"`` (``app/services/remote_relay.py``)."""
    import httpx

    from app.main import app

    async def tagged(scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope.setdefault("state", {})["via"] = "relay"
        await app(scope, receive, send)

    transport = httpx.ASGITransport(app=tagged, client=(visitor, 50000))
    return httpx.AsyncClient(transport=transport, base_url="https://mesh.example.org")


async def test_relayed_link_requests_are_limited_per_visitor_address(mailbox, monkeypatch):
    from app.auth import ratelimit
    from app.repository.auth import AuthSettingsRepository

    # Behind Docker without a trusted proxy the LAN rules share one key; a relayed
    # request is still keyed by its visitor.
    monkeypatch.setattr(ratelimit.login_limiter, "address_rules_active", lambda: False)
    await _smtp_profile()
    await _account_with_links(mailbox)
    await AuthSettingsRepository.update(public_urls=["https://mesh.example.org"])
    async with _relay_client("198.51.100.7") as client:
        for _ in range(email_factor.ADDRESS_MAX_PER_HOUR):
            assert await _ask_link(client, username="nobody") == {"sent": True, "expires_in": 900}
        over = await client.post("/api/auth/magic-link", json={"username": ADMIN})
        assert over.status_code == 429 and _code_of(over) == "auth_rate_limited"
    async with _relay_client("198.51.100.8") as other:
        assert await _ask_link(other) == {"sent": True, "expires_in": 900}
        await _wait_for_mail(mailbox)
    assert set(email_factor.address_requests._keys) >= {
        ("address", ratelimit.client_address_key("198.51.100.7")),
        ("address", ratelimit.client_address_key("198.51.100.8")),
    }


def test_codes_and_masks():
    assert email_factor.normalize_address(" a@b.org ") == "a@b.org"
    assert email_factor.normalize_address("a@b") is None
    assert email_factor.normalize_address("a@b.org;c@d.org") is None
    assert email_factor.normalize_address("a@.org") is None
    assert email_factor.normalize_address("a@b.") is None
    assert email_factor.normalize_address("a" + "!" * 5000) is None
    assert email_factor.mask_address("owner@example.org") == "o***@example.org"
    codes = {email_factor.new_code() for _ in range(50)}
    assert all(len(c) == 6 and c.isdigit() for c in codes) and len(codes) > 40
    subject, html = email_factor.render("code", "fr", instance="<b>x</b>", code="123456")
    assert subject == "Votre code de connexion Meshloom"
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "123456" in html
