"""Instance identity and share tokens (MSHL-230, app/services/instance_identity.py).

Provenance: tests/fixtures/links_files_vectors.json is a byte-for-byte copy of
TwinRocket/meshloom-community docs/contracts/vectors/links-files.json on main
(commit a9b34cfc, PR #138, vectors version 1), rules in docs/contracts/links-files.md,
"Instance JWT". sha256 below. To update, copy the file again and change the
hash; never edit the copy to make a test pass.

``_worker_verify`` re-implements the Worker's checks in the contract's order. It
is proved on every vector case first, then run on the tokens Meshloom mints.
"""

import asyncio
import base64
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import pytest
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from app.models import BackupRestoreRequest
from app.repository import AppSettingsRepository
from app.services import instance_identity
from app.services.backup import export_backup_v2, export_document, export_json, restore_json
from app.services.backup_sections import run_on_restore_hooks
from app.services.instance_identity import (
    SHARE_SERVICE,
    ensure_instance_key,
    fingerprint,
    identity_ready,
    instance_public_key,
    mint_instance_jwt,
    mint_share_jwt,
    regenerate,
)

VECTORS_PATH = Path(__file__).parent / "fixtures" / "links_files_vectors.json"
VECTORS_SHA256 = "a0296037cffe04f5f22de4f9e2bb0ff1b8715bec5c1bc93dab582a3227c40d2c"
VECTORS: dict[str, Any] = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
JWT = VECTORS["jwt"]

_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_SIGNATURE = re.compile(r"^[0-9a-fA-F]{128}$")
_PUBLIC_KEY = re.compile(r"^[0-9A-F]{64}$")
_JTI = re.compile(r"^[A-Za-z0-9_-]{22}$")
_BH = re.compile(r"^[0-9a-f]{64}$")
_IATA = re.compile(r"^[A-Z]{3}$")


class Rejected(Exception):
    pass


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise Rejected("token_malformed")
    return dict(pairs)


def _decode(part: str) -> dict[str, Any]:
    if not _B64URL.match(part):
        raise Rejected("token_malformed")
    try:
        raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
        value = json.loads(raw, object_pairs_hook=_no_duplicates)
    except (ValueError, UnicodeDecodeError) as exc:
        raise Rejected("token_malformed") from exc
    if not isinstance(value, dict):
        raise Rejected("token_malformed")
    return value


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _worker_verify(token: str, binding: str, now: int, seen: set[str]) -> str:
    """The links-files verification order; returns "accept" or the error code."""
    try:
        skew = JWT["clock_skew_s"]
        # 1. jwt.md rules, audience meshloom-share.
        parts = token.split(".")
        if len(token) > 8192 or len(parts) != 3:
            raise Rejected("token_malformed")
        header, claims = _decode(parts[0]), _decode(parts[1])
        if header != {"alg": "Ed25519", "typ": "JWT"} or not _SIGNATURE.match(parts[2]):
            raise Rejected("token_malformed")
        public_key = claims.get("publicKey")
        iat, exp, client = claims.get("iat"), claims.get("exp"), claims.get("client")
        if not isinstance(public_key, str) or not _PUBLIC_KEY.match(public_key):
            raise Rejected("token_malformed")
        if not (_is_int(iat) and _is_int(exp) and 0 < exp - iat <= 3600):
            raise Rejected("token_malformed")
        if not isinstance(client, str) or not client.strip():
            raise Rejected("token_malformed")
        if "iata" in claims and not (
            isinstance(claims["iata"], str) and _IATA.match(claims["iata"])
        ):
            raise Rejected("token_malformed")
        if claims.get("aud") != JWT["audience"]:
            raise Rejected("token_audience")
        if iat > now + skew:
            raise Rejected("clock_skew")
        if exp < now - skew:
            raise Rejected("token_expired")
        try:
            VerifyKey(bytes.fromhex(public_key)).verify(
                f"{parts[0]}.{parts[1]}".encode(), bytes.fromhex(parts[2])
            )
        except BadSignatureError as exc:
            raise Rejected("token_signature") from exc
        # 2. Service-only rules.
        if exp - iat > JWT["max_lifetime_s"]:
            raise Rejected("token_malformed")
        jti, bh = claims.get("jti"), claims.get("bh")
        if not (isinstance(jti, str) and _JTI.match(jti)):
            raise Rejected("token_malformed")
        if not (isinstance(bh, str) and _BH.match(bh)):
            raise Rejected("token_malformed")
        if bh != hashlib.sha256(binding.encode("utf-8")).hexdigest():
            raise Rejected("token_binding")
        # 3. Replay, last.
        if jti in seen:
            raise Rejected("token_replayed")
        seen.add(jti)
    except Rejected as rejected:
        return str(rejected)
    return "accept"


