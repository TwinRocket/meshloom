"""Position this radio publishes to Community (observer_position)."""

import math

import pytest

from app.services.observer_position import advert_location_broadcast, publishable_position


class TestAdvertLocationBroadcast:
    @pytest.mark.parametrize("policy", [1, 2, 7])
    def test_non_zero_integer_broadcasts(self, policy):
        assert advert_location_broadcast({"adv_loc_policy": policy}) is True

    @pytest.mark.parametrize("policy", [0, None, "x", "1", True, False, 1.0])
    def test_anything_else_is_off(self, policy):
        assert advert_location_broadcast({"adv_loc_policy": policy}) is False

    def test_absent_is_off(self):
        assert advert_location_broadcast({}) is False

    @pytest.mark.parametrize("info", [None, [], "x"])
    def test_no_self_info_is_off(self, info):
        assert advert_location_broadcast(info) is False


def _info(lat=45.764043, lon=4.835659, policy=0, **extra):
    return {"adv_lat": lat, "adv_lon": lon, "adv_loc_policy": policy, **extra}


class TestPublishablePosition:
    def test_broadcast_position_is_exact_to_six_decimals(self):
        assert publishable_position(_info(45.76404321, 4.83565987, policy=1)) == (
            45.764043,
            4.83566,
            "exact",
        )

    def test_position_not_broadcast_is_rounded_to_two_decimals(self):
        position = publishable_position(_info(policy=0))
        assert position == (45.76, 4.84, "rounded")
        assert position is not None and position.precision == "rounded"

    @pytest.mark.parametrize("policy", [None, "x", True])
    def test_untrusted_policy_rounds(self, policy):
        assert publishable_position(_info(policy=policy)) == (45.76, 4.84, "rounded")

    def test_missing_policy_rounds(self):
        info = _info()
        del info["adv_loc_policy"]
        assert publishable_position(info) == (45.76, 4.84, "rounded")

    @pytest.mark.parametrize(
        ("lat", "lon"),
        [
            (None, 4.8),
            (45.7, None),
            (0.0, 0.0),
            (0.001, 0.002),  # rounds onto (0, 0)
            (91.0, 4.8),
            (45.7, -181.0),
            (math.nan, 4.8),
            (45.7, math.inf),
            ("45.7", "4.8"),
            (True, 4.8),
        ],
    )
    def test_no_usable_position(self, lat, lon):
        assert publishable_position(_info(lat, lon)) is None

    def test_tiny_broadcast_position_near_null_island_is_kept_exact(self):
        assert publishable_position(_info(0.001, 0.002, policy=1)) == (0.001, 0.002, "exact")

    def test_missing_fields_or_no_info(self):
        assert publishable_position({}) is None
        assert publishable_position(None) is None
