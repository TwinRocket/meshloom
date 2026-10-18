"""Instance identities: one Ed25519 key per service, owned by this Meshloom.

Each service (``share`` for links and files; Access adds ``remote``) has its
own random 32-byte seed in ``app_settings.<service>_instance_seed``, created by
``secrets.token_bytes`` on first use and never derived from the radio key. The
public key is a pseudonym unrelated to the radio's identity in the Community
directory, and two services' keys cannot be linked to each other.

The seed never leaves the server: not in ``AppSettings``, not in an API
response, not in a log line, not in a backup (``docs/dev/backup.md``, rule 4).
Every restore regenerates it through the ``on_restore`` hook registered below.

Tokens use the Community wire format (``_generate_jwt_token`` in
``app/fanout/community_mqtt.py``, signed here with PyNaCl instead of the
expanded radio key). Never log a token either.
"""

import asyncio
import base64
import hashlib
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
        stored = await AppSettingsRepository.claim_instance_seed(
            service, secrets.token_bytes(SEED_BYTES).hex()
        )
        key = _signing_key(stored)
        if key is None:
            # An unreadable stored value: replace it rather than fail forever.
            fresh = secrets.token_bytes(SEED_BYTES).hex()
            await AppSettingsRepository.set_instance_seed(service, fresh)
            key = _signing_key(fresh)
            assert key is not None
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
    claims: dict[str, Any] = {}
    if jti:
        claims["jti"] = base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode()
    claims.update(extra_claims or {})
    return _generate_jwt_token(
        None,
        bytes(key.verify_key),
        audience=audience,
        sign=lambda message: key.sign(message).signature,
        lifetime=lifetime,
        extra_claims=claims,
        owner=False,
        client=_client(),
    )


def binding_hash(binding: str) -> str:
    """``bh``: lowercase hex SHA-256 of the binding string's UTF-8 bytes."""
    return hashlib.sha256(binding.encode("utf-8")).hexdigest()


async def mint_share_jwt(binding: str) -> str:
    """A share-service token bound to one request (``bh`` of ``binding``).

    The binding strings are in the links-files contract (the ``url`` of a link,
    ``id + "\\n" + reason`` for a report). Never cache the result.
    """
    return await mint_instance_jwt(
        SHARE_SERVICE,
        SHARE_AUDIENCE,
        lifetime=SHARE_TOKEN_LIFETIME,
        extra_claims={"bh": binding_hash(binding)},
    )
