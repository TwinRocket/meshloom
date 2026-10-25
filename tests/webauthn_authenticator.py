"""A software WebAuthn authenticator for the passkey tests (``tests/test_auth_webauthn.py``).

An ES256 key from ``cryptography``, ``none`` attestation, the browser's JSON
shapes (what ``@simplewebauthn/browser`` sends). Every knob a test needs to
break a ceremony is a keyword: another RP id or origin, no user verification,
a counter that does not grow.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

AAGUID = bytes.fromhex("0123456789abcdef0123456789abcdef")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class SoftAuthenticator:
    def __init__(self, *, counter_step: int = 1, backup_eligible: bool = False) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.counter = 0
        self.counter_step = counter_step
        self.backup_eligible = backup_eligible
        self.user_handle: bytes | None = None

    def _cose_key(self) -> bytes:
        numbers = self.key.public_key().public_numbers()
        return cbor2.dumps(
            {
                1: 2,
                3: -7,
                -1: 1,
                -2: numbers.x.to_bytes(32, "big"),
                -3: numbers.y.to_bytes(32, "big"),
            }
        )

    def _auth_data(self, rp_id: str, *, uv: bool, attested: bool) -> bytes:
        flags = 0x01 | (0x04 if uv else 0)
        if self.backup_eligible:
            flags |= 0x08 | 0x10
        if attested:
            flags |= 0x40
        data = hashlib.sha256(rp_id.encode()).digest()
        data += bytes([flags]) + self.counter.to_bytes(4, "big")
        if attested:
            data += AAGUID + len(self.credential_id).to_bytes(2, "big") + self.credential_id
            data += self._cose_key()
        return data

    @staticmethod
    def _client_data(kind: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}
        ).encode()

    def create(
        self, options: dict, origin: str, *, rp_id: str | None = None, uv: bool = True
    ) -> dict:
        """``navigator.credentials.create()`` for these options."""
        self.user_handle = unb64url(options["user"]["id"])
        client_data = self._client_data("webauthn.create", options["challenge"], origin)
        auth_data = self._auth_data(rp_id or options["rp"]["id"], uv=uv, attested=True)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "attestationObject": b64url(attestation),
                "transports": ["internal", "hybrid"],
            },
            "clientExtensionResults": {},
        }

    def get(
        self,
        options: dict,
        origin: str,
        *,
        rp_id: str | None = None,
        uv: bool = True,
        user_handle: bytes | None = None,
    ) -> dict:
        """``navigator.credentials.get()`` for these options."""
        self.counter += self.counter_step
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        auth_data = self._auth_data(rp_id or options["rpId"], uv=uv, attested=False)
        signature = self.key.sign(
            auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        handle = user_handle if user_handle is not None else self.user_handle
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(auth_data),
                "signature": b64url(signature),
                "userHandle": b64url(handle) if handle is not None else None,
            },
            "clientExtensionResults": {},
        }
