"""Property and boundary tests for the encrypted envelope v1 (MSHL-254).

The contract vectors are in tests/test_share_vectors.py; these tests cover the
failure modes on generated input and the off-loop async wrappers.
"""

from __future__ import annotations

import asyncio
import struct
import threading

import pytest
from Crypto.Cipher import AES
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services import share, share_crypto
from app.services.share_crypto import (
    MAX_DATA_BYTES,
    EnvelopeAuthError,
    EnvelopeError,
    decrypt_envelope,
    decrypt_envelope_async,
    derive_file_key,
    encrypt_envelope,
    encrypt_envelope_async,
)

CHANNEL = bytes(range(1, 17))
DM = bytes(range(1, 33))
OBJECT_ID = "AbCdEfG"
ids = st.from_regex(r"\A[1-9A-HJ-NP-Za-km-z]{7}\Z")
secrets_ = st.one_of(
    st.binary(min_size=16, max_size=16), st.binary(min_size=32, max_size=32)
).filter(any)
PROPS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def _seal_plaintext(secret: bytes, object_id: str, plaintext: bytes) -> bytes:
    """An authentic envelope around an arbitrary plaintext (a non-conforming encryptor)."""
    nonce = bytes(12)
    cipher = AES.new(derive_file_key(secret, object_id), AES.MODE_GCM, nonce=nonce, mac_len=16)
    cipher.update(b"MLF1" + object_id.encode())
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return b"MLF1\x01" + nonce + ciphertext + tag


def _code(secret: bytes, object_id: str, blob: bytes) -> str:
    with pytest.raises(EnvelopeError) as raised:
        decrypt_envelope(secret, object_id, blob)
    return raised.value.code


def test_constants_shared_with_share_service() -> None:
    assert share_crypto.MAX_DATA_BYTES == share.MAX_IMAGE_BYTES
    assert share_crypto.MAX_ENVELOPE_BYTES == share.MAX_ENVELOPE_BYTES == 10_489_891
    assert share_crypto.MIN_ENVELOPE_BYTES == 35


@PROPS
@given(secret=secrets_, object_id=ids, name=st.text(max_size=60), data=st.binary(max_size=2048))
def test_round_trip(secret: bytes, object_id: str, name: str, data: bytes) -> None:
    blob = encrypt_envelope(secret, object_id, name, "image/png", data)
    assert len(blob) == 35 + len(share_crypto._header_bytes(name, "image/png", len(data))) + len(
        data
    )
    content = decrypt_envelope(secret, object_id, blob)
    assert content.data == data
    assert content.size == len(data)
    assert content.content_type == "image/png"
    assert content.name == (share_crypto.sanitize_name(name) or object_id + ".png")


def test_fresh_nonce_per_encryption() -> None:
    first = encrypt_envelope(CHANNEL, OBJECT_ID, "a.png", "image/png", b"x")
    second = encrypt_envelope(CHANNEL, OBJECT_ID, "a.png", "image/png", b"x")
    assert first[5:17] != second[5:17]
    assert first != second


@PROPS
@given(secret=secrets_, other=secrets_, data=st.binary(max_size=256))
def test_wrong_secret_fails_auth(secret: bytes, other: bytes, data: bytes) -> None:
    if other == secret:
        return
    blob = encrypt_envelope(secret, OBJECT_ID, "a.png", "image/png", data)
    with pytest.raises(EnvelopeAuthError) as raised:
        decrypt_envelope(other, OBJECT_ID, blob)
    # No plaintext leaks on failure: the error carries only its code.
    assert raised.value.args == ("auth_failed",)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None or not isinstance(
        raised.value.__context__, EnvelopeError
    )


@PROPS
@given(object_id=ids, other=ids)
def test_wrong_id_fails_auth(object_id: str, other: str) -> None:
    if other == object_id:
        return
    blob = encrypt_envelope(DM, object_id, "a.png", "image/png", b"secret bytes")
    assert _code(DM, other, blob) == "auth_failed"


