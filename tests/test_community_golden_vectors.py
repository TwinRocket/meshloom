"""Shared golden vectors for the packet hash and the hashtag key (issue #51).

Provenance: tests/fixtures/community_golden_vectors.json is a byte-for-byte copy
of TwinRocket/meshloom-community docs/contracts/vectors/golden.json (branch
fix/L10-contracts, vectors version 1). sha256 below. To update, copy the
file again and change the hash; never edit the copy to make a test pass.
A failure is a divergence between Meshloom and Community: report it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from app.data.meshcore_channels import hashtag_hash_byte_from_name, hashtag_key_from_name
from app.path_utils import calculate_packet_hash

VECTORS_PATH = Path(__file__).parent / "fixtures" / "community_golden_vectors.json"
VECTORS_SHA256 = "8e777e64aee4e5e250057419c0c4184537d04321de7d1b0e4408a90521279cbb"
VECTORS: dict[str, Any] = json.loads(VECTORS_PATH.read_text(encoding="utf-8"))


def test_vectors_are_an_unedited_copy() -> None:
    digest = hashlib.sha256(VECTORS_PATH.read_bytes()).hexdigest()
    assert digest == VECTORS_SHA256, (
        "copy changed: re-copy from meshloom-community and update the hash"
    )
    assert VECTORS["version"] == 1


@pytest.mark.parametrize(
    "case", VECTORS["packet_hash"]["cases"], ids=[c["id"] for c in VECTORS["packet_hash"]["cases"]]
)
def test_packet_hash_vectors(case: dict[str, Any]) -> None:
    assert calculate_packet_hash(bytes.fromhex(case["raw_hex"])) == case["packet_hash"]


# Issue #71: every case, ``trailing_space`` included, hashes the exact name.
@pytest.mark.parametrize(
    "case", VECTORS["hashtag"]["cases"], ids=[c["id"] for c in VECTORS["hashtag"]["cases"]]
)
def test_hashtag_vectors(case: dict[str, Any]) -> None:
    assert hashtag_key_from_name(case["input"]).hex() == case["key_hex"]
    assert hashtag_hash_byte_from_name(case["input"]) == case["hash_byte"]
