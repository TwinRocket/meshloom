"""Remote identity: instance JWT and device passes against the golden vectors (MSHL-79).

Provenance: tests/fixtures/remote_frames_vectors.json is a byte-for-byte copy of
TwinRocket/meshloom-community docs/contracts/vectors/remote-frames.json on main at
57edbd89 (PR #149, MSHL-76; vectors version 2), rules in docs/contracts/
remote-registration.md ("Instance JWT") and remote-relay.md ("Edge lock"). sha256
below. To update, copy the file again and change the hash; never edit the copy to
make a test pass.

``_relay_verify`` re-implements the registry's JWT checks in the contract's order; it
is proved on every vector case first, then run on the tokens Meshloom mints. The pass
verifier under test is ``app.services.remote_passes.verify_pass`` itself.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

import pytest
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from app.repository import AppSettingsRepository
from app.services import remote_passes
from app.services.instance_identity import (
    REMOTE_AUDIENCE,
    REMOTE_SERVICE,
    SHARE_SERVICE,
    ensure_instance_key,
    instance_public_key,
    mint_remote_jwt,
    sign_with_instance_key,
)
from app.services.remote_passes import PassState, normalize_access_code, verify_pass

VECTORS_PATH = Path(__file__).parent / "fixtures" / "remote_frames_vectors.json"
VECTORS_SHA256 = "af856fdd859165e414595da81b92e176fa5dbf5500ee6b147b7d1c2d265af9a7"
VECTORS: dict[str, Any] = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
JWT = VECTORS["jwt"]
PASSES = VECTORS["passes"]
_STATE = PASSES["state"]
VECTOR_STATE = PassState(
    slug=_STATE["slug"],
    public_key=bytes.fromhex(_STATE["public_key"]),
    gen=_STATE["gen"],
    revoked=frozenset(_STATE["revoked"]),
    seen_jti=frozenset(_STATE["seen_jti"]),
)
_JTI = re.compile(JWT["jti_pattern"].strip("^$"))
_SIG = re.compile(r"[0-9a-fA-F]{128}")


def test_vectors_are_an_unedited_copy() -> None:
    digest = hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest()
    assert digest == VECTORS_SHA256, "copy changed: re-copy from meshloom-community"
    assert VECTORS["version"] == 2


def test_constants_match_the_vectors() -> None:
    assert PASSES["header"] == remote_passes.PASS_HEADER
    assert PASSES["max_bytes"] == remote_passes.MAX_PASS_BYTES
    assert PASSES["max_lifetime_s"] == remote_passes.MAX_LIFETIME_S
    assert PASSES["slot_kinds"] == remote_passes.SLOT_KINDS
    assert PASSES["clock_skew_s"] == remote_passes.CLOCK_SKEW_S
    assert JWT["audience"] == REMOTE_AUDIENCE
    limits = VECTORS["limits"]
    from app.services import remote

    assert limits["revoked_max"] == remote.REVOKED_MAX
    assert limits["access_request_ttl_s"] == remote.ACCESS_REQUEST_TTL_S
    assert limits["access_requests_pending_max"] == remote.ACCESS_REQUESTS_PENDING_MAX
    assert limits["enroll_answer_timeout_s"] > remote.ENROLL_ANSWER_TIMEOUT_S
    assert VECTORS["access_codes"]["alphabet"] == remote_passes.ACCESS_CODE_ALPHABET


# ---------------------------------------------------------------- passes


@pytest.mark.parametrize("case", PASSES["cases"], ids=[c["id"] for c in PASSES["cases"]])
def test_pass_verifier_matches_every_vector(case: dict[str, Any]) -> None:
    verdict, claims = verify_pass(case["token"], case["slot"], VECTOR_STATE, PASSES["now"])
    assert verdict == case["expect"]
    if verdict == "accept":
        assert claims == case["claims"]


@pytest.mark.parametrize(
    "case",
    [c for c in PASSES["cases"] if c["expect"] == "accept"],
    ids=lambda c: c["id"],
)
def test_signing_reproduces_the_vector_tokens(case: dict[str, Any]) -> None:
    """Same claims, same key: the same bytes (Ed25519 is deterministic)."""
    key = SigningKey(bytes.fromhex(JWT["key"]["seed_hex"]))
    payload_b64, message = remote_passes.signing_input(case["claims"])
    assert json.dumps(case["claims"], separators=(",", ":")) == case["payload_json"]
    token = remote_passes.assemble_pass(payload_b64, key.sign(message).signature)
    assert token == case["token"]


def test_pass_claims_follow_the_contract_order() -> None:
    claims = remote_passes.pass_claims(
        "enroll",
        iss="k3xwq2m7ta",
        sub="A" * 22,
        uid="1",
        gen=0,
        iat=100,
        lifetime=900,
        jti="B" * 22,
    )
    assert list(claims) == ["v", "kind", "iss", "sub", "uid", "gen", "iat", "exp", "jti"]
    with pytest.raises(ValueError):
        remote_passes.pass_claims(
            "browser",
            iss="s",
            sub="A" * 22,
            uid="1",
            gen=0,
            iat=0,
            lifetime=remote_passes.MAX_LIFETIME_S["browser"] + 1,
        )
    with pytest.raises(ValueError):
        remote_passes.pass_claims(
            "enroll", iss="s", sub="A" * 22, uid="1", gen=0, iat=0, lifetime=60
        )


@pytest.mark.parametrize("case", VECTORS["access_codes"]["cases"], ids=lambda c: c["input"])
def test_access_codes_normalise_like_the_vectors(case: dict[str, Any]) -> None:
    assert normalize_access_code(case["input"]) == case["normalized"]


# ---------------------------------------------------------------- instance JWT


class Rejected(Exception):
    pass


def _b64(part: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", part):
        raise Rejected("token_malformed")
    try:
        value = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except ValueError as exc:
        raise Rejected("token_malformed") from exc
    if not isinstance(value, dict):
        raise Rejected("token_malformed")
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _relay_verify(token: str, now: int, seen: set[str]) -> str:
    """remote-registration.md, "Verification order" (service verdict)."""
    try:
        parts = token.split(".")
        if len(token) > 8192 or len(parts) != 3 or not _SIG.fullmatch(parts[2]):
            raise Rejected("token_malformed")
        header, claims = _b64(parts[0]), _b64(parts[1])
        if header != {"alg": "Ed25519", "typ": "JWT"}:
            raise Rejected("token_malformed")
        public_key = claims.get("publicKey")
        if not isinstance(public_key, str) or not re.fullmatch(r"[0-9A-F]{64}", public_key):
            raise Rejected("token_malformed")
        iat, exp = claims.get("iat"), claims.get("exp")
        if not (_is_int(iat) and _is_int(exp)) or not 0 < exp - iat <= JWT["max_lifetime_s"]:
            raise Rejected("token_malformed")
        if not isinstance(claims.get("client"), str) or not claims["client"]:
            raise Rejected("token_malformed")
        try:
            VerifyKey(bytes.fromhex(public_key)).verify(
                f"{parts[0]}.{parts[1]}".encode(), bytes.fromhex(parts[2])
            )
        except BadSignatureError as exc:
            raise Rejected("token_signature") from exc
        if claims.get("aud") != JWT["audience"]:
            raise Rejected("token_audience")
        if exp < now - JWT["clock_skew_s"]:
            raise Rejected("token_expired")
        if iat > now + JWT["clock_skew_s"]:
            raise Rejected("clock_skew")
        jti = claims.get("jti")
        if not isinstance(jti, str) or not _JTI.fullmatch(jti):
            raise Rejected("token_malformed")
        if jti in seen:
            raise Rejected("token_replayed")
        seen.add(jti)
    except Rejected as exc:
        return str(exc)
    return "accept"


@pytest.mark.parametrize("case", JWT["cases"], ids=[c["id"] for c in JWT["cases"]])
def test_local_relay_checks_match_every_vector(case: dict[str, Any]) -> None:
    assert (
        _relay_verify(case["token"], JWT["now"], set(case["seen_jti"]))
        == (case["expect"]["service"])
    )


@pytest.mark.asyncio
async def test_the_vector_seed_gives_the_vector_public_key(test_db):
    await AppSettingsRepository.set_instance_seed(REMOTE_SERVICE, JWT["key"]["seed_hex"])
    public_key = await instance_public_key(REMOTE_SERVICE)
    assert public_key is not None and public_key.hex().upper() == JWT["key"]["public_key"]


@pytest.mark.asyncio
async def test_remote_jwt_passes_the_relay_checks_and_is_new_every_time(test_db):
    from app.version_info import get_app_build_info

    seen: set[str] = set()
    tokens = [await mint_remote_jwt() for _ in range(5)]
    for token in tokens:
        assert _relay_verify(token, int(time.time()), seen) == "accept"
    assert _relay_verify(tokens[0], int(time.time()), seen) == "token_replayed"
    claims = _b64(tokens[0].split(".")[1])
    public_key = await instance_public_key(REMOTE_SERVICE)
    assert public_key is not None and claims["publicKey"] == public_key.hex().upper()
    assert claims["aud"] == "relay.meshloom.link"
    assert claims["exp"] - claims["iat"] == 300
    assert claims["client"] == f"meshloom/{get_app_build_info().version}"
    assert len({_b64(t.split(".")[1])["jti"] for t in tokens}) == 5
    for absent in ("owner", "email", "iata"):
        assert absent not in claims


@pytest.mark.asyncio
async def test_remote_and_share_keys_are_distinct(test_db):
    share_key = await ensure_instance_key(SHARE_SERVICE)
    remote_key = await ensure_instance_key(REMOTE_SERVICE)
    assert bytes(share_key.verify_key) != bytes(remote_key.verify_key)
    assert await AppSettingsRepository.get_instance_seed(REMOTE_SERVICE) != (
        await AppSettingsRepository.get_instance_seed(SHARE_SERVICE)
    )


@pytest.mark.asyncio
async def test_signed_pass_verifies_with_the_remote_key(test_db):
    await AppSettingsRepository.set_instance_seed(REMOTE_SERVICE, JWT["key"]["seed_hex"])
    case = next(c for c in PASSES["cases"] if c["id"] == "client_valid")
    payload_b64, message = remote_passes.signing_input(case["claims"])
    signature = await sign_with_instance_key(REMOTE_SERVICE, message)
    assert remote_passes.assemble_pass(payload_b64, signature) == case["token"]