@PROPS
@given(data=st.binary(max_size=256), cut=st.integers(min_value=1, max_value=300))
def test_truncated_blob_fails(data: bytes, cut: int) -> None:
    blob = encrypt_envelope(CHANNEL, OBJECT_ID, "a.png", "image/png", data)
    cut = min(cut, len(blob))
    truncated = blob[:-cut]
    expected = "malformed" if len(truncated) < 35 else "auth_failed"
    assert _code(CHANNEL, OBJECT_ID, truncated) == expected


@PROPS
@given(
    data=st.binary(min_size=1, max_size=128),
    position=st.integers(min_value=5),
    bit=st.integers(0, 7),
)
def test_any_flipped_byte_after_version_fails_auth(data: bytes, position: int, bit: int) -> None:
    blob = bytearray(encrypt_envelope(CHANNEL, OBJECT_ID, "a.png", "image/png", data))
    blob[5 + position % (len(blob) - 5)] ^= 1 << bit
    assert _code(CHANNEL, OBJECT_ID, bytes(blob)) == "auth_failed"


def test_header_too_long() -> None:
    with pytest.raises(EnvelopeError) as raised:
        encrypt_envelope(CHANNEL, OBJECT_ID, "x" * 4096, "image/png", b"")
    assert raised.value.code == "header_too_long"
    header = b'{"n":"","t":"image/png","s":0}'.ljust(4097, b" ")
    blob = _seal_plaintext(CHANNEL, OBJECT_ID, struct.pack(">H", 4097) + header)
    assert _code(CHANNEL, OBJECT_ID, blob) == "header_too_long"
    # header_len 4097 is refused even when the plaintext is shorter than that.
    assert (
        _code(CHANNEL, OBJECT_ID, _seal_plaintext(CHANNEL, OBJECT_ID, b"\x10\x01{}"))
        == "header_too_long"
    )


def test_header_at_the_limit_is_accepted() -> None:
    overhead = len(share_crypto._header_bytes("", "image/png", 0))
    blob = encrypt_envelope(CHANNEL, OBJECT_ID, "x" * (4096 - overhead), "image/png", b"")
    assert decrypt_envelope(CHANNEL, OBJECT_ID, blob).size == 0


@PROPS
@given(data=st.binary(max_size=256), delta=st.integers(min_value=-300, max_value=300).filter(bool))
def test_size_mismatch(data: bytes, delta: int) -> None:
    size = len(data) + delta
    if size < 0:
        return
    header = share_crypto._header_bytes("a.png", "image/png", size)
    blob = _seal_plaintext(CHANNEL, OBJECT_ID, struct.pack(">H", len(header)) + header + data)
    assert _code(CHANNEL, OBJECT_ID, blob) == "size_mismatch"


def test_header_len_out_of_range() -> None:
    assert (
        _code(CHANNEL, OBJECT_ID, _seal_plaintext(CHANNEL, OBJECT_ID, b"\x00\x00"))
        == "header_invalid"
    )
    assert (
        _code(CHANNEL, OBJECT_ID, _seal_plaintext(CHANNEL, OBJECT_ID, b"\x00\x05{}"))
        == "header_invalid"
    )


@pytest.mark.parametrize(
    "header",
    [
        b"{}",  # missing members
        b'{"n":"","t":"","s":0,"x":1}',  # extra member
        b'{"n":"","t":"","s":0,"s":0}',  # duplicate member
        b'{"n":"","t":"","s":true}',  # bool size
        b'{"n":"","t":"","s":0.0}',  # float size
        b'{"n":"","t":"","s":NaN}',  # not JSON
        b'{"n":1,"t":"","s":0}',  # wrong type
        b'{"n":"","t":null,"s":0}',  # wrong type
        b'{"n":"","t":"","s":-1}',  # negative size
        b'{"n":"","t":"","s":10485761}',  # size over the cap
        b'\xef\xbb\xbf{"n":"","t":"","s":0}',  # BOM
        b'{"n":"\xff","t":"","s":0}',  # invalid UTF-8
        b'{"n":"","t":"","s":0',  # truncated JSON
        b"[]",  # not an object
    ],
)
def test_header_invalid(header: bytes) -> None:
    plaintext = struct.pack(">H", len(header)) + header
    assert (
        _code(CHANNEL, OBJECT_ID, _seal_plaintext(CHANNEL, OBJECT_ID, plaintext))
        == "header_invalid"
    )


