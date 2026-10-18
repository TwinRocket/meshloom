"""Opaque secrets (session cookies, setup codes, later API and device tokens).

A secret is 256 random bits from ``secrets``, shown to its holder once; the
database keeps only its SHA-256 (hex). Lookups go by that hash, and a stored
hash is compared with ``hmac.compare_digest``. Never log a secret or its hash.
"""

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32


def new_token() -> str:
    """A fresh URL-safe secret (43 characters, 256 bits)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The SHA-256 of ``token`` as lowercase hex: what the database stores."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, stored_hash: str | None) -> bool:
    """Constant-time check of ``token`` against a stored hash."""
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_token(token), stored_hash)
