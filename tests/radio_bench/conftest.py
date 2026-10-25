"""Fixtures of the opt-in radio bench (docs/dev/radio-bench.md).

Every test here is skipped unless MESHLOOM_RADIO_BENCH_URL points at a running
instance. The session guard records the settings first and checks, at the very
end and whatever happened in between, that they are back.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from tests.radio_bench import bench

_CONFIG_ERROR: str | None = None
try:
    _CONFIG = bench.load_config()
except ValueError as exc:
    _CONFIG, _CONFIG_ERROR = None, str(exc)

# Filled by the session guard for the terminal summary.
_GUARD_RESULT: list[str] = []


def pytest_terminal_summary(terminalreporter) -> None:
    for line in _GUARD_RESULT:
        terminalreporter.write_line(line)


@pytest.fixture(scope="session")
def bench_config() -> bench.BenchConfig:
    if _CONFIG_ERROR:
        pytest.fail(_CONFIG_ERROR)
    if _CONFIG is None:
        pytest.skip(f"radio bench: set {bench.ENV_URL} to run it")
    if os.environ.get("PYTEST_XDIST_WORKER"):
        pytest.fail("radio bench drives one radio: run it with -n 0")
    return _CONFIG


@pytest.fixture(scope="session")
def api(bench_config: bench.BenchConfig) -> Iterator[httpx.Client]:
    with bench.api_client(bench_config) as client:
        yield client


@pytest.fixture(scope="session", autouse=True)
def settings_guard(bench_config: bench.BenchConfig, api: httpx.Client) -> Iterator[dict]:
    """Record the settings, then restore and compare them after the last test."""
    if not bench.ensure_radio_connected(api):
        pytest.fail("radio bench: the radio is not connected on the instance")
    before = bench.settings_snapshot(api)
    yield before
    reconnected = bench.ensure_radio_connected(api)
    bench.restore_radio_fields(api, before["radio"])
    after = bench.settings_snapshot(api)
    changed = bench.snapshot_diff(before, after)
    if not reconnected:
        changed.insert(0, "radio link (not reconnected)")
    if changed:
        _GUARD_RESULT.append(f"radio bench: settings differ after the run: {', '.join(changed)}")
        pytest.fail(f"settings not restored: {', '.join(changed)}")
    _GUARD_RESULT.append(
        "radio bench: settings identical before and after the run "
        f"({sum(len(v) if isinstance(v, dict | list) else 1 for v in before.values())} values)"
    )


@pytest.fixture(scope="session")
def run_id() -> str:
    return uuid.uuid4().hex[:6]


@pytest.fixture(scope="session")
def airtime() -> bench.AirTime:
    return bench.AirTime()


@pytest.fixture(scope="session")
def channel_key(api: httpx.Client, bench_config: bench.BenchConfig) -> str:
    try:
        return bench.find_channel_key(api, bench_config.channel)
    except LookupError as exc:
        pytest.fail(str(exc))


@pytest.fixture
async def aapi(bench_config: bench.BenchConfig) -> AsyncIterator[httpx.AsyncClient]:
    async with bench.async_api_client(bench_config) as client:
        yield client


@pytest.fixture
def proxy_endpoint(bench_config: bench.BenchConfig) -> tuple[str, int]:
    if bench_config.proxy_host is None or bench_config.proxy_port is None:
        pytest.skip(f"radio bench: set {bench.ENV_PROXY} to test the radio proxy")
    return bench_config.proxy_host, bench_config.proxy_port


@pytest.fixture
def radio_link(api: httpx.Client) -> Iterator[httpx.Client]:
    """For tests that drop the radio link: it is brought back even if they fail."""
    yield api
    assert bench.ensure_radio_connected(api), "radio link not restored"
