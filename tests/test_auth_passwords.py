"""argon2id password helpers (app/auth/passwords.py) and opaque tokens (app/auth/tokens.py)."""

import threading

import nacl.pwhash.argon2id as argon2id
import pytest

from app.auth import passwords, tokens
from app.auth.passwords import (
    PARAMS,
    Argon2Params,
    hash_password,
    hash_password_async,
    needs_rehash,
    verify_password,
    verify_password_async,
)

# Small parameters keep the suite fast; one test covers the real defaults.
FAST = Argon2Params(opslimit=argon2id.OPSLIMIT_MIN, memlimit=argon2id.MEMLIMIT_MIN)


def test_hash_verifies_and_rejects_a_wrong_password():
    stored = hash_password("correct horse", params=FAST)
    assert stored.startswith("$argon2id$v=19$")
    assert "correct horse" not in stored
    assert verify_password(stored, "correct horse")
    assert not verify_password(stored, "correct horsE")
    assert hash_password("correct horse", params=FAST) != stored  # fresh salt


def test_default_parameters_are_interactive():
    assert Argon2Params(argon2id.OPSLIMIT_INTERACTIVE, argon2id.MEMLIMIT_INTERACTIVE) == PARAMS
    stored = hash_password("pw")
    assert "m=65536,t=2,p=1$" in stored
    assert verify_password(stored, "pw")
    assert not needs_rehash(stored)


def test_unicode_is_normalized():
    stored = hash_password("café", params=FAST)
    assert verify_password(stored, "café")


@pytest.mark.parametrize(
    "stored", [None, "", "plain", "$argon2i$v=19$m=8,t=1,p=1$x$y", "$argon2id$junk"]
)
def test_missing_or_malformed_hash_never_verifies(stored):
    assert not verify_password(stored, "pw")
    assert needs_rehash(stored)


def test_empty_or_oversized_passwords():
    with pytest.raises(ValueError):
        hash_password("", params=FAST)
    with pytest.raises(ValueError):
        hash_password("x" * (passwords.MAX_PASSWORD_LENGTH + 1), params=FAST)
    stored = hash_password("pw", params=FAST)
    assert not verify_password(stored, "")
    assert not verify_password(stored, "x" * (passwords.MAX_PASSWORD_LENGTH + 1))


def test_needs_rehash_follows_the_parameters():
    stored = hash_password("pw", params=FAST)
    assert not needs_rehash(stored, params=FAST)
    assert needs_rehash(stored, params=Argon2Params(FAST.opslimit + 1, FAST.memlimit))
    assert needs_rehash(stored, params=Argon2Params(FAST.opslimit, FAST.memlimit * 2))
    assert needs_rehash(stored)  # module defaults are higher


@pytest.mark.asyncio
async def test_async_variants_run_off_the_event_loop(monkeypatch):
    loop_thread = threading.get_ident()
    seen: list[int] = []
    real_verify = argon2id.verify
    real_str = argon2id.str

    def spy_verify(*args, **kwargs):
        seen.append(threading.get_ident())
        return real_verify(*args, **kwargs)

    def spy_str(*args, **kwargs):
        seen.append(threading.get_ident())
        return real_str(*args, **kwargs)

    monkeypatch.setattr(passwords.argon2id, "verify", spy_verify)
    monkeypatch.setattr(passwords.argon2id, "str", spy_str)

    stored = await hash_password_async("pw", params=FAST)
    assert await verify_password_async(stored, "pw")
    assert not await verify_password_async(stored, "nope")
    assert len(seen) == 3
    assert loop_thread not in seen


def test_tokens_are_random_and_only_their_hash_matches():
    first, second = tokens.new_token(), tokens.new_token()
    assert first != second and len(first) >= 43
    stored = tokens.hash_token(first)
    assert len(stored) == 64 and first not in stored
    assert tokens.token_matches(first, stored)
    assert not tokens.token_matches(second, stored)
    assert not tokens.token_matches(first, None)
