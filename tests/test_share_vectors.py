"""Encrypted envelope v1 against the contract vectors (MSHL-254, app/services/share_crypto.py).

Provenance: tests/fixtures/links_files_vectors.json is a byte-for-byte copy of
TwinRocket/meshloom-community docs/contracts/vectors/links-files.json on main
at the merge commit of PR #141 (3856ca17; vectors version 1, MSHL-370), rules in
docs/contracts/links-files.md, "Encrypted envelope v1". sha256 below (the same
file is pinned in tests/test_share_identity.py). To update, copy the file again
and change the hash; never edit the copy to make a test pass. A failure is a
divergence between Meshloom and the contract: report it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.services import share_crypto
from app.services.share_crypto import (
    EnvelopeAuthError,
    EnvelopeError,
    decrypt_envelope,
    derive_file_key,
    encrypt_envelope,
    sanitize_name,
)

VECTORS_PATH = Path(__file__).parent / "fixtures" / "links_files_vectors.json"
VECTORS_SHA256 = "ab2c633174034b8ea0ce026bfbf21029c152f762cb3a7c213ce4aa8388611275"
VECTORS: dict[str, Any] = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))
ENV = VECTORS["envelope"]
SECRETS = {name: bytes.fromhex(item["hex"]) for name, item in ENV["secrets"].items()}
OK_CASES = [case for case in ENV["cases"] if case["expect"] == "ok"]
BAD_CASES = [case for case in ENV["cases"] if case["expect"] != "ok"]


def test_vectors_are_an_unedited_copy() -> None:
    digest = hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest()
    assert digest == VECTORS_SHA256, (
        "copy changed: re-copy from meshloom-community and update the hash"
    )


def test_constants_match_the_contract() -> None:
    assert ENV["version"] == 1 == share_crypto.VERSION
    assert ENV["magic"].encode() == share_crypto.MAGIC
    assert ENV["hkdf"]["hash"] == "SHA-256"
    assert ENV["hkdf"]["salt"].encode() == share_crypto.HKDF_SALT
    assert ENV["hkdf"]["length"] == share_crypto.KEY_LEN
    assert ENV["nonce_len"] == share_crypto.NONCE_LEN
    assert ENV["tag_len"] == share_crypto.TAG_LEN
    assert ENV["header_len_size"] == share_crypto.HEADER_LEN_SIZE
    assert ENV["max_header_len"] == share_crypto.MAX_HEADER_LEN
    assert ENV["max_plaintext"] == share_crypto.MAX_DATA_BYTES
    assert ENV["min_envelope"] == share_crypto.MIN_ENVELOPE_BYTES
    assert ENV["max_envelope"] == share_crypto.MAX_ENVELOPE_BYTES
    assert ENV["name_max"] == share_crypto.NAME_MAX
    assert ENV["name_ext_max"] == share_crypto.NAME_EXT_MAX
    assert {case["expect"] for case in ENV["cases"]} == {
        "ok",
        "malformed",
        "unsupported_version",
        "auth_failed",
        "header_too_long",
        "size_mismatch",
    }


@pytest.mark.parametrize("case", ENV["cases"], ids=lambda case: case["id"])
def test_key_and_aad(case: dict[str, Any]) -> None:
    if "key_hex" in case:
        assert derive_file_key(SECRETS[case["secret"]], case["object_id"]).hex() == case["key_hex"]
    if "aad_hex" in case:
        assert (share_crypto.MAGIC + case["object_id"].encode()).hex() == case["aad_hex"]


@pytest.mark.parametrize("case", OK_CASES, ids=lambda case: case["id"])
def test_ok_vector_decrypts_and_reencrypts(case: dict[str, Any]) -> None:
    secret, object_id = SECRETS[case["secret"]], case["object_id"]
    envelope, data = bytes.fromhex(case["envelope_hex"]), bytes.fromhex(case["data_hex"])
    content = decrypt_envelope(secret, object_id, envelope)
    assert content.data == data
    assert content.size == case["header"]["s"] == len(data)
    assert content.content_type == case["header"]["t"]
    assert content.name == sanitize_name(case["header"]["n"])
    assert share_crypto._header_bytes(case["header"]["n"], case["header"]["t"], len(data)) == case[
        "header_json"
    ].encode("utf-8")
    sealed = share_crypto._seal(
        secret,
        object_id,
        case["header"]["n"],
        case["header"]["t"],
        data,
        bytes.fromhex(case["nonce_hex"]),
    )
    assert sealed == envelope


@pytest.mark.parametrize("case", BAD_CASES, ids=lambda case: case["id"])
def test_negative_vector_fails_with_its_code(case: dict[str, Any]) -> None:
    with pytest.raises(EnvelopeError) as raised:
        decrypt_envelope(
            SECRETS[case["secret"]], case["object_id"], bytes.fromhex(case["envelope_hex"])
        )
    assert raised.value.code == case["expect"]
    assert isinstance(raised.value, EnvelopeAuthError) == (case["expect"] == "auth_failed")


@pytest.mark.parametrize("item", ENV["refused_secrets"], ids=lambda item: item["id"])
def test_refused_secret_before_any_crypto(
    item: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = bytes.fromhex(item["hex"])
    envelope = bytes.fromhex(OK_CASES[0]["envelope_hex"])

    def no_kdf(*_args: Any, **_kwargs: Any) -> bytes:
        raise AssertionError("HKDF called with a refused secret")

    monkeypatch.setattr(share_crypto, "HKDF", no_kdf)
    for call in (
        lambda: derive_file_key(secret, "AbCdEfG"),
        lambda: encrypt_envelope(secret, "AbCdEfG", "a.png", "image/png", b"x"),
        lambda: decrypt_envelope(secret, "AbCdEfG", envelope),
        lambda: decrypt_envelope(secret, "AbCdEfG", b""),  # refused before the envelope is read
    ):
        with pytest.raises(EnvelopeError) as raised:
            call()
        assert raised.value.code == "refused_secret"


@pytest.mark.parametrize("item", ENV["sanitize_name"], ids=lambda item: repr(item["in"])[:40])
def test_sanitize_name(item: dict[str, str]) -> None:
    assert sanitize_name(item["in"]) == item["out"]
    assert sanitize_name(item["out"]) == item["out"]
