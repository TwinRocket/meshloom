"""Password hashing (argon2id from PyNaCl, libsodium ``crypto_pwhash_str``) and the password policy.

The stored value is libsodium's encoded string
(``$argon2id$v=19$m=<KiB>,t=<passes>,p=1$<salt>$<hash>``): it carries its own
salt and parameters, so a verify never needs the current ones, and
``needs_rehash()`` tells the caller to store a new hash after a successful
login once ``PARAMS`` change. libsodium compares in constant time.

``PARAMS`` is libsodium's INTERACTIVE level (2 passes, 64 MiB): the targets
include Raspberry Pi boards with 512 MB, and the login path runs at most two
verifications at once (about 128 MiB at peak). Measured costs are in
``docs/dev/auth.md``. Each call blocks for that long: call the async variants
from the event loop, they run in a worker thread. Limiting how many run at once
(the login limiter, a semaphore) is the caller's job.

Never log a password or a hash, and never put either in an exception message.
"""

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

import nacl.exceptions
import nacl.pwhash.argon2id as argon2id


@dataclass(frozen=True)
class Argon2Params:
    opslimit: int
    memlimit: int


# The one place the cost is set. Raising it later is safe: needs_rehash()
# flags older hashes and the caller re-hashes them after a successful login.
PARAMS = Argon2Params(
    opslimit=argon2id.OPSLIMIT_INTERACTIVE, memlimit=argon2id.MEMLIMIT_INTERACTIVE
)

# A bound on the input, not a policy: argon2id hashes any length, but nothing
# legitimate is longer and a cap keeps a huge body from being hashed at all.
MAX_PASSWORD_LENGTH = 1024

_PREFIX = "$argon2id$"
_PARAMS = re.compile(r"^\$argon2id\$v=(\d+)\$m=(\d+),t=(\d+),p=(\d+)\$")


def _encode(password: str) -> bytes:
    # NFC so a password typed on two keyboards (composed or decomposed accents)
    # hashes the same.
    return unicodedata.normalize("NFC", password).encode("utf-8")


def hash_password(password: str, *, params: Argon2Params = PARAMS) -> str:
    """Return the encoded argon2id hash of ``password``. Blocking."""
    if not password:
        raise ValueError("empty password")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError("password too long")
    return argon2id.str(
        _encode(password), opslimit=params.opslimit, memlimit=params.memlimit
    ).decode("ascii")


def verify_password(password_hash: str | None, password: str) -> bool:
    """True when ``password`` matches ``password_hash``. Blocking.

    False for a missing or malformed hash (ingress and kiosk identities have
    none) and for an empty or oversized password, never an exception.
    """
    if not password_hash or not password_hash.startswith(_PREFIX):
        return False
    if not password or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        return argon2id.verify(password_hash.encode("ascii"), _encode(password))
    except (nacl.exceptions.InvalidkeyError, nacl.exceptions.CryptoError, ValueError):
        return False


