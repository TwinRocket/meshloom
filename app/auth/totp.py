"""The TOTP second factor (RFC 6238, pyotp) and its recovery codes (Access T50).

Rules (``docs/dev/auth.md``, "Second factor"):

- **Secret.** ``pyotp.random_base32()`` (160 bits), stored in
  ``users.totp_secret``. It is returned once, by ``POST /auth/2fa/enrol``, and by
  no other route: ``GET`` payloads only say whether the factor is on. Stored as
  base32 until the instance secrets key (Notifications T32, ``data/secrets.key``)
  exists; encrypting it then is a follow-up. The SQLite download nulls it, and
  the JSON backup carries it only inside the ``users`` section ("Include
  accounts").
- **Codes.** 6 digits, 30 s steps, one step of drift either way
  (``VALID_WINDOW``). The accepted step is stored in ``users.totp_last_counter``
  and a code is accepted only for a later step (``TotpRepository.claim_counter``,
  one conditional ``UPDATE``): a code already used, or an earlier one, is refused
  even inside its 90 s window.
- **Recovery codes.** ``RECOVERY_CODE_COUNT`` codes of 12 characters (about 59
  bits each, shown once, ``xxxx-xxxx-xxxx``), hashed with argon2id (PyNaCl) at a
  light cost (``RECOVERY_HASH_OPSLIMIT`` / ``RECOVERY_HASH_MEMLIMIT``): the codes
  are random, so the hash only has to make a stolen table useless, and a login
  checks up to ten of them. Each one works once (``TotpRepository.use_code``).
- **Pending logins.** A correct password for an account with the factor on opens
  no session: ``POST /auth/login`` answers a pending-login token
  (``PendingLogins``, in memory, ``PENDING_TTL_S``, ``PENDING_MAX_ATTEMPTS``
  wrong codes), and ``POST /auth/login/2fa`` exchanges it with a code for the
  session. The token is bound to the first step's client (``client_binding``:
  the address the trusted-proxy interpreter resolved, and the User-Agent), is
  taken out of the table while a code is checked (two concurrent requests
  cannot both use it) and put back only after a wrong code. Any change to the
  account (``updated_at``) voids the token. A restart forgets them all (sign in
  again).
- **Never log** a secret, a code, a recovery code or a pending token.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import nacl.exceptions
import nacl.pwhash.argon2id as argon2id
import pyotp

from app.auth.tokens import hash_token, new_token
from app.repository.auth import TotpRepository

DIGITS = 6
PERIOD_S = 30
VALID_WINDOW = 1
ISSUER_FALLBACK = "Meshloom"

RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_LENGTH = 12
# No 0/o, 1/l/i: the codes are read off paper.
RECOVERY_ALPHABET = "23456789abcdefghjkmnpqrstuvwxyz"
RECOVERY_HASH_OPSLIMIT = 2
RECOVERY_HASH_MEMLIMIT = 1024 * 1024

PENDING_TTL_S = 300.0
PENDING_MAX_ATTEMPTS = 5
PENDING_MAX_ENTRIES = 1024

FactorKind = Literal["totp", "recovery"]

# Wall clock of the code checks (tests pin it).
clock: Callable[[], float] = time.time

_TOTP_CODE = re.compile(r"[0-9]{6}")
_RECOVERY_CODE = re.compile(f"[{RECOVERY_ALPHABET}]{{{RECOVERY_CODE_LENGTH}}}")


def new_secret() -> str:
    return pyotp.random_base32()


def _totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret, digits=DIGITS, interval=PERIOD_S)


def provisioning_uri(secret: str, username: str, issuer: str | None) -> str:
    """The ``otpauth://`` URI an authenticator app scans."""
    return _totp(secret).provisioning_uri(name=username, issuer_name=issuer or ISSUER_FALLBACK)


def normalize_code(code: str) -> str:
    """Spaces and dashes removed, lower case: ``123 456`` and ``ABCD-EFGH-JKMN`` pass."""
    return re.sub(r"[\s-]", "", code).lower()


def is_totp_code(code: str) -> bool:
    return _TOTP_CODE.fullmatch(normalize_code(code)) is not None


def is_recovery_code(code: str) -> bool:
    return _RECOVERY_CODE.fullmatch(normalize_code(code)) is not None


