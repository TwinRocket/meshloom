"""decide_identity_restore over the twelve rows of the TR-1 plan (C.4)."""

import pytest

from app.services.backup import BackupError
from app.services.backup_restore import (
    check_identity_confirmations,
    decide_identity_restore,
)

P = "aa" * 32  # the backup's key
L = "bb" * 32  # the live radio's key
B = "cc" * 32  # the key the database is bound to

MISMATCH = "identity_mismatch"
LEGACY = "identity_unbound_legacy"

# (row, gate, bound, live, has_history) ->
#   (relation, import, wipe, bind, deferred, requires_replace_live, requires_wipe, refusal)
ROWS = [
    (1, None, P, P, True, ("same", False, False, False, False, False, False, None)),
    (2, None, P, None, True, ("same", False, False, False, True, False, False, None)),
    (3, None, None, None, False, ("new_database", False, False, False, True, False, False, None)),
    (4, None, None, None, True, ("new_database", False, False, False, True, False, False, None)),
    (5, None, L, L, False, ("replaces_live", True, False, True, False, True, False, None)),
    (6, None, L, L, True, ("replaces_live", True, True, True, False, True, True, None)),
    (
        7,
        None,
        B,
        None,
        True,
        ("needs_radio", False, False, False, False, False, False, "backup_identity_needs_radio"),
    ),
    (8, MISMATCH, P, L, True, ("restores_bound", True, False, True, False, True, False, None)),
    (9, MISMATCH, B, P, True, ("replaces_history", False, True, True, False, False, True, None)),
    (
        10,
        MISMATCH,
        B,
        L,
        True,
        ("conflict", False, False, False, False, False, False, "backup_identity_conflict"),
    ),
    (11, LEGACY, None, P, True, ("same", False, False, True, False, False, False, None)),
    (12, LEGACY, None, L, True, ("replaces_live", True, False, True, False, True, False, None)),
]


@pytest.mark.parametrize(("row", "gate", "bound", "live", "history", "expected"), ROWS)
def test_decision_table(row, gate, bound, live, history, expected):
    decision = decide_identity_restore(
        gate=gate, bound=bound, live=live, backup=P, has_history=history
    )
    got = (
        decision.relation,
        decision.import_key,
        decision.wipe,
        decision.bind,
        decision.deferred,
        decision.requires_replace_live,
        decision.requires_wipe,
        decision.refusal,
    )
    assert got == expected, f"row {row}"


def test_rows_3_and_4_never_bind_the_backup_key():
    for history in (False, True):
        decision = decide_identity_restore(
            gate=None, bound=None, live=None, backup=P, has_history=history
        )
        assert decision.bind is False and decision.import_key is False


def test_row_12_keeps_the_history_unless_asked():
    decision = decide_identity_restore(gate=LEGACY, bound=None, live=L, backup=P, has_history=True)
    assert decision.wipe is False and decision.wipe_optional is True


@pytest.mark.parametrize("history", [False, True])
def test_an_unbound_database_offers_the_wipe_only_with_history(history):
    decision = decide_identity_restore(gate=None, bound=None, live=L, backup=P, has_history=history)
    assert decision.wipe is False and decision.wipe_optional is history


@pytest.mark.parametrize("history", [False, True])
@pytest.mark.parametrize("gate,bound", [(None, L), (MISMATCH, P), (LEGACY, None), (None, None)])
def test_a_known_live_key_other_than_the_backup_always_needs_confirmation(gate, bound, history):
    decision = decide_identity_restore(
        gate=gate, bound=bound, live=L, backup=P, has_history=history
    )
    assert decision.import_key is True
    assert decision.requires_replace_live is True


def test_keys_are_compared_case_insensitively():
    decision = decide_identity_restore(
        gate=None, bound=P.upper(), live=P.upper(), backup=P, has_history=True
    )
    assert decision.relation == "same"


def test_no_private_key_means_no_identity_step():
    decision = decide_identity_restore(gate=None, bound=B, live=L, backup=None, has_history=True)
    assert decision.relation == "no_identity" and decision.refusal is None


def _codes(decision, **kwargs):
    try:
        check_identity_confirmations(decision, **kwargs)
    except BackupError as exc:
        return exc.code, exc.status
    return None


def test_confirmations():
    row6 = decide_identity_restore(gate=None, bound=L, live=L, backup=P, has_history=True)
    base = {"live": L, "expected_live_public_key": L}
    assert _codes(row6, replace_live_identity=False, wipe_history=False, **base) == (
        "backup_identity_replaces_live",
        409,
    )
    assert _codes(row6, replace_live_identity=True, wipe_history=False, **base) == (
        "backup_identity_wipe_required",
        409,
    )
    assert _codes(row6, replace_live_identity=True, wipe_history=True, **base) is None
    # The expected live key must name the radio actually connected.
    assert _codes(
        row6, replace_live_identity=True, wipe_history=True, live=L, expected_live_public_key=B
    ) == ("backup_identity_replaces_live", 409)

    row7 = decide_identity_restore(gate=None, bound=B, live=None, backup=P, has_history=True)
    assert _codes(row7, replace_live_identity=True, wipe_history=True, **base) == (
        "backup_identity_needs_radio",
        409,
    )
    row10 = decide_identity_restore(gate=MISMATCH, bound=B, live=L, backup=P, has_history=True)
    assert _codes(row10, replace_live_identity=True, wipe_history=True, **base) == (
        "backup_identity_conflict",
        409,
    )
