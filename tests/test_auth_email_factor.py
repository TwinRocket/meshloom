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


async def _wait_for_mail(mailbox: Mailbox) -> None:
    for _ in range(100):
        if mailbox.mails:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("no mail")


async def test_a_sign_in_link_opens_one_session(mailbox, clock):
    await _smtp_profile()
    await _account_with_links(mailbox)
    async with make_client() as client:
        assert (await client.get("/api/auth/session")).json()["methods"]["magic_link"] is True
        sent = await client.post(
            "/api/auth/magic-link", json={"username": ADMIN, "base": "http://test/"}
        )
        assert sent.status_code == 200 and sent.json()["sent"] is True
        await _wait_for_mail(mailbox)
        link = mailbox.last_link()
        assert link.startswith("http://test/#magic_link=")
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
    async with make_client() as client:
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
    async with make_client() as client:
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
    async with make_client() as client:
        await client.post("/api/auth/magic-link", json={"username": ADMIN})
        await _wait_for_mail(mailbox)
        token = mailbox.last_link().split("=", 1)[1]
        body = (await client.post("/api/auth/magic-link/verify", json={"token": token})).json()
        assert body["authenticated"] is False
        assert body["second_factor"]["methods"] == ["totp", "recovery"]


async def test_an_account_change_voids_a_sign_in_link(mailbox):
    await _smtp_profile()
    user = await _account_with_links(mailbox)
    async with make_client() as client:
        await client.post("/api/auth/magic-link", json={"username": ADMIN})
        await _wait_for_mail(mailbox)
        token = mailbox.last_link().split("=", 1)[1]
        await UserRepository.update(user.id, display_name="Changed")
        refused = await client.post("/api/auth/magic-link/verify", json={"token": token})
        assert refused.status_code == 401


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
