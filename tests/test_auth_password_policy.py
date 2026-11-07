"""Password policy (app/auth/passwords.py)."""

import pytest

from app.auth.passwords import (
    COMMON_PASSWORDS,
    MIN_PASSWORD_LENGTH,
    _fold,
    password_policy_violation,
)


def test_common_list_entries_pass_the_length_rule():
    assert len(COMMON_PASSWORDS) >= 130
    assert all(len(p) >= MIN_PASSWORD_LENGTH and p == _fold(p) for p in COMMON_PASSWORDS)


@pytest.mark.parametrize(
    ("password", "username", "reason"),
    [
        ("", "alice", "too_short"),
        ("ninechars", "alice", "too_short"),
        ("x" * 1025, "alice", "too_long"),
        ("Alice-Example1", " alice-example1 ", "same_as_username"),
        ("ÉLISE-DURAND-1", "élise-durand-1", "same_as_username"),
        ("Password1234", "alice", "too_common"),
        ("Password12", "alice", "too_common"),
        ("1234567890", "alice", "too_common"),
        ("AZERTYUIOP", "alice", "too_common"),
        ("MeshLoom12", "alice", "too_common"),
        ("AZERTYUIOP123", "alice", "too_common"),
        ("correct horse battery", "alice", None),
        ("twelve chars", "alice", None),
        ("ten chars!", "alice", None),
    ],
)
def test_policy(password, username, reason):
    assert password_policy_violation(password, username) == reason
