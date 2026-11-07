"""Community tokens are byte-identical after the instance identity refactor (MSHL-230).

The expected tokens were minted by ``_generate_jwt_token`` before it gained the
``sign``/``lifetime``/``extra_claims``/``owner``/``client`` keywords, for a fixed
expanded key, clock and client string. Community MQTT, the Stats uplink and the
Community API all mint through it: never refresh these strings to make a test
pass, a difference is a wire format change for 4.18.x-compatible traffic.
"""

import hashlib
from unittest.mock import patch

import nacl.bindings
import pytest
from nacl.signing import SigningKey, VerifyKey

from app.fanout import community_mqtt
from app.fanout.community_mqtt import _generate_jwt_token

_SCALAR = bytearray(hashlib.sha256(b"meshloom community jwt golden scalar").digest())
_SCALAR[0] &= 248
_SCALAR[31] &= 63
_SCALAR[31] |= 64
PRIVATE_KEY = bytes(_SCALAR) + hashlib.sha256(b"meshloom community jwt golden prefix").digest()
PUBLIC_KEY = nacl.bindings.crypto_scalarmult_ed25519_base_noclamp(bytes(_SCALAR))

NOW = 1760100000.7
CLIENT = "Meshloom/4.18.2-0123abc"

GOLDEN_DEFAULT = (
    "eyJhbGciOiJFZDI1NTE5IiwidHlwIjoiSldUIn0."
    "eyJwdWJsaWNLZXkiOiI0MENFQ0IxOEM1M0FBMTk4NkFDQ0JGRENERkVBMUQzMEI4RTNDMUNERUI4MjY0N0Y1"
    "MTY3QTg2MDdCMjNCNjFDIiwiaWF0IjoxNzYwMTAwMDAwLCJleHAiOjE3NjAxMDMzMDAsImF1ZCI6Im1xdHQt"
    "dXMtdjEubGV0c21lc2gubmV0Iiwib3duZXIiOiI0MENFQ0IxOEM1M0FBMTk4NkFDQ0JGRENERkVBMUQzMEI4"
    "RTNDMUNERUI4MjY0N0Y1MTY3QTg2MDdCMjNCNjFDIiwiY2xpZW50IjoiTWVzaGxvb20vNC4xOC4yLTAxMjNh"
    "YmMifQ."
    "b2b950d9f91eae8c1e1d6b69ef8d47b1d3800e7d3e52b24427f7ee224b8373ff"
    "9514123a44f39612643c6f74c5c7bde0a3bcea9af63a8787ea008a5dc89f6b00"
)
GOLDEN_STATS = (
    "eyJhbGciOiJFZDI1NTE5IiwidHlwIjoiSldUIn0."
    "eyJwdWJsaWNLZXkiOiI0MENFQ0IxOEM1M0FBMTk4NkFDQ0JGRENERkVBMUQzMEI4RTNDMUNERUI4MjY0N0Y1"
    "MTY3QTg2MDdCMjNCNjFDIiwiaWF0IjoxNzYwMTAwMDAwLCJleHAiOjE3NjAxMDMzMDAsImF1ZCI6Im1xdHQu"
    "bWVzaGxvb20uYXBwIiwib3duZXIiOiI0MENFQ0IxOEM1M0FBMTk4NkFDQ0JGRENERkVBMUQzMEI4RTNDMUNE"
    "RUI4MjY0N0Y1MTY3QTg2MDdCMjNCNjFDIiwiY2xpZW50IjoiTWVzaGxvb20vNC4xOC4yLTAxMjNhYmMiLCJl"
    "bWFpbCI6Im93bmVyQGV4YW1wbGUuY29tIiwiaWF0YSI6IkNERyJ9."
    "3f635fb01b1301cad7c76c9595787e97b886d5228206a1fb2c7b8f5744cdf372"
    "99fef2868884f8feb6edf59fda3ac66cc61d0500565df768024596366be44e05"
)


def _mint(**kwargs) -> str:
    with (
        patch.object(community_mqtt.time, "time", return_value=NOW),
        patch.object(community_mqtt, "_get_client_version", return_value=CLIENT),
    ):
        return _generate_jwt_token(PRIVATE_KEY, PUBLIC_KEY, **kwargs)


def test_default_community_token_is_unchanged():
    assert _mint() == GOLDEN_DEFAULT


def test_token_with_email_and_iata_is_unchanged():
    token = _mint(audience="mqtt.meshloom.app", email="owner@example.com", iata="cdg ")
    assert token == GOLDEN_STATS


def test_a_signer_replaces_the_expanded_key():
    """The PyNaCl path gives the same 64-byte signature as the expanded-key path."""
    seed = hashlib.sha256(b"signer seed").digest()
    key = SigningKey(seed)
    public_key = bytes(key.verify_key)
    expanded = hashlib.sha512(seed).digest()
    scalar = bytearray(expanded[:32])
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    with patch.object(community_mqtt.time, "time", return_value=NOW):
        signed = _generate_jwt_token(
            None, public_key, client="x", sign=lambda m: key.sign(m).signature
        )
        expanded_signed = _generate_jwt_token(bytes(scalar) + expanded[32:], public_key, client="x")
    assert signed == expanded_signed
    header_b64, payload_b64, signature_hex = signed.split(".")
    VerifyKey(public_key).verify(
        f"{header_b64}.{payload_b64}".encode(), bytes.fromhex(signature_hex)
    )


@pytest.mark.parametrize(
    "claim", ["iss", "sub", "aud", "exp", "iat", "nbf", "jti", "publicKey", "owner", "client"]
)
def test_extra_claims_never_override_a_standard_or_set_claim(claim):
    with pytest.raises(ValueError, match=claim):
        _mint(extra_claims={claim: "x"})