def matching_counter(
    secret: str, code: str, *, now: float | None = None, last_counter: int | None = None
) -> int | None:
    """The time step ``code`` belongs to (current step, or one either side), or
    None. Steps at or before ``last_counter`` never match (replay)."""
    code = normalize_code(code)
    if _TOTP_CODE.fullmatch(code) is None:
        return None
    totp = _totp(secret)
    moment = datetime.datetime.fromtimestamp(clock() if now is None else now, tz=datetime.UTC)
    current = totp.timecode(moment)
    for counter in range(current - VALID_WINDOW, current + VALID_WINDOW + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(totp.generate_otp(counter), code):
            return counter
    return None


def new_recovery_codes() -> list[str]:
    """Fresh codes in display form (``xxxx-xxxx-xxxx``)."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(RECOVERY_CODE_LENGTH))
        codes.append("-".join(raw[i : i + 4] for i in range(0, RECOVERY_CODE_LENGTH, 4)))
    return codes


def hash_recovery_code(code: str) -> str:
    return argon2id.str(
        normalize_code(code).encode("ascii"),
        opslimit=RECOVERY_HASH_OPSLIMIT,
        memlimit=RECOVERY_HASH_MEMLIMIT,
    ).decode("ascii")


async def hash_recovery_codes(codes: list[str]) -> list[str]:
    return await asyncio.to_thread(lambda: [hash_recovery_code(code) for code in codes])


def _recovery_code_matches(stored: str, code: str) -> bool:
    try:
        return argon2id.verify(stored.encode("ascii"), code.encode("ascii"))
    except (nacl.exceptions.InvalidkeyError, nacl.exceptions.CryptoError, ValueError):
        return False


async def verify_code(
    user_id: int, code: str, *, allow_recovery: bool = True, now: float | None = None
) -> FactorKind | None:
    """Check a TOTP code (or, with ``allow_recovery``, a recovery code) for an
    account whose factor is on, and spend it. None when it does not check, was
    already used, or the account has no enabled factor (fail closed)."""
    state = await TotpRepository.get_state(user_id)
    if state is None or state.enabled_at is None or state.secret is None:
        return None
    normalized = normalize_code(code)
    if _TOTP_CODE.fullmatch(normalized):
        counter = matching_counter(
            state.secret.get_secret_value(),
            normalized,
            now=now,
            last_counter=state.last_counter,
        )
        if counter is not None and await TotpRepository.claim_counter(user_id, counter):
            return "totp"
        return None
    if not allow_recovery or _RECOVERY_CODE.fullmatch(normalized) is None:
        return None
    stored = await TotpRepository.unused_codes(user_id)
    for code_id, code_hash in stored:
        if await asyncio.to_thread(_recovery_code_matches, code_hash, normalized):
            if await TotpRepository.use_code(user_id, code_id):
                return "recovery"
            return None
    return None


# --- Pending logins -------------------------------------------------------------


@dataclass
class PendingLogin:
    user_id: int
    # ``users.updated_at`` when the password checked: any later account change voids it.
    generation: int
    # ``client_binding()`` of the first step: another client cannot use the token.
    client: str
    expires_at: float
    attempts: int = 0
    # "Remember me" of the password step, applied to the session the code opens.
    remember: bool = False


def client_binding(ip: str | None, user_agent: str | None) -> str:
    """The client a pending login is bound to: its address (``client_ip()``, as
    the trusted-proxy interpreter resolved it) and its User-Agent, hashed."""
    material = f"{ip or ''}\n{(user_agent or '')[:512]}"
    return hashlib.sha256(material.encode("utf-8", "surrogatepass")).hexdigest()


class PendingLogins:
    """Password checked, second factor still owed. Keyed by the SHA-256 of the
    token handed to the client; bounded (``PENDING_MAX_ENTRIES``, oldest first)."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, PendingLogin] = {}

    def issue(self, user_id: int, generation: int, client: str, remember: bool = False) -> str:
        now = self._clock()
        self._sweep(now)
        while len(self._entries) >= PENDING_MAX_ENTRIES:
            self._entries.pop(next(iter(self._entries)))
        token = new_token()
        self._entries[hash_token(token)] = PendingLogin(
            user_id=user_id,
            generation=generation,
            client=client,
            remember=remember,
            expires_at=now + PENDING_TTL_S,
        )
        return token

    def get(self, token: str) -> PendingLogin | None:
        key = hash_token(token)
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= self._clock():
            self._entries.pop(key, None)
            return None
        return entry

    def take(self, token: str) -> PendingLogin | None:
        """Remove and return a live entry, so a concurrent request with the same
        token finds nothing while this one checks its code."""
        entry = self.get(token)
        if entry is not None:
            self._entries.pop(hash_token(token), None)
        return entry

    def put_back(self, token: str, entry: PendingLogin, *, failed: bool) -> None:
        """Return a taken entry after a check that opened no session; a wrong
        code (``failed``) counts, and the token is void after
        ``PENDING_MAX_ATTEMPTS``."""
        if failed:
            entry.attempts += 1
        if entry.attempts >= PENDING_MAX_ATTEMPTS or entry.expires_at <= self._clock():
            return
        self._entries[hash_token(token)] = entry

    def discard(self, token: str) -> None:
        self._entries.pop(hash_token(token), None)

    def __len__(self) -> int:
        return len(self._entries)

    def _sweep(self, now: float) -> None:
        for key in [k for k, entry in self._entries.items() if entry.expires_at <= now]:
            self._entries.pop(key, None)


pending_logins = PendingLogins()


def reset_pending_logins() -> None:
    """Tests: forget every pending login."""
    pending_logins._entries.clear()
