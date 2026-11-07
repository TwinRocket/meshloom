"""Helpers for the opt-in radio bench (docs/dev/radio-bench.md).

The bench drives a running Meshloom instance over its REST API and WebSocket, and
its radio proxy with the pinned `meshcore` client. It never imports the app's
runtime: the only app module it uses is the pure packet hash helper.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import websockets

ENV_URL = "MESHLOOM_RADIO_BENCH_URL"
ENV_PROXY = "MESHLOOM_RADIO_BENCH_PROXY"
ENV_CHANNEL = "MESHLOOM_RADIO_BENCH_CHANNEL"
DEFAULT_CHANNEL = "#meshloom-next-test"

# The radio settings a bench test may change. The session guard restores any of
# them that differ at the end, then compares the whole snapshot.
MUTABLE_RADIO_FIELDS = (
    "multi_acks_enabled",
    "telemetry_mode_base",
    "telemetry_mode_loc",
    "telemetry_mode_env",
)

# Routes the bench must never call: identity, factory reset, backups (they carry
# the private key) and transport writes. Enforced on every request it sends.
_FORBIDDEN_PREFIXES = (
    "/api/radio/private-key",
    "/api/radio/factory-reset",
    "/api/radio/identity",
    "/api/settings/backup",
    "/api/radio/reboot",
)
# Writes that can cut the radio's network link (Wi-Fi credentials, enable, reboot),
# change how the bench reaches it (transport, proxy) or its custom variables.
_FORBIDDEN_WRITES = (
    "/api/radio/transport",
    "/api/radio/proxy",
    "/api/radio/custom-vars",
    "/api/radio/wifi",
)

# Volatile fields left out of the before/after comparison.
_VOLATILE_APP_SETTINGS = {"last_message_times", "last_advert_time"}
_PROXY_SETTING_FIELDS = ("enabled", "bind", "port", "max_clients")
_TRANSPORT_FIELDS = (
    "transport",
    "configured",
    "serial_port",
    "serial_baudrate",
    "tcp_host",
    "tcp_port",
    "ble_address",
    "ble_pin_configured",
)
_CHANNEL_FIELDS = (
    "key",
    "name",
    "is_hashtag",
    "membership",
    "flood_scope_override",
    "path_hash_mode_override",
)

HEALTH_TIMEOUT = 120.0
ECHO_TIMEOUT = 45.0
# A flood message sent while the repeaters still relay the previous one often
# collides with it and is never repeated: sends are spaced, and a send nobody
# repeated is tried again with a new text, up to this many times.
SEND_SPACING = 20.0
ECHO_ATTEMPTS = 3
# The instance and the machine running the bench are both expected on NTP.
MAX_CLOCK_SKEW = 120
# With its GPS on (custom var `gps`), the radio refreshes its own position: a
# fix moves lat/lon by a few metres between two reads. About 110 m of latitude.
GPS_JITTER_DEG = 0.001
_GPS_FIELDS = {"lat", "lon"}


@dataclass(frozen=True)
class BenchConfig:
    base_url: str
    channel: str
    proxy_host: str | None = None
    proxy_port: int | None = None

    @property
    def ws_url(self) -> str:
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        return f"{scheme}://{parts.netloc}{parts.path}/api/ws"


def load_config() -> BenchConfig | None:
    """Read the bench target from the environment; None leaves the bench skipped."""
    base_url = os.environ.get(ENV_URL, "").strip().rstrip("/")
    if not base_url:
        return None
    if base_url.endswith("/api"):
        base_url = base_url[: -len("/api")]
    channel = os.environ.get(ENV_CHANNEL, "").strip() or DEFAULT_CHANNEL
    proxy = os.environ.get(ENV_PROXY, "").strip()
    if not proxy:
        return BenchConfig(base_url=base_url, channel=channel)
    host, sep, port = proxy.rpartition(":")
    if not sep or not host or not port.isdigit():
        raise ValueError(f"{ENV_PROXY} must be host:port, got {proxy!r}")
    return BenchConfig(base_url=base_url, channel=channel, proxy_host=host, proxy_port=int(port))


def _refuse_forbidden(request: httpx.Request) -> None:
    path = request.url.path
    if path.startswith(_FORBIDDEN_PREFIXES) or (
        request.method != "GET" and path.startswith(_FORBIDDEN_WRITES)
    ):
        raise RuntimeError(f"radio bench refuses {request.method} {path}")
    if request.method == "PATCH" and path == "/api/radio/config":
        # The guard restores only MUTABLE_RADIO_FIELDS: any other key (frequency,
        # tx power, name...) would be reported but never put back.
        try:
            body = json.loads(request.content or b"null")
        except ValueError:
            body = None
        if not isinstance(body, dict) or not set(body) <= set(MUTABLE_RADIO_FIELDS):
            raise RuntimeError(
                f"radio bench refuses {request.method} {path} outside MUTABLE_RADIO_FIELDS"
            )


async def _refuse_forbidden_async(request: httpx.Request) -> None:
    _refuse_forbidden(request)


def api_client(config: BenchConfig, transport: httpx.BaseTransport | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=f"{config.base_url}/api",
        timeout=30.0,
        event_hooks={"request": [_refuse_forbidden]},
        transport=transport,
    )


def async_api_client(config: BenchConfig) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=f"{config.base_url}/api",
        timeout=30.0,
        event_hooks={"request": [_refuse_forbidden_async]},
    )


def radio_config(client: httpx.Client) -> dict[str, Any]:
    """GET /radio/config without the public key (the bench never looks at the identity)."""
    response = client.get("/radio/config")
    response.raise_for_status()
    data = response.json()
    data.pop("public_key", None)
    data.pop("warnings", None)
    return data


def settings_snapshot(client: httpx.Client) -> dict[str, Any]:
    """Everything the bench could plausibly change, read before and after a run."""
    app_settings = client.get("/settings")
    app_settings.raise_for_status()
    proxy = client.get("/radio/proxy")
    proxy.raise_for_status()
    transport = client.get("/radio/transport")
    transport.raise_for_status()
    channels = client.get("/channels")
    channels.raise_for_status()
    return {
        "radio": radio_config(client),
        "app": {k: v for k, v in app_settings.json().items() if k not in _VOLATILE_APP_SETTINGS},
        "proxy": {k: proxy.json().get(k) for k in _PROXY_SETTING_FIELDS},
        "transport": {k: transport.json().get(k) for k in _TRANSPORT_FIELDS},
        "channels": sorted(
            ({k: ch.get(k) for k in _CHANNEL_FIELDS} for ch in channels.json()),
            key=lambda ch: ch["key"],
        ),
    }


def radio_config_diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Radio config fields that changed, GPS jitter on the position excepted."""
    changed = []
    for key in sorted(set(before) | set(after)):
        left, right = before.get(key), after.get(key)
        if (
            key in _GPS_FIELDS
            and isinstance(left, float)
            and isinstance(right, float)
            and abs(left - right) <= GPS_JITTER_DEG
        ):
            continue
        if left != right:
            changed.append(key)
    return changed