def _claims(token: str) -> dict[str, Any]:
    return _decode(token.split(".")[1])


def test_vectors_are_an_unedited_copy() -> None:
    digest = hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest()
    assert digest == VECTORS_SHA256, (
        "copy changed: re-copy from meshloom-community and update the hash"
    )
    assert VECTORS["version"] == 1


@pytest.mark.parametrize("case", JWT["cases"], ids=[c["id"] for c in JWT["cases"]])
def test_local_worker_checks_match_every_vector(case: dict[str, Any]) -> None:
    seen = set(case["seen_jti"])
    assert (
        _worker_verify(case["token"], case["binding"], JWT["now"], seen)
        == (case["expect"]["service"])
    )


@pytest.mark.asyncio
async def test_the_vector_seed_gives_the_vector_public_key(test_db):
    await AppSettingsRepository.set_instance_seed(SHARE_SERVICE, JWT["key"]["seed_hex"])
    public_key = await instance_public_key(SHARE_SERVICE)
    assert public_key is not None
    assert public_key.hex().upper() == JWT["key"]["public_key"]


@pytest.mark.asyncio
async def test_share_token_passes_the_worker_checks(test_db):
    binding = "https://exämple.fr/chemin/é?q=ü"
    token = await mint_share_jwt(binding)
    seen: set[str] = set()
    assert _worker_verify(token, binding, int(time.time()), seen) == "accept"
    # One use only, and only for the request it was minted for.
    assert _worker_verify(token, binding, int(time.time()), seen) == "token_replayed"
    assert _worker_verify(token, binding + "x", int(time.time()), set()) == "token_binding"


@pytest.mark.asyncio
async def test_share_token_claims(test_db):
    from app.version_info import get_app_build_info

    token = await mint_share_jwt("AbCdEfG\nphishing")
    claims = _claims(token)
    public_key = await instance_public_key(SHARE_SERVICE)
    assert public_key is not None
    assert claims["publicKey"] == public_key.hex().upper()
    assert claims["aud"] == "meshloom-share"
    assert claims["exp"] - claims["iat"] == 110
    assert claims["client"] == f"meshloom/{get_app_build_info().version}"
    assert claims["bh"] == hashlib.sha256(b"AbCdEfG\nphishing").hexdigest()
    assert _JTI.match(claims["jti"])
    assert len(base64.urlsafe_b64decode(claims["jti"] + "==")) == 16
    for absent in ("owner", "email", "iata"):
        assert absent not in claims


@pytest.mark.asyncio
async def test_every_token_is_new(test_db):
    tokens = [await mint_share_jwt("https://example.com/") for _ in range(5)]
    assert len(set(tokens)) == 5
    assert len({_claims(token)["jti"] for token in tokens}) == 5


@pytest.mark.asyncio
async def test_a_community_token_is_refused_and_the_radio_key_is_not_used(test_db):
    token = await mint_instance_jwt(SHARE_SERVICE, "api.meshloom.app", lifetime=60)
    assert _worker_verify(token, "", int(time.time()), set()) == "token_audience"
    without_jti = await mint_instance_jwt(
        SHARE_SERVICE, "meshloom-share", lifetime=60, jti=False, extra_claims={"bh": "0" * 64}
    )
    assert "jti" not in _claims(without_jti)


