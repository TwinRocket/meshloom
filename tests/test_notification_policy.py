"""The conversation policy over N channels, from the shared fixture.

``tests/fixtures/notification_policy_cases.json`` is also read by the frontend suite
(``notificationPolicy.ts``, the cadrage's T19): the names and outcomes are the contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.notifications.rules import conversation_channel_enabled, message_channel_ids

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "notification_policy_cases.json").read_text("utf-8")
)["cases"]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_conversation_channel_enabled_vector(case: dict) -> None:
    assert (
        conversation_channel_enabled(
            state_key=case["state_key"],
            message_type=case["message_type"],
            rules=case["rules"],
            channel_id=case["channel_id"],
            is_hashtag=case["is_hashtag"],
            is_public=case["is_public"],
            muted=case["muted"],
            blocked=case["blocked"],
            now=case["now"],
        )
        is case["expected"]
    )


def test_fixture_names_are_unique_and_cover_the_rules() -> None:
    names = [c["name"] for c in CASES]
    assert len(names) == len(set(names))
    for word in ("exception", "private_key", "pause", "muted", "blocked", "hashtag"):
        assert any(word in n for n in names), word


def test_message_channel_ids_lists_every_enabled_channel() -> None:
    rules = {
        "events": {"message.dm": {"channels": ["builtin:push", "legacy:email"]}},
        "conversations": {"contact-ab": {"legacy:email": False, "webhook-1": True}},
    }
    assert message_channel_ids(
        rules, state_key="contact-ab", is_dm=True, is_public_or_hashtag=False
    ) == ["builtin:push", "webhook-1"]
    assert (
        message_channel_ids(
            rules, state_key="contact-ab", is_dm=True, is_public_or_hashtag=False, muted=True
        )
        == []
    )