def snapshot_diff(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Dotted paths whose value changed (values are not shown: settings may hold secrets)."""
    changed: list[str] = []
    for section in sorted(set(before) | set(after)):
        left, right = before.get(section), after.get(section)
        if section == "radio" and isinstance(left, dict) and isinstance(right, dict):
            changed.extend(f"radio.{key}" for key in radio_config_diff(left, right))
        elif isinstance(left, dict) and isinstance(right, dict):
            changed.extend(
                f"{section}.{key}"
                for key in sorted(set(left) | set(right))
                if left.get(key) != right.get(key)
            )
        elif left != right:
            changed.append(section)
    return changed


def restore_radio_fields(client: httpx.Client, original: dict[str, Any]) -> None:
    """PATCH back every mutable radio field that differs from ``original``."""
    current = radio_config(client)
    update = {
        field: original[field]
        for field in MUTABLE_RADIO_FIELDS
        if field in original and current.get(field) != original[field]
    }
    if update:
        client.patch("/radio/config", json=update).raise_for_status()


def health(client: httpx.Client) -> dict[str, Any]:
    response = client.get("/health")
    response.raise_for_status()
    return response.json()


def radio_ready(data: dict[str, Any]) -> bool:
    return (
        bool(data.get("radio_connected"))
        and not data.get("radio_initializing")
        and data.get("radio_state") == "connected"
    )


def wait_until(predicate: Callable[[], Any], timeout: float, interval: float = 1.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if result:
            return result
        if time.monotonic() >= deadline:
            return result
        time.sleep(interval)


async def async_wait_until(
    predicate: Callable[[], Any], timeout: float, interval: float = 1.0
) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        result = predicate()
        if asyncio.iscoroutine(result):
            result = await result
        if result:
            return result
        if time.monotonic() >= deadline:
            return result
        await asyncio.sleep(interval)


def ensure_radio_connected(client: httpx.Client, timeout: float = HEALTH_TIMEOUT) -> bool:
    """Bring the radio link back if a test left it down; True once it is ready."""

    def attempt() -> bool:
        if radio_ready(health(client)):
            return True
        client.post("/radio/reconnect")
        return radio_ready(health(client))

    return bool(wait_until(attempt, timeout, interval=5.0))


def find_channel_key(client: httpx.Client, name: str) -> str:
    response = client.get("/channels")
    response.raise_for_status()
    for channel in response.json():
        if channel["name"] == name and channel.get("membership") != "pending":
            return channel["key"]
    raise LookupError(f"channel {name!r} is not adopted on the bench instance")


async def channel_rows(client: httpx.AsyncClient, key: str, token: str) -> list[dict[str, Any]]:
    """Rows of the bench channel whose text carries ``token``."""
    response = await client.get(
        "/messages", params={"type": "CHAN", "conversation_key": key, "limit": 100}
    )
    response.raise_for_status()
    return [row for row in response.json() if token in row["text"]]


class AirTime:
    """Spaces the bench's own transmissions (one per session)."""

    def __init__(self) -> None:
        self._last_send = 0.0

    async def wait_turn(self) -> None:
        delay = self._last_send + SEND_SPACING - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        self._last_send = time.monotonic()


class WsRecorder:
    """Collects every WebSocket frame of one connection in the background."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._changed = asyncio.Event()

    def _record(self, raw: str | bytes) -> None:
        try:
            self.events.append(json.loads(raw))
        except ValueError:
            return
        self._changed.set()

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e.get("data") or {} for e in self.events if e.get("type") == event_type]

    async def wait_for(
        self, predicate: Callable[[dict[str, Any]], bool], timeout: float
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while True:
            for event in self.events:
                if predicate(event):
                    return event
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), remaining)


@contextlib.asynccontextmanager
async def ws_recorder(config: BenchConfig) -> AsyncIterator[WsRecorder]:
    recorder = WsRecorder()
    async with websockets.connect(config.ws_url, open_timeout=15) as ws:

        async def pump() -> None:
            with contextlib.suppress(websockets.ConnectionClosed):
                async for raw in ws:
                    recorder._record(raw)

        task = asyncio.create_task(pump())
        try:
            yield recorder
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
