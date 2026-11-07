"""E-mail code (second factor) and sign-in links (MSHL-426).

Rules (``docs/dev/auth.md``, "E-mail code and sign-in links"):

- **SMTP profile.** No SMTP of its own: the mails go through one notification e-mail
  channel (type ``email``, ``app/notifications/channels.py``), chosen by an admin in
  Settings → Connection → E-mail sign-in (``email_sign_in.channel_id``), through
  ``send_message()`` of ``app/notifications/transports/email.py`` (egress guard, TLS and
  plaintext rules) with the account's address as the only recipient. No channel, a
  disabled one, a legacy one or one whose secret cannot be read: ``EmailUnavailable``
  (``auth_email_unavailable``), and both features are refused with that error.
- **Opt-in per account.** An account verifies its address with a code sent to it
  (``AddressChallenges``), then turns on the e-mail code after the password
  (``otp_enabled``), sign-in links (``magic_link_enabled``), or both
  (``app/repository/email_factor.py``). Never required.
- **E-mail code.** ``CODE_DIGITS`` digits from ``secrets``, ``CODE_TTL_S`` (10 min),
  single use, only its SHA-256 kept, compared in constant time. It belongs to one
  pending login (``app/auth/totp.py``, ``PendingLogin.email_code``), so the pending
  login's budget of five wrong codes applies, whatever the kind of code.
- **Sign-in links.** A 256-bit token (``MagicLinks``, in memory, ``LINK_TTL_S``,
  15 min, single use, only its SHA-256 kept) in the fragment of the app's address
  (``#magic_link=``): a mail scanner fetching the link never sends it to a server.
  The page posts it to ``POST /auth/magic-link/verify``.
- **Sending limits** (``SendLimiter``): per account ``SEND_INTERVAL_S`` between two
  mails and ``SEND_MAX_PER_HOUR``; per client address ``ADDRESS_MAX_PER_HOUR`` link
  requests. A restart forgets codes, links and counters.
- **Never log** a code, a token or a link.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from html import escape
from typing import Literal

from app.auth.tokens import hash_token, new_token

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
CODE_TTL_S = 600.0
CODE_MAX_ATTEMPTS = 5
LINK_TTL_S = 900.0
LINK_MAX_ENTRIES = 1024
ADDRESS_CHALLENGE_MAX_ENTRIES = 256
# The resends a pending login may ask for (the first mail included).
PENDING_MAX_SENDS = 3

SEND_INTERVAL_S = 60.0
SEND_MAX_PER_HOUR = 5
ADDRESS_MAX_PER_HOUR = 20
_HOUR_S = 3600.0
LIMITER_MAX_KEYS = 4096

# The fragment key the app reads a sign-in link from (``frontend/src/auth/magicLink.ts``).
LINK_FRAGMENT = "magic_link"

MailLanguage = Literal["fr", "en"]
MailKind = Literal["code", "verify", "link"]

# Characters an address may not hold: separators the transport splits on, quoting, @.
_ADDRESS_FORBIDDEN = frozenset(",;<>\"'()[]\\@")
_CODE = re.compile(rf"[0-9]{{{CODE_DIGITS}}}")

# Wall clock of the in-memory stores (tests pin it).
clock: Callable[[], float] = time.monotonic


class EmailUnavailable(Exception):
    """No usable SMTP profile: ``auth_email_unavailable``."""

    code = "auth_email_unavailable"


class EmailSendFailed(Exception):
    """The SMTP send failed: ``auth_email_send_failed`` (``reason``: the transport's code)."""

    code = "auth_email_send_failed"

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --- Codes and addresses ------------------------------------------------------------


def normalize_address(raw: str) -> str | None:
    """A single address, trimmed, or None. Commas, semicolons and line breaks are
    refused: the mail transport would read them as several recipients."""
    address = raw.strip()
    if not address or len(address) > 254:
        return None
    # A plain scan, not a regular expression: linear on any input (no ReDoS).
    local, at, domain = address.partition("@")
    if not at or not local or not _plain(local) or not _plain(domain):
        return None
    head, dot, tail = domain.rpartition(".")
    if not dot or not head or not tail:
        return None
    return address


def _plain(part: str) -> bool:
    return all(not c.isspace() and c not in _ADDRESS_FORBIDDEN for c in part)


def mask_address(address: str) -> str:
    """``o***@example.org``: enough for the owner to recognise it."""
    local, _, domain = address.partition("@")
    return f"{local[:1]}***@{domain}"


def new_code() -> str:
    return f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"


def normalize_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code)


