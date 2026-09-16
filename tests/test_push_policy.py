"""Shared push conversation-enablement policy vector.

Vague C frontend ``pushPolicy.ts`` must mirror these case names and outcomes.
"""

import pytest

from app.channel_constants import PUBLIC_CHANNEL_KEY
from app.push.policy import conversation_is_enabled
from app.repository.settings import DEFAULT_PUSH_DEFAULTS, PushDefaults

DM_KEY = "aa" * 32
ROOM_KEY = "bb" * 32
HASHTAG_KEY = "cc" * 16
PRIVATE_CHAN_KEY = "dd" * 16

_ALL_ON = PushDefaults(
    new_contact=True,
    new_dm=True,
    advert_repeater=True,
    advert_companion=True,
    advert_sensor=True,
    channel_found=True,
    telemetry_alert=True,
)
_DM_OFF = PushDefaults(
    new_contact=True,
    new_dm=False,
    advert_repeater=True,
    advert_companion=True,
    advert_sensor=True,
    channel_found=True,
    telemetry_alert=True,
)


def _case(
    name: str,
    *,
    expected: bool,
    state_key: str,
    message_type: str,
    defaults: PushDefaults = _ALL_ON,
    overrides: dict[str, bool] | None = None,
    is_hashtag: bool = False,
    is_public: bool = False,
    contact_type: int | None = None,
) -> tuple:
    return (
        name,
        expected,
        {
            "state_key": state_key,
            "message_type": message_type,
            "defaults": defaults,
            "overrides": overrides or {},
            "is_hashtag": is_hashtag,
            "is_public": is_public,
            "contact_type": contact_type,
        },
    )


POLICY_CASES = [
    _case(
        "public_channel_no_override_enabled",
        expected=True,
        state_key=f"channel-{PUBLIC_CHANNEL_KEY}",
        message_type="CHAN",
        is_public=True,
    ),
    _case(
        "hashtag_channel_no_override_enabled",
        expected=True,
        state_key=f"channel-{HASHTAG_KEY}",
        message_type="CHAN",
        is_hashtag=True,
    ),
    _case(
        "private_channel_no_override_disabled",
        expected=False,
        state_key=f"channel-{PRIVATE_CHAN_KEY}",
        message_type="CHAN",
    ),
    _case(
        "dm_priv_default_new_dm_true_enabled",
        expected=True,
        state_key=f"contact-{DM_KEY}",
        message_type="PRIV",
        contact_type=1,
    ),
    _case(
        "dm_priv_default_new_dm_false_disabled",
        expected=False,
        state_key=f"contact-{DM_KEY}",
        message_type="PRIV",
        defaults=_DM_OFF,
        contact_type=1,
    ),
    _case(
        "room_priv_follows_new_dm_true_enabled",
        expected=True,
        state_key=f"contact-{ROOM_KEY}",
        message_type="PRIV",
        contact_type=3,
    ),
    _case(
        "room_priv_follows_new_dm_false_disabled",
        expected=False,
        state_key=f"contact-{ROOM_KEY}",
        message_type="PRIV",
        defaults=_DM_OFF,
        contact_type=3,
    ),
    _case(
        "override_true_enables_private_channel",
        expected=True,
        state_key=f"channel-{PRIVATE_CHAN_KEY}",
        message_type="CHAN",
        overrides={f"channel-{PRIVATE_CHAN_KEY}": True},
    ),
    _case(
        "override_false_disables_public_channel",
        expected=False,
        state_key=f"channel-{PUBLIC_CHANNEL_KEY}",
        message_type="CHAN",
        is_public=True,
        overrides={f"channel-{PUBLIC_CHANNEL_KEY}": False},
    ),
    _case(
        "override_false_disables_dm_despite_new_dm",
        expected=False,
        state_key=f"contact-{DM_KEY}",
        message_type="PRIV",
        overrides={f"contact-{DM_KEY}": False},
        contact_type=1,
    ),
    _case(
        "override_true_enables_dm_despite_new_dm_off",
        expected=True,
        state_key=f"contact-{DM_KEY}",
        message_type="PRIV",
        defaults=_DM_OFF,
        overrides={f"contact-{DM_KEY}": True},
        contact_type=1,
    ),
    _case(
        "override_true_enables_room_despite_new_dm_off",
        expected=True,
        state_key=f"contact-{ROOM_KEY}",
        message_type="PRIV",
        defaults=_DM_OFF,
        overrides={f"contact-{ROOM_KEY}": True},
        contact_type=3,
    ),
]


@pytest.mark.parametrize(
    ("case_name", "expected", "kwargs"),
    POLICY_CASES,
    ids=[row[0] for row in POLICY_CASES],
)
def test_conversation_is_enabled_vector(case_name: str, expected: bool, kwargs: dict) -> None:
    assert case_name
    assert DEFAULT_PUSH_DEFAULTS["new_dm"] is True
    assert conversation_is_enabled(**kwargs) is expected
