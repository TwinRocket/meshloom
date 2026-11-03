"""Instance identities: one Ed25519 key per service, owned by this Meshloom.

Each service (``share`` for links and files, ``remote`` for Meshloom Remote) has its
own random 32-byte seed in ``app_settings.<service>_instance_seed``, created by
``secrets.token_bytes`` on first use and never derived from the radio key. The
public key is a pseudonym unrelated to the radio's identity in the Community
directory, and two services' keys cannot be linked to each other.

The seed never leaves the server: not in ``AppSettings``, not in an API
response, not in a log line, not in a backup (``docs/dev/backup.md``, rule 4).
Every restore regenerates it through the ``on_restore`` hook registered below.

Tokens use the Community wire format (``_generate_jwt_token`` in
``app/fanout/community_mqtt.py``, signed here with PyNaCl instead of the
expanded radio key). A share creation token may carry ``acct``, the pseudonymous id
of the local account behind it (``share_account_id``, MSHL-445). The Remote key also
signs the device passes of the edge lock (``sign_with_instance_key``; the pass format
and its rules are in ``app/services/remote_passes.py``). Never log a token or a pass either.
"""

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
from typing import Any

from nacl.signing import SigningKey

from app.fanout.community_mqtt import _generate_jwt_token
from app.repository import AppSettingsRepository
from app.repository.settings import INSTANCE_SEED_SERVICES
from app.services.backup_sections import RestoreSource, register_on_restore
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

SEED_BYTES = 32

SHARE_SERVICE = "share"
SHARE_AUDIENCE = "meshloom-share"
# The links-files contract allows exp - iat <= 120 s; keep a margin under it.
SHARE_TOKEN_LIFETIME = 110

REMOTE_SERVICE = "remote"
REMOTE_AUDIENCE = "relay.meshloom.link"
# ``acct`` (links-files.md, "Instance JWT"): [A-Za-z0-9_-]{1,64}. 16 bytes of an HMAC in
# base64url without padding are 22 characters.
SHARE_ACCOUNT_CONTEXT = b"meshloom/links-acct/v1"
SHARE_ACCOUNT_BYTES = 16

# remote-registration.md allows exp - iat <= 3600 s and recommends 300 s.
REMOTE_TOKEN_LIFETIME = 300

_locks: dict[str, asyncio.Lock] = {}


def _lock(service: str) -> asyncio.Lock:
    return _locks.setdefault(service, asyncio.Lock())


def _signing_key(seed_hex: str) -> SigningKey | None:
    try:
        seed = bytes.fromhex(seed_hex)
    except ValueError:
        return None
    return SigningKey(seed) if len(seed) == SEED_BYTES else None


async def _load(service: str) -> SigningKey | None:
    return _signing_key(await AppSettingsRepository.get_instance_seed(service))


async def ensure_instance_key(service: str) -> SigningKey:
    """The service's signing key, created on first use and reused afterwards."""
    async with _lock(service):
        key = await _load(service)
        if key is not None:
            return key
        candidate = secrets.token_bytes(SEED_BYTES).hex()
        stored = await AppSettingsRepository.claim_instance_seed(service, candidate)
        key = _signing_key(stored)
        if key is None:
            # An unreadable stored value: replace it rather than fail forever.
            candidate = stored = secrets.token_bytes(SEED_BYTES).hex()
            await AppSettingsRepository.set_instance_seed(service, stored)
            key = _signing_key(stored)
            assert key is not None
        # Another writer (a second process on the database) may have won the claim.
        if stored == candidate:
            logger.info("Created the %s instance identity", service)
        return key


async def identity_ready(service: str) -> bool:
    """True when the service's key exists (never creates it)."""
    return await _load(service) is not None


async def instance_public_key(service: str) -> bytes | None:
    """The 32-byte public key, or None before the key is created."""
    key = await _load(service)
    return bytes(key.verify_key) if key is not None else None


def fingerprint_of(public_key: bytes) -> str:
    """First 8 bytes of SHA-256 of the public key, uppercase hex in groups of 4."""
    digest = hashlib.sha256(public_key).hexdigest()[:16].upper()
    return " ".join(digest[i : i + 4] for i in range(0, len(digest), 4))


async def fingerprint(service: str) -> str | None:
    """A short form of the public key for display, None before it is created."""
    public_key = await instance_public_key(service)
    return fingerprint_of(public_key) if public_key is not None else None