def is_code(code: str) -> bool:
    return _CODE.fullmatch(normalize_code(code)) is not None


def _digest(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode("ascii", "replace")).hexdigest()


@dataclass
class CodeChallenge:
    """One code sent by mail: its SHA-256, its expiry, the wrong tries."""

    digest: str
    expires_at: float
    attempts: int = 0

    @classmethod
    def issue(cls, code: str) -> CodeChallenge:
        return cls(digest=_digest(code), expires_at=clock() + CODE_TTL_S)

    @property
    def live(self) -> bool:
        return self.attempts < CODE_MAX_ATTEMPTS and self.expires_at > clock()

    def check(self, code: str) -> bool:
        """True once for the right code while it lives; a wrong one counts."""
        if not self.live or not is_code(code):
            self.attempts += 1
            return False
        if hmac.compare_digest(self.digest, _digest(code)):
            # Single use: spent at once.
            self.attempts = CODE_MAX_ATTEMPTS
            return True
        self.attempts += 1
        return False


@dataclass
class AddressChallenge:
    address: str
    challenge: CodeChallenge


class AddressChallenges:
    """The address an account is verifying and the code sent to it (one per account)."""

    def __init__(self) -> None:
        self._entries: dict[int, AddressChallenge] = {}

    def issue(self, user_id: int, address: str, code: str) -> None:
        self._entries.pop(user_id, None)
        now = clock()
        for key in [k for k, v in self._entries.items() if v.challenge.expires_at <= now]:
            self._entries.pop(key, None)
        while len(self._entries) >= ADDRESS_CHALLENGE_MAX_ENTRIES:
            self._entries.pop(next(iter(self._entries)))
        self._entries[user_id] = AddressChallenge(address, CodeChallenge.issue(code))

    def pending(self, user_id: int) -> str | None:
        entry = self._entries.get(user_id)
        return entry.address if entry is not None and entry.challenge.live else None

    def check(self, user_id: int, code: str) -> str | None:
        """The verified address for the right code, once; None otherwise."""
        entry = self._entries.get(user_id)
        if entry is None:
            return None
        ok = entry.challenge.check(code)
        if ok or not entry.challenge.live:
            self._entries.pop(user_id, None)
        return entry.address if ok else None

    def discard(self, user_id: int) -> None:
        self._entries.pop(user_id, None)


# --- Sign-in links ------------------------------------------------------------------


@dataclass
class MagicLink:
    user_id: int
    # ``users.updated_at`` when the link was sent: any account change voids it.
    generation: int
    remember: bool
    expires_at: float


class MagicLinks:
    """Sign-in links waiting for their click, keyed by the token's SHA-256."""

    def __init__(self) -> None:
        self._entries: dict[str, MagicLink] = {}

    def issue(self, user_id: int, generation: int, remember: bool) -> str:
        now = clock()
        for key in [k for k, v in self._entries.items() if v.expires_at <= now]:
            self._entries.pop(key, None)
        while len(self._entries) >= LINK_MAX_ENTRIES:
            self._entries.pop(next(iter(self._entries)))
        token = new_token()
        self._entries[hash_token(token)] = MagicLink(
            user_id=user_id, generation=generation, remember=remember, expires_at=now + LINK_TTL_S
        )
        return token

    def take(self, token: str) -> MagicLink | None:
        """Single use: removed whether or not it is still valid."""
        entry = self._entries.pop(hash_token(token), None)
        if entry is None or entry.expires_at <= clock():
            return None
        return entry

    def __len__(self) -> int:
        return len(self._entries)


