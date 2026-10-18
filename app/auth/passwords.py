"""Password hashing: argon2id from PyNaCl (libsodium ``crypto_pwhash_str``).

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
