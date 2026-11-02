"""Meshloom Remote device passes and access codes (contract: meshloom-community
``docs/contracts/remote-relay.md``, "Edge lock"; vectors ``remote-frames.json``).

A pass is ``b64url(header).b64url(payload).signature``: the header is exactly
``{"alg":"Ed25519","typ":"mlpass"}``, the signature is Ed25519 over
``header_b64 + "." + payload_b64`` by the instance's Remote key, as 128 lowercase
hex characters, and the whole token is at most 2048 characters. Claims: ``v`` (1),
``kind`` (``enroll``, ``browser``, ``client``), ``iss`` (the slug), ``sub`` (device
id, 22 base64url characters), ``uid`` (the Meshloom user, opaque to the relay),
``gen``, ``iat``, ``exp`` and, for ``enroll``, a single-use ``jti``.

``verify_pass`` is the contract's verifier, step by step (the first failing step
gives the verdict); the instance runs it as the third check of the edge lock, on
``x-meshloom-pass``, and on the enrolment tokens the relay forwards. Pure functions:
no database, no clock, no network. Never log a pass.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any, Literal

from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

PassKind = Literal["enroll", "browser", "client"]
PassSlot = Literal["cookie", "header", "enroll"]

PASS_HEADER = {"alg": "Ed25519", "typ": "mlpass"}
MAX_PASS_BYTES = 2048
CLOCK_SKEW_S = 60
MAX_LIFETIME_S: dict[str, int] = {
    "enroll": 604800,  # 7 days (a guest link); a LAN QR code lives 15 min
    "browser": 7776000,  # 90 days, renewed while used
    "client": 31536000,  # 365 days
}
SLOT_KINDS: dict[str, str] = {"cookie": "browser", "header": "client", "enroll": "enroll"}
GATE_COOKIE = "__Host-mlgate"
PASS_HEADER_NAME = "X-Meshloom-Pass"

ID_RE = re.compile(r"[A-Za-z0-9_-]{22}")
UID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{128}")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]*")

# Access codes: 10 Crockford base32 symbols, shown as XXXXX-XXXXX.
ACCESS_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ACCESS_CODE_SYMBOLS = 10


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(part: str) -> bytes:
    if not _B64URL_RE.fullmatch(part):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


def new_id() -> str:
    """128 random bits as 22 base64url characters (device ids, ``jti``)."""
    return b64url(secrets.token_bytes(16))


HEADER_B64 = b64url(json.dumps(PASS_HEADER, separators=(",", ":")).encode())


def pass_claims(
    kind: PassKind,
    *,
    iss: str,
    sub: str,
    uid: str,
    gen: int,
    iat: int,
    lifetime: int,
    jti: str | None = None,
) -> dict[str, Any]:
    """The claims of a pass, in the contract's order. Refuses what the relay would."""
    if lifetime <= 0 or lifetime > MAX_LIFETIME_S[kind]:
        raise ValueError(f"lifetime out of range for a {kind} pass")
    if not ID_RE.fullmatch(sub) or not UID_RE.fullmatch(uid) or gen < 0:
        raise ValueError("malformed pass claims")
    claims: dict[str, Any] = {
        "v": 1,
        "kind": kind,
        "iss": iss,
        "sub": sub,
        "uid": uid,
        "gen": gen,
        "iat": iat,
        "exp": iat + lifetime,
    }
    if kind == "enroll":
        if jti is None or not ID_RE.fullmatch(jti):
            raise ValueError("an enrolment token needs a jti")
        claims["jti"] = jti
    elif jti is not None:
        claims["jti"] = jti
    return claims


def signing_input(claims: dict[str, Any]) -> tuple[str, bytes]:
    """``(payload_b64, header_b64 + "." + payload_b64)`` for ``claims``."""
    payload_b64 = b64url(json.dumps(claims, separators=(",", ":")).encode())
    return payload_b64, f"{HEADER_B64}.{payload_b64}".encode("ascii")


def assemble_pass(payload_b64: str, signature: bytes) -> str:
    token = f"{HEADER_B64}.{payload_b64}.{signature.hex()}"
    if len(token) > MAX_PASS_BYTES:
        raise ValueError("pass too long")
    return token