# --- Sending limits -----------------------------------------------------------------


@dataclass
class _Sends:
    times: deque[float] = field(default_factory=deque)


class SendLimiter:
    """At most ``per_hour`` mails a key, ``interval_s`` apart. ``allow()`` takes the slot."""

    def __init__(self, *, interval_s: float, per_hour: int) -> None:
        self.interval_s = interval_s
        self.per_hour = per_hour
        self._keys: dict[object, _Sends] = {}

    def retry_after(self, key: object) -> int:
        """Whole seconds before ``key`` may send again (0: now)."""
        now = clock()
        entry = self._keys.get(key)
        if entry is None:
            return 0
        while entry.times and entry.times[0] <= now - _HOUR_S:
            entry.times.popleft()
        wait = 0.0
        if entry.times:
            wait = max(wait, entry.times[-1] + self.interval_s - now)
        if len(entry.times) >= self.per_hour:
            wait = max(wait, entry.times[0] + _HOUR_S - now)
        return max(0, int(wait + 0.999)) if wait > 0 else 0

    def allow(self, key: object) -> bool:
        if self.retry_after(key):
            return False
        now = clock()
        if key not in self._keys:
            for stale in [
                k for k, v in self._keys.items() if not v.times or v.times[-1] <= now - _HOUR_S
            ]:
                self._keys.pop(stale, None)
            while len(self._keys) >= LIMITER_MAX_KEYS:
                self._keys.pop(next(iter(self._keys)))
        self._keys.setdefault(key, _Sends()).times.append(now)
        return True

    def clear(self) -> None:
        self._keys.clear()


magic_links = MagicLinks()
address_challenges = AddressChallenges()
account_sends = SendLimiter(interval_s=SEND_INTERVAL_S, per_hour=SEND_MAX_PER_HOUR)
address_requests = SendLimiter(interval_s=0.0, per_hour=ADDRESS_MAX_PER_HOUR)


def reset_state() -> None:
    """Tests: forget links, address codes and counters."""
    magic_links._entries.clear()
    address_challenges._entries.clear()
    account_sends.clear()
    address_requests.clear()


# --- The SMTP profile ---------------------------------------------------------------


def usable_channel(channel) -> bool:
    """A native e-mail channel (not a 4.18.x ``legacy:email``) whose secrets read."""
    return (
        channel.type == "email"
        and not channel.id.startswith("legacy:")
        and not channel.secret_unreadable
    )


async def _profile():
    from app.repository.email_factor import EmailSignInRepository
    from app.repository.notification_channels import NotificationChannelRepository

    try:
        channel_id = await EmailSignInRepository.channel_id()
    except Exception:
        logger.warning("E-mail sign-in settings unreadable: e-mail sign-in off")
        raise EmailUnavailable from None
    if not channel_id:
        raise EmailUnavailable
    found = await NotificationChannelRepository.get_for_send(channel_id)
    if found is None:
        raise EmailUnavailable
    channel, channel_secrets = found
    if not channel.enabled or not usable_channel(channel) or not channel.config.get("host"):
        raise EmailUnavailable
    return channel, channel_secrets


async def available() -> bool:
    """Whether a usable SMTP profile is set (the routes refuse otherwise)."""
    try:
        await _profile()
    except EmailUnavailable:
        return False
    return True


async def deliver(address: str, subject: str, html: str) -> None:
    """Send one sign-in mail to ``address``. ``EmailUnavailable`` without a profile,
    ``EmailSendFailed`` when the SMTP send fails. The seam the tests replace."""
    from app.notifications.transports.email import send_message

    channel, channel_secrets = await _profile()
    result = await send_message(channel, channel_secrets, subject, html, to=address)
    if not result.ok:
        raise EmailSendFailed(result.error_code or "send_failed")


# --- The mails ----------------------------------------------------------------------