def test_lenient_header_parsing_is_accepted() -> None:
    header = b' { "s" : 3 , "t" : "image/png", "n" : "\\u00e9t\\u00e9.png" } '
    blob = _seal_plaintext(CHANNEL, OBJECT_ID, struct.pack(">H", len(header)) + header + b"abc")
    content = decrypt_envelope(CHANNEL, OBJECT_ID, blob)
    assert (content.name, content.content_type, content.data) == ("été.png", "image/png", b"abc")


def test_empty_sanitised_name_falls_back_to_the_id() -> None:
    blob = encrypt_envelope(CHANNEL, OBJECT_ID, "​..", "image/jpeg", b"x")
    assert decrypt_envelope(CHANNEL, OBJECT_ID, blob).name == "AbCdEfG.jpg"
    blob = encrypt_envelope(CHANNEL, OBJECT_ID, "", "application/x-other", b"x")
    assert decrypt_envelope(CHANNEL, OBJECT_ID, blob).name == "AbCdEfG"


def test_ten_mib_boundary() -> None:
    data = bytes(MAX_DATA_BYTES)
    name = "n" * (4096 - len(share_crypto._header_bytes("", "image/png", MAX_DATA_BYTES)))
    blob = encrypt_envelope(DM, OBJECT_ID, name, "image/png", data)
    assert len(blob) == share_crypto.MAX_ENVELOPE_BYTES
    assert decrypt_envelope(DM, OBJECT_ID, blob).size == MAX_DATA_BYTES
    with pytest.raises(EnvelopeError) as raised:
        encrypt_envelope(DM, OBJECT_ID, "a.png", "image/png", data + b"\x00")
    assert raised.value.code == "too_large"
    assert _code(DM, OBJECT_ID, blob + b"\x00") == "malformed"


def test_framing_checks_run_before_decryption() -> None:
    blob = encrypt_envelope(CHANNEL, OBJECT_ID, "a.png", "image/png", b"x")
    assert _code(CHANNEL, OBJECT_ID, b"MLF2" + blob[4:]) == "malformed"
    assert _code(CHANNEL, OBJECT_ID, blob[:4] + b"\x02" + blob[5:]) == "unsupported_version"
    assert _code(CHANNEL, OBJECT_ID, blob[:34]) == "malformed"


@pytest.mark.parametrize("object_id", ["", "AbCdEf", "AbCdEfGh", "AbCdEf0", "AbCdEfI", "AbCdEf/"])
def test_invalid_object_id(object_id: str) -> None:
    with pytest.raises(EnvelopeError) as raised:
        derive_file_key(CHANNEL, object_id)
    assert raised.value.code == "invalid_id"


async def test_async_wrappers_run_off_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_encrypt, real_decrypt = share_crypto.encrypt_envelope, share_crypto.decrypt_envelope

    def encrypt(*args: object) -> bytes:
        seen["encrypt"] = threading.get_ident()
        return real_encrypt(*args)  # type: ignore[arg-type]

    def decrypt(*args: object) -> share_crypto.EnvelopeContent:
        seen["decrypt"] = threading.get_ident()
        return real_decrypt(*args)  # type: ignore[arg-type]

    monkeypatch.setattr(share_crypto, "encrypt_envelope", encrypt)
    monkeypatch.setattr(share_crypto, "decrypt_envelope", decrypt)
    blob = await encrypt_envelope_async(CHANNEL, OBJECT_ID, "a.png", "image/png", b"data")
    content = await decrypt_envelope_async(CHANNEL, OBJECT_ID, blob)
    assert content.data == b"data"
    assert seen.keys() == {"encrypt", "decrypt"}
    assert loop_thread not in seen.values()
    assert asyncio.get_running_loop() is not None


async def test_async_decrypt_propagates_errors() -> None:
    with pytest.raises(EnvelopeAuthError):
        blob = await encrypt_envelope_async(CHANNEL, OBJECT_ID, "a.png", "image/png", b"x")
        await decrypt_envelope_async(DM, OBJECT_ID, blob)