@dataclass(frozen=True)
class PassState:
    """What a pass is checked against: the slug, the registered key, the generation,
    the revoked device ids and the consumed enrolment ``jti``."""

    slug: str
    public_key: bytes
    gen: int
    revoked: Collection[str] = field(default_factory=frozenset)
    seen_jti: Collection[str] = field(default_factory=frozenset)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _claims_ok(claims: Any) -> bool:
    if not isinstance(claims, dict):
        return False
    if not _is_int(claims.get("v")) or claims["v"] != 1:
        return False
    kind = claims.get("kind")
    if not isinstance(kind, str) or kind not in MAX_LIFETIME_S:
        return False
    if not isinstance(claims.get("iss"), str):
        return False
    sub, uid = claims.get("sub"), claims.get("uid")
    if not isinstance(sub, str) or not ID_RE.fullmatch(sub):
        return False
    if not isinstance(uid, str) or not UID_RE.fullmatch(uid):
        return False
    if not _is_int(claims.get("gen")) or claims["gen"] < 0:
        return False
    iat, exp = claims.get("iat"), claims.get("exp")
    if not isinstance(iat, int) or not isinstance(exp, int) or not (_is_int(iat) and _is_int(exp)):
        return False
    if exp <= iat:
        return False
    if kind == "enroll":
        jti = claims.get("jti")
        if not isinstance(jti, str) or not ID_RE.fullmatch(jti):
            return False
    return True


def decode_pass(token: str) -> dict[str, Any] | None:
    """The claims of a well-formed pass, signature NOT checked (None otherwise)."""
    if not isinstance(token, str) or not token or len(token) > MAX_PASS_BYTES:
        return None
    parts = token.split(".")
    if len(parts) != 3 or not _SIGNATURE_RE.fullmatch(parts[2]):
        return None
    try:
        header = json.loads(_b64url_decode(parts[0]))
        claims = json.loads(_b64url_decode(parts[1]))
    except (ValueError, UnicodeDecodeError):
        return None
    if header != PASS_HEADER or not _claims_ok(claims):
        return None
    return claims


def verify_pass(
    token: str, slot: PassSlot, state: PassState, now: int
) -> tuple[str, dict[str, Any] | None]:
    """The contract's verdict for ``token`` in ``slot``: ``("accept", claims)``, or the
    first failing step (``pass_malformed``, ``pass_kind``, ``pass_issuer``,
    ``pass_signature``, ``clock_skew``, ``pass_expired``, ``pass_lifetime``,
    ``pass_generation``, ``pass_revoked``, ``pass_replayed``) and None."""
    claims = decode_pass(token)
    if claims is None:
        return "pass_malformed", None
    if claims["kind"] != SLOT_KINDS[slot]:
        return "pass_kind", None
    if claims["iss"] != state.slug:
        return "pass_issuer", None
    header_b64, payload_b64, signature_hex = token.split(".")
    try:
        VerifyKey(state.public_key).verify(
            f"{header_b64}.{payload_b64}".encode("ascii"), bytes.fromhex(signature_hex)
        )
    except (BadSignatureError, ValueError):
        return "pass_signature", None
    if claims["iat"] > now + CLOCK_SKEW_S:
        return "clock_skew", None
    if claims["exp"] < now - CLOCK_SKEW_S:
        return "pass_expired", None
    if claims["exp"] - claims["iat"] > MAX_LIFETIME_S[claims["kind"]]:
        return "pass_lifetime", None
    if claims["gen"] != state.gen:
        return "pass_generation", None
    if claims["sub"] in state.revoked:
        return "pass_revoked", None
    if claims["kind"] == "enroll" and claims["jti"] in state.seen_jti:
        return "pass_replayed", None
    return "accept", claims


def normalize_access_code(raw: str) -> str | None:
    """The 10 symbols of an access code typed by a user, or None.

    Upper case; ``-`` and spaces removed; ``O`` reads as ``0``, ``I`` and ``L`` as ``1``.
    """
    if not isinstance(raw, str):
        return None
    text = raw.upper().replace("-", "").replace(" ", "")
    text = text.replace("O", "0").replace("I", "1").replace("L", "1")
    if len(text) != ACCESS_CODE_SYMBOLS or any(ch not in ACCESS_CODE_ALPHABET for ch in text):
        return None
    return text
