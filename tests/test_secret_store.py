"""``app.secret_store``: the key file and the AES-256-GCM tokens (MSHL-133)."""

import base64
import logging
import os
import stat
import sys

import pytest

from app import secret_store
from app.secret_store import SecretStoreUnavailable, SecretUnreadable

AAD = secret_store.aad("notification_channels", "webhook-1", "url")
SECRET = "https://hooks.example.org/very-secret-token"


@pytest.fixture
def key_file(tmp_path):
    """A fresh key file in a temporary folder; the default key is reloaded afterwards."""
    path = tmp_path / "data" / secret_store.KEY_FILENAME
    secret_store.reset()
    assert secret_store.ensure_key(path)
    yield path
    secret_store.reset()


def test_round_trip_with_the_same_aad(key_file):
    token = secret_store.encrypt(SECRET, AAD)
    assert token.startswith("v1:") and SECRET not in token
    assert secret_store.is_encrypted(token)
    assert secret_store.decrypt(token, AAD) == SECRET


def test_every_encryption_uses_a_fresh_nonce(key_file):
    tokens = {secret_store.encrypt(SECRET, AAD) for _ in range(20)}
    assert len(tokens) == 20
    nonces = {base64.b64decode(t[3:])[: secret_store.NONCE_BYTES] for t in tokens}
    assert len(nonces) == 20


@pytest.mark.parametrize(
    "other",
    [
        secret_store.aad("notification_channels", "webhook-2", "url"),  # another row
        secret_store.aad("notification_channels", "webhook-1", "hmac_secret"),  # another field
        secret_store.aad("fanout_configs", "webhook-1", "url"),  # another table
    ],
)
def test_a_token_copied_to_another_row_or_field_does_not_decrypt(key_file, other):
    token = secret_store.encrypt(SECRET, AAD)
    with pytest.raises(SecretUnreadable):
        secret_store.decrypt(token, other)


def test_a_tampered_token_is_rejected(key_file):
    raw = bytearray(base64.b64decode(secret_store.encrypt(SECRET, AAD)[3:]))
    for index in (0, secret_store.NONCE_BYTES + 1, len(raw) - 1):  # nonce, body, tag
        tampered = bytearray(raw)
        tampered[index] ^= 0x01
        with pytest.raises(SecretUnreadable):
            secret_store.decrypt("v1:" + base64.b64encode(bytes(tampered)).decode(), AAD)


@pytest.mark.parametrize(
    "token", ["v1:", "v1:not base64!", "v1:" + base64.b64encode(b"x" * 8).decode()]
)
def test_a_malformed_token_is_unreadable(key_file, token):
    with pytest.raises(SecretUnreadable):
        secret_store.decrypt(token, AAD)


@pytest.mark.parametrize("plain", [SECRET, "", "v2:something", "plain password"])
def test_a_plain_value_is_read_unchanged(key_file, plain):
    assert not secret_store.is_encrypted(plain)
    assert secret_store.decrypt(plain, AAD) == plain
    assert secret_store.unseal(plain, AAD) == plain


def test_seal_keeps_any_json_value(key_file):
    headers = {"Authorization": "Bearer abc", "X-Token": "1"}
    token = secret_store.seal(headers, AAD)
    assert "Bearer" not in token
    assert secret_store.unseal(token, AAD) == headers
    assert secret_store.unseal({"a": 1}, AAD) == {"a": 1}  # a plain dict is a legacy value
    assert secret_store.opens(token, AAD)
    assert not secret_store.opens(token, secret_store.aad("x", "y", "z"))
    assert not secret_store.opens("plain", AAD)


def test_another_key_cannot_decrypt(key_file, tmp_path):
    token = secret_store.encrypt(SECRET, AAD)
    secret_store.reset()
    assert secret_store.ensure_key(tmp_path / "other" / secret_store.KEY_FILENAME)
    with pytest.raises(SecretUnreadable):
        secret_store.decrypt(token, AAD)
    secret_store.reset()
    assert secret_store.ensure_key(key_file)  # the original file brings it back
    assert secret_store.decrypt(token, AAD) == SECRET


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_key_file_is_created_with_mode_0600(key_file):
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    assert len(key_file.read_bytes()) == secret_store.KEY_BYTES


def test_an_existing_key_is_never_overwritten(key_file):
    before = key_file.read_bytes()
    secret_store.reset()
    assert secret_store.ensure_key(key_file)
    assert key_file.read_bytes() == before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_a_key_readable_by_others_is_tightened(key_file):
    os.chmod(key_file, 0o644)
    secret_store.reset()
    assert secret_store.ensure_key(key_file)
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_a_key_of_the_wrong_size_is_moved_aside(key_file):
    key_file.write_bytes(b"short")
    secret_store.reset()
    assert secret_store.ensure_key(key_file)
    assert len(key_file.read_bytes()) == secret_store.KEY_BYTES
    aside = list(key_file.parent.glob(f"{secret_store.KEY_FILENAME}.invalid-*"))
    assert len(aside) == 1 and aside[0].read_bytes() == b"short"


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX, not root")
def test_ensure_key_never_raises_and_encrypt_then_refuses(tmp_path, monkeypatch):
    folder = tmp_path / "readonly"
    folder.mkdir()
    os.chmod(folder, 0o500)
    monkeypatch.setattr(secret_store, "key_path", lambda: folder / secret_store.KEY_FILENAME)
    try:
        secret_store.reset()
        assert secret_store.ensure_key() is False
        with pytest.raises(SecretStoreUnavailable):
            secret_store.encrypt(SECRET, AAD)
        assert secret_store.decrypt("plain", AAD) == "plain"  # a plain read still works
    finally:
        os.chmod(folder, 0o700)
        secret_store.reset()


def test_no_key_and_no_secret_in_the_logs(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    path = tmp_path / secret_store.KEY_FILENAME
    secret_store.reset()
    try:
        assert secret_store.ensure_key(path)
        token = secret_store.encrypt(SECRET, AAD)
        with pytest.raises(SecretUnreadable):
            secret_store.decrypt(token, "wrong")
        path.write_bytes(b"bad")
        secret_store.reset()
        assert secret_store.ensure_key(path)
        key = path.read_bytes()
    finally:
        secret_store.reset()
    text = caplog.text
    assert SECRET not in text and token not in text
    for value in (key, *[p.read_bytes() for p in tmp_path.glob("secrets.key*")]):
        assert value.hex() not in text and base64.b64encode(value).decode() not in text
