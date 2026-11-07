"""A stand-in for Community's ``POST /v1/me/attestations`` (MSHL-445).

``fake_attestation_token`` builds a token of the contract's wire shape
(``header_b64.payload_b64.signature_hex``) for a key and an audience; its signature is
random (Meshloom never verifies it, the services do). ``fake_request_attestation`` is
a stand-in for ``community_attestation._request_attestation``. ``tests/conftest.py`` puts
``fake_attestation_for`` and ``fake_renew_after_rejection`` in place of the module's
public functions, so no test reaches Community (or the database) for an attestation.
"""

from __future__ import annotations

import base64
import json
import secrets
import time

import httpx

LIFETIME_S = 259200  # attestation.md: Community issues exp = iat + 3 days


def _b64(value: dict) -> str:
    raw = json.dumps(value, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def fake_attestation_token(
    instance_key: str, audience: str, *, iat: int | None = None, exp: int | None = None
) -> str:
    iat = int(time.time()) if iat is None else iat
    exp = iat + LIFETIME_S if exp is None else exp
    header = _b64({"alg": "Ed25519", "typ": "meshloom-attestation"})
    payload = _b64(
        {
            "aid": secrets.token_urlsafe(16)[:22],
            "sub": instance_key,
            "aud": audience,
            "iat": iat,
            "exp": exp,
        }
    )
    return f"{header}.{payload}.{secrets.token_hex(64)}"


def attestation_answer(
    instance_key: str, audience: str, *, iat: int | None = None, exp: int | None = None
) -> httpx.Response:
    iat = int(time.time()) if iat is None else iat
    exp = iat + LIFETIME_S if exp is None else exp
    token = fake_attestation_token(instance_key, audience, iat=iat, exp=exp)
    return httpx.Response(
        201,
        json={"attestation": token, "audience": audience, "issued_at": iat, "expires_at": exp},
    )


async def fake_request_attestation(instance_key: str, audience: str) -> httpx.Response:
    return attestation_answer(instance_key, audience)


# What every Links creation and Remote registration carries in tests (conftest).
FAKE_ATTESTATION = "fake-attestation"


async def fake_attestation_for(audience: str) -> str:
    return FAKE_ATTESTATION


async def fake_renew_after_rejection(audience: str, rejected: str | None) -> str:
    return FAKE_ATTESTATION