@pytest.mark.asyncio
async def test_the_seed_is_created_once_and_reused(test_db):
    assert await identity_ready(SHARE_SERVICE) is False
    assert await instance_public_key(SHARE_SERVICE) is None
    assert await fingerprint(SHARE_SERVICE) is None
    assert await AppSettingsRepository.get_instance_seed(SHARE_SERVICE) == ""

    keys = await asyncio.gather(*(ensure_instance_key(SHARE_SERVICE) for _ in range(5)))
    assert len({bytes(key) for key in keys}) == 1
    seed = await AppSettingsRepository.get_instance_seed(SHARE_SERVICE)
    assert len(bytes.fromhex(seed)) == 32
    assert await identity_ready(SHARE_SERVICE) is True

    # A restart: nothing in memory, the stored seed comes back.
    instance_identity._locks.clear()
    again = await ensure_instance_key(SHARE_SERVICE)
    assert bytes(again) == bytes(keys[0])
    assert await AppSettingsRepository.get_instance_seed(SHARE_SERVICE) == seed
    shown = await fingerprint(SHARE_SERVICE)
    assert shown is not None and re.match(r"^[0-9A-F]{4}( [0-9A-F]{4}){3}$", shown)


@pytest.mark.asyncio
async def test_an_unreadable_seed_is_replaced(test_db):
    await AppSettingsRepository.set_instance_seed(SHARE_SERVICE, "not hex")
    assert await identity_ready(SHARE_SERVICE) is False
    await ensure_instance_key(SHARE_SERVICE)
    assert len(bytes.fromhex(await AppSettingsRepository.get_instance_seed(SHARE_SERVICE))) == 32


@pytest.mark.asyncio
async def test_unknown_service_is_refused(test_db):
    with pytest.raises(ValueError):
        await ensure_instance_key("share_instance_seed; --")


@pytest.mark.asyncio
async def test_regenerate_and_restore_hooks(test_db):
    first = bytes(await ensure_instance_key(SHARE_SERVICE))
    await regenerate(SHARE_SERVICE)
    second = bytes(await ensure_instance_key(SHARE_SERVICE))
    assert second != first

    await run_on_restore_hooks("json")
    assert bytes(await ensure_instance_key(SHARE_SERVICE)) != second

    # A snapshot put back in place has its seeds cleared: none is created until
    # the first use, which then gets a fresh one.
    await AppSettingsRepository.set_instance_seed(SHARE_SERVICE, "")
    await run_on_restore_hooks("sqlite")
    assert await identity_ready(SHARE_SERVICE) is False


@pytest.mark.asyncio
async def test_the_seed_never_leaves(test_db, client, caplog):
    caplog.set_level(logging.DEBUG)
    token = await mint_share_jwt("https://example.com/")
    seed = await AppSettingsRepository.get_instance_seed(SHARE_SERVICE)
    assert seed

    settings = await client.get("/api/settings")
    assert settings.status_code == 200
    assert seed not in settings.text and seed.upper() not in settings.text

    v1 = json.dumps((await export_json(include_secrets=True)).model_dump(mode="json"))
    v2 = json.dumps(export_document(await export_backup_v2()))
    for document in (v1, v2):
        assert seed not in document and seed.upper() not in document

    await restore_json(BackupRestoreRequest(confirm=True))
    await regenerate(SHARE_SERVICE)
    new_seed = await AppSettingsRepository.get_instance_seed(SHARE_SERVICE)
    for secret in (seed, new_seed, token):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_created_is_logged_only_by_the_writer_that_won(test_db, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="app.services.instance_identity")
    winner = "cd" * 32

    async def lose_the_claim(service: str, seed: str) -> str:
        await AppSettingsRepository.set_instance_seed(service, winner)
        return winner

    monkeypatch.setattr(AppSettingsRepository, "claim_instance_seed", lose_the_claim)
    await ensure_instance_key(SHARE_SERVICE)
    assert "Created the share instance identity" not in caplog.text
    assert await AppSettingsRepository.get_instance_seed(SHARE_SERVICE) == winner

    monkeypatch.undo()
    await AppSettingsRepository.set_instance_seed(SHARE_SERVICE, "")
    await ensure_instance_key(SHARE_SERVICE)
    assert "Created the share instance identity" in caplog.text
