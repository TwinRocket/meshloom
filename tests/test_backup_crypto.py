"""Passphrase envelope of the full backup: round trip, tampering, KDF bounds."""

from __future__ import annotations

import asyncio
import base64
import copy
import threading
import time
import unicodedata

import pytest

import app.services.backup_crypto as crypto
from app.services.backup_crypto import (
    BackupCryptoError,
    decrypt_backup,
    decrypt_backup_async,
    encrypt_backup,
    encrypt_backup_async,
    is_encrypted_backup,
)

PASSPHRASE = "correct horse battery"
DOCUMENT = {
    "format": "meshloom-backup-v2",
    "exported_at": 1760000000,
    "identity": {"private_key": "ab" * 64},
    "wifi": {"ssid": "Home", "password": " p@ss word "},
}


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    """Small scrypt cost for speed; the bounds checks scale with it."""
    monkeypatch.setattr(crypto, "SCRYPT_N", 2**4)
    monkeypatch.setattr(crypto, "SCRYPT_MIN_N", 2**4)
    monkeypatch.setattr(crypto, "SCRYPT_MAX_N", 2**6)


def test_round_trip():
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key="cd" * 32)
    assert is_encrypted_backup(envelope)
    assert envelope["public_key"] == "cd" * 32
    assert envelope["exported_at"] == DOCUMENT["exported_at"]
    assert "ab" * 64 not in str(envelope)
    assert decrypt_backup(envelope, PASSPHRASE) == DOCUMENT


def test_wrong_passphrase():
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key=None)
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(envelope, PASSPHRASE + "x")
    assert exc.value.code == "backup_bad_passphrase"
    assert PASSPHRASE not in str(exc.value)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda env: env.__setitem__("exported_at", env["exported_at"] + 1),
        lambda env: env.__setitem__("public_key", "ee" * 32),
    ],
)
def test_header_tampering_fails(mutate):
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key="cd" * 32)
    mutate(envelope)
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(envelope, PASSPHRASE)
    assert exc.value.code == "backup_bad_passphrase"


def test_ciphertext_tampering_fails():
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key=None)
    blob = bytearray(base64.b64decode(envelope["ciphertext"]))
    blob[0] ^= 0x01
    envelope["ciphertext"] = base64.b64encode(bytes(blob)).decode()
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(envelope, PASSPHRASE)
    assert exc.value.code == "backup_bad_passphrase"


def test_unknown_extra_keys_do_not_change_the_aad():
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key=None)
    envelope["comment"] = "added by hand"
    envelope["kdf"]["note"] = "ignored"
    envelope["cipher"]["extra"] = 1
    assert decrypt_backup(envelope, PASSPHRASE) == DOCUMENT


def _count_kdf(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = crypto.scrypt

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(crypto, "scrypt", counting)
    return calls


@pytest.mark.parametrize(
    "kdf_change",
    [
        {"n": 2**20},
        {"n": 2**4 + 1},  # not a power of two
        {"n": 2**3},  # below the minimum
        {"r": 16},
        {"p": 4},
        {"name": "pbkdf2"},
        {"salt": base64.b64encode(b"short").decode()},
        {"n": True},
    ],
)
def test_out_of_bounds_kdf_refused_before_any_kdf_call(monkeypatch, kdf_change):
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key=None)
    envelope["kdf"].update(kdf_change)
    calls = _count_kdf(monkeypatch)
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(envelope, PASSPHRASE)
    assert exc.value.code == "backup_bad_envelope"
    assert calls == []


@pytest.mark.parametrize(
    "change",
    [
        lambda env: env["cipher"].update(nonce=base64.b64encode(b"x" * 8).decode()),
        lambda env: env["cipher"].update(name="chacha20"),
        lambda env: env.__setitem__("ciphertext", "not base64!"),
        lambda env: env.__setitem__("ciphertext", base64.b64encode(b"x" * 4).decode()),
        lambda env: env.__setitem__("exported_at", "yesterday"),
        lambda env: env.__setitem__("format", "meshloom-backup-v2"),
        lambda env: env.pop("kdf"),
    ],
)
def test_malformed_envelopes_refused_before_any_kdf_call(monkeypatch, change):
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key=None)
    change(envelope)
    calls = _count_kdf(monkeypatch)
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(envelope, PASSPHRASE)
    assert exc.value.code == "backup_bad_envelope"
    assert calls == []


def test_oversized_ciphertext_refused_before_any_kdf_call(monkeypatch):
    monkeypatch.setattr(crypto, "MAX_CIPHERTEXT_BYTES", 64)
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key=None)
    envelope["ciphertext"] = base64.b64encode(b"x" * 200).decode()
    calls = _count_kdf(monkeypatch)
    with pytest.raises(BackupCryptoError) as exc:
        decrypt_backup(envelope, PASSPHRASE)
    assert exc.value.code == "backup_bad_envelope"
    assert calls == []


def test_written_parameters_are_the_documented_ones(monkeypatch):
    monkeypatch.undo()
    assert crypto.SCRYPT_N == 2**15 and crypto.SCRYPT_R == 8 and crypto.SCRYPT_P == 1
    assert crypto.SCRYPT_MIN_N == 2**14 and crypto.SCRYPT_MAX_N == 2**16


def test_passphrase_is_nfc_normalised():
    composed = unicodedata.normalize("NFC", "pâssphrase-é")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed
    envelope = encrypt_backup(DOCUMENT, composed, public_key=None)
    assert decrypt_backup(envelope, decomposed) == DOCUMENT


def test_short_passphrase_refused():
    with pytest.raises(ValueError):
        encrypt_backup(DOCUMENT, "short", public_key=None)


@pytest.mark.asyncio
async def test_async_round_trip():
    envelope = await encrypt_backup_async(DOCUMENT, PASSPHRASE, public_key=None)
    assert await decrypt_backup_async(copy.deepcopy(envelope), PASSPHRASE) == DOCUMENT


@pytest.mark.asyncio
async def test_kdf_calls_are_serialized(monkeypatch):
    active = 0
    peak = 0
    lock = threading.Lock()
    real = crypto.scrypt

    def slow(*args, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return real(*args, **kwargs)

    monkeypatch.setattr(crypto, "scrypt", slow)
    await asyncio.gather(
        *(encrypt_backup_async(DOCUMENT, PASSPHRASE, public_key=None) for _ in range(3))
    )
    assert peak == 1


def test_semaphore_works_across_event_loops():
    for _ in range(2):
        loop = asyncio.new_event_loop()
        try:
            envelope = loop.run_until_complete(
                encrypt_backup_async(DOCUMENT, PASSPHRASE, public_key=None)
            )
            assert loop.run_until_complete(decrypt_backup_async(envelope, PASSPHRASE)) == DOCUMENT
        finally:
            loop.close()


@pytest.mark.asyncio
async def test_async_decrypt_refuses_bad_envelope_without_kdf(monkeypatch):
    envelope = encrypt_backup(DOCUMENT, PASSPHRASE, public_key=None)
    envelope["kdf"]["n"] = 2**20
    calls = _count_kdf(monkeypatch)
    with pytest.raises(BackupCryptoError):
        await decrypt_backup_async(envelope, PASSPHRASE)
    assert calls == []