def needs_rehash(password_hash: str | None, *, params: Argon2Params = PARAMS) -> bool:
    """True when ``password_hash`` was not made with argon2id at these parameters."""
    if not password_hash:
        return True
    match = _PARAMS.match(password_hash)
    if match is None:
        return True
    version, mem_kib, passes, lanes = (int(group) for group in match.groups())
    return (version, mem_kib, passes, lanes) != (19, params.memlimit // 1024, params.opslimit, 1)


async def hash_password_async(password: str, *, params: Argon2Params = PARAMS) -> str:
    """``hash_password`` in a worker thread, off the event loop."""
    return await asyncio.to_thread(hash_password, password, params=params)


async def verify_password_async(password_hash: str | None, password: str) -> bool:
    """``verify_password`` in a worker thread, off the event loop."""
    return await asyncio.to_thread(verify_password, password_hash, password)


# --- Password policy ---------------------------------------------------------
#
# At least 12 characters, not the username, not a common password (cadrage
# §4.2). The list holds 100 common passwords that are 12 characters or more:
# the usual top-100 lists are almost all shorter, so the length rule already
# refuses them. Keyboard walks (QWERTY, AZERTY, QWERTZ), digit runs, and
# breach-list words with digits appended, in English and French, plus a few
# names a Meshloom user would reach for. Compared case-insensitively. No
# zxcvbn on purpose (no i18n feedback, 400 kB of word lists).

MIN_PASSWORD_LENGTH = 12

PasswordPolicyReason = Literal["too_short", "too_long", "same_as_username", "too_common"]

COMMON_PASSWORDS: frozenset[str] = frozenset(
    [
        "123456789012",
        "1234567890123",
        "12345678901234",
        "123456789123",
        "123456123456",
        "123123123123",
        "123412341234",
        "111111111111",
        "000000000000",
        "999999999999",
        "112233445566",
        "121212121212",
        "987654321098",
        "098765432109",
        "123456654321",
        "111222333444",
        "qwertyuiopas",
        "qwertyuiop12",
        "qwertyuiop123",
        "qwerty123456",
        "qwertyqwerty",
        "qwerty1234567",
        "1qaz2wsx3edc",
        "1qaz2wsx3edc4rfv",
        "1q2w3e4r5t6y",
        "1q2w3e4r5t6y7u8i",
        "q1w2e3r4t5y6",
        "zaq12wsxcde3",
        "qazwsxedcrfv",
        "qazwsxedc123",
        "1qazxsw23edc",
        "asdfghjkl123",
        "zxcvbnm12345",
        "azertyuiop12",
        "azertyuiop123",
        "azerty123456",
        "azertyazerty",
        "qwertzuiop12",
        "1234qwerasdf",
        "qwer1234asdf",
        "abc123abc123",
        "abcdefghijkl",
        "abcdefgh1234",
        "abcdefg12345",
        "abcd1234abcd",
        "aaaaaaaaaaaa",
        "a1b2c3d4e5f6",
        "password1234",
        "password12345",
        "password123456",
        "passwordpassword",
        "password2024",
        "password2025",
        "password2026",
        "p@ssw0rd1234",
        "p@ssword1234",
        "motdepasse12",
        "motdepasse123",
        "motdepasse1234",
        "iloveyou1234",
        "iloveyou12345",
        "iloveyouiloveyou",
        "letmein12345",
        "welcome12345",
        "welcome123456",
        "administrator",
        "administrator1",
        "admin1234567",
        "admin123456789",
        "changeme1234",
        "changeme12345",
        "sunshine1234",
        "football1234",
        "baseball1234",
        "princess1234",
        "superman1234",
        "starwars1234",
        "dragon123456",
        "monkey123456",
        "master123456",
        "lovelovelove",
        "helloworld12",
        "helloworld123",
        "hello1234567",
        "computer1234",
        "internet1234",
        "bonjour12345",
        "soleil123456",
        "chocolat1234",
        "doudou123456",
        "loulou123456",
        "jetaime12345",
        "meshcore1234",
        "meshcore12345",
        "meshloom1234",
        "meshloom12345",
        "meshtastic123",
        "raspberry123",
        "homeassistant",
        "homeassistant1",
    ]
)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", text).casefold())


def password_policy_violation(password: str, username: str) -> PasswordPolicyReason | None:
    """Why ``password`` is refused for ``username``, or None when it is acceptable.

    Length is counted in characters after NFC, the form that is hashed.
    """
    normalized = unicodedata.normalize("NFC", password)
    if len(normalized) < MIN_PASSWORD_LENGTH:
        return "too_short"
    if len(normalized) > MAX_PASSWORD_LENGTH:
        return "too_long"
    folded = _fold(normalized)
    if folded == _fold(username.strip()):
        return "same_as_username"
    if folded in COMMON_PASSWORDS:
        return "too_common"
    return None
