"""Radio settings round trip: read, change, read back, restore."""

from collections.abc import Iterator

import httpx
import pytest

from tests.radio_bench import bench

pytestmark = pytest.mark.radio


@pytest.fixture
def original_radio_config(api: httpx.Client) -> Iterator[dict]:
    """The radio config before the test, put back afterwards even on failure."""
    original = bench.radio_config(api)
    yield original
    bench.restore_radio_fields(api, original)
    restored = bench.radio_config(api)
    assert {f: restored[f] for f in bench.MUTABLE_RADIO_FIELDS} == {
        f: original[f] for f in bench.MUTABLE_RADIO_FIELDS
    }


def test_radio_settings_round_trip(api: httpx.Client, original_radio_config: dict) -> None:
    changed = {
        "multi_acks_enabled": not original_radio_config["multi_acks_enabled"],
        # 0 deny, 1 by contact flags, 2 allow all: any other value works.
        "telemetry_mode_env": 1 if original_radio_config["telemetry_mode_env"] != 1 else 0,
    }

    response = api.patch("/radio/config", json=changed)
    response.raise_for_status()
    assert {k: response.json()[k] for k in changed} == changed

    # A fresh read comes from the radio's self info, not from the PATCH answer.
    read_back = bench.radio_config(api)
    assert {k: read_back[k] for k in changed} == changed
    assert bench.radio_config_diff(original_radio_config, read_back) == sorted(changed)

    restore = {k: original_radio_config[k] for k in changed}
    api.patch("/radio/config", json=restore).raise_for_status()
    assert bench.radio_config_diff(original_radio_config, bench.radio_config(api)) == []