_COPY: dict[MailLanguage, dict[str, str]] = {
    "en": {
        "code_subject": "Your Meshloom sign-in code",
        "code_intro": "Your code to finish signing in to {instance}:",
        "verify_subject": "Confirm your e-mail address for Meshloom",
        "verify_intro": "Your code to confirm this address for {instance}:",
        "link_subject": "Your Meshloom sign-in link",
        "link_intro": "Open this link to sign in to {instance}:",
        "link_button": "Sign in",
        "code_ttl": "It works once, for 10 minutes.",
        "link_ttl": "It works once, for 15 minutes.",
        "footer": "You did not ask for this? Ignore this mail: nothing happens without it.",
    },
    "fr": {
        "code_subject": "Votre code de connexion Meshloom",
        "code_intro": "Votre code pour terminer la connexion à {instance} :",
        "verify_subject": "Confirmez votre adresse e-mail pour Meshloom",
        "verify_intro": "Votre code pour confirmer cette adresse sur {instance} :",
        "link_subject": "Votre lien de connexion Meshloom",
        "link_intro": "Ouvrez ce lien pour vous connecter à {instance} :",
        "link_button": "Se connecter",
        "code_ttl": "Il ne sert qu'une fois, pendant 10 minutes.",
        "link_ttl": "Il ne sert qu'une fois, pendant 15 minutes.",
        "footer": "Vous n'avez rien demandé ? Ignorez ce message : rien ne se passe sans lui.",
    },
}


def render(
    kind: MailKind,
    lang: MailLanguage,
    *,
    instance: str | None,
    code: str | None = None,
    link: str | None = None,
) -> tuple[str, str]:
    """(subject, HTML body) of a sign-in mail in ``lang``. Every value is escaped."""
    from app.notifications.render import LOGO_CID

    copy = _COPY["en" if lang == "en" else "fr"]
    name = instance or "Meshloom"
    if kind == "link":
        assert link is not None
        intro = copy["link_intro"].format(instance=name)
        ttl = copy["link_ttl"]
        subject = copy["link_subject"]
        main = (
            f'<p style="margin:0 0 16px 0;"><a href="{escape(link, quote=True)}" '
            'style="display:inline-block;padding:10px 18px;border-radius:8px;background:#3b82f6;'
            f'color:#ffffff;text-decoration:none;font-weight:700;">{escape(copy["link_button"])}</a></p>'
            f'<p style="margin:0 0 8px 0;font-size:12px;word-break:break-all;color:#8b8b96;">{escape(link)}</p>'
        )
    else:
        assert code is not None
        intro = copy[f"{kind}_intro"].format(instance=name)
        ttl = copy["code_ttl"]
        subject = copy[f"{kind}_subject"]
        main = (
            '<p style="margin:0 0 16px 0;font-family:monospace;font-size:28px;letter-spacing:0.2em;'
            f'color:#f4f4f5;">{escape(code)}</p>'
        )
    html = f"""<!DOCTYPE html>
<html lang="{lang}">
<body style="margin:0;padding:0;background:#07080c;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#07080c;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#111318;border:1px solid #2a2d36;border-radius:16px;">
        <tr><td style="padding:22px 24px 18px 24px;border-bottom:1px solid #2a2d36;font-family:Arial,Helvetica,sans-serif;font-size:18px;font-weight:700;color:#f4f4f5;">
          <img src="cid:{escape(LOGO_CID, quote=True)}" width="40" height="33" alt="" style="vertical-align:middle;border:0;width:40px;height:auto;margin-right:12px;" />Meshloom
        </td></tr>
        <tr><td style="padding:24px;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.5;color:#c4c4cc;">
          <p style="margin:0 0 16px 0;">{escape(intro)}</p>
          {main}
          <p style="margin:0;">{escape(ttl)}</p>
        </td></tr>
        <tr><td style="padding:16px 24px 22px 24px;border-top:1px solid #2a2d36;font-family:Arial,Helvetica,sans-serif;font-size:12px;color:#8b8b96;">{escape(copy["footer"])}</td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""
    return subject, html
