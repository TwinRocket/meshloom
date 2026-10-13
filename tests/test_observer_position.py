"""Position this radio publishes to Community (observer_position)."""

import pytest

from app.services.observer_position import advert_location_broadcast


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