async def regenerate(service: str) -> None:
    """Replace the service's key with a new random one."""
    async with _lock(service):
        await AppSettingsRepository.set_instance_seed(
            service, secrets.token_bytes(SEED_BYTES).hex()
        )
    logger.info("Regenerated the %s instance identity", service)


async def _regenerate_after_restore(source: RestoreSource) -> None:
    """A restored instance gets new identities. A service that had none keeps
    none: it is created on its first use, as on a fresh install."""
    for service in INSTANCE_SEED_SERVICES:
        if await AppSettingsRepository.get_instance_seed(service):
            await regenerate(service)


register_on_restore("instance_identity", _regenerate_after_restore)


def _client() -> str:
    return f"meshloom/{get_app_build_info().version}"


async def mint_instance_jwt(
    service: str,
    audience: str,
    *,
    lifetime: int,
    jti: bool = True,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """A token signed by the service's instance key, in the Community wire format.

    ``publicKey`` is the instance key, ``client`` is ``meshloom/<version>``, no
    ``owner``; ``jti`` adds 16 random bytes in base64url. Mint one per request.
    """
    key = await ensure_instance_key(service)
    token_id = (
        base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode() if jti else None
    )
    return _generate_jwt_token(
        None,
        bytes(key.verify_key),
        audience=audience,
        sign=lambda message: key.sign(message).signature,
        lifetime=lifetime,
        extra_claims=extra_claims,
        jti=token_id,
        owner=False,
        client=_client(),
    )


def binding_hash(binding: str) -> str:
    """``bh``: lowercase hex SHA-256 of the binding string's UTF-8 bytes."""
    return hashlib.sha256(binding.encode("utf-8")).hexdigest()


def share_account_id_for(key: SigningKey, user_id: int) -> str:
    """``acct`` of ``user_id`` under the share key ``key`` (see ``share_account_id``)."""
    subkey = hmac.new(key.encode(), SHARE_ACCOUNT_CONTEXT, hashlib.sha256).digest()
    digest = hmac.new(subkey, str(int(user_id)).encode("ascii"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:SHARE_ACCOUNT_BYTES]).rstrip(b"=").decode()


async def share_account_id(user_id: int) -> str:
    """The pseudonymous ``acct`` of a local account for the share service.

    HMAC-SHA256 of the local user id under a key derived from the share instance
    seed, cut to 16 bytes, base64url (22 characters). Stable while the Links
    instance key is: the service sees the pair (instance key, ``acct``), and only
    this instance can tell which account it names. Without the seed it cannot be
    reversed or linked to an account, a username or another instance. It holds no
    username, e-mail or name. A new Links key (regenerate, restore) gives every
    account a new ``acct``, as it gives the instance a new key.
    """
    return share_account_id_for(await ensure_instance_key(SHARE_SERVICE), user_id)


async def mint_share_jwt(binding: str, *, account_id: int | None = None) -> str:
    """A share-service token bound to one request (``bh`` of ``binding``).

    The binding strings are in the links-files contract (the ``url`` of a link,
    ``id + "\\n" + reason`` for a report). ``account_id`` (the local user id) adds
    the ``acct`` claim. Never cache the result.
    """
    claims: dict[str, Any] = {"bh": binding_hash(binding)}
    if account_id is not None:
        claims["acct"] = await share_account_id(account_id)
    return await mint_instance_jwt(
        SHARE_SERVICE,
        SHARE_AUDIENCE,
        lifetime=SHARE_TOKEN_LIFETIME,
        extra_claims=claims,
    )


async def mint_remote_jwt() -> str:
    """A Remote instance JWT (``aud`` ``relay.meshloom.link``, 300 s, ``jti``), for one
    registry request or one relay connect attempt. Never cache the result."""
    return await mint_instance_jwt(REMOTE_SERVICE, REMOTE_AUDIENCE, lifetime=REMOTE_TOKEN_LIFETIME)


async def sign_with_instance_key(service: str, message: bytes) -> bytes:
    """The 64-byte Ed25519 signature of ``message`` by the service's key (created on
    first use). Only the Remote device passes use it (``app/services/remote_passes.py``)."""
    key = await ensure_instance_key(service)
    return key.sign(message).signature
