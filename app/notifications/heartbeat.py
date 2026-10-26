"""The heartbeat of a notification channel (MSHL-131): proof that Meshloom is still running.

``radio.offline`` only fires while Meshloom runs: a power cut, a host reboot or a stopped
container produce nothing. A channel with ``heartbeat_interval_s`` > 0 (0 = off, the default;
``HEARTBEAT_MIN_S``..``HEARTBEAT_MAX_S`` otherwise) gets a sign of life every interval, so a
service on the other side can alert on the silence.

Two modes, per channel:

- **Message** (no ``heartbeat_url``): a ``notify.heartbeat`` event (instance name, uptime,
  version) through the channel's own transport, so through the egress guard like any send.
  The first one leaves one interval after the loop starts.
- **Ping** (``heartbeat_url`` set, kept in the channel's encrypted secrets under
  ``HEARTBEAT_URL_KEY``): a plain GET or POST (``heartbeat_method``) to a healthchecks-style
  URL instead of a message, the first one as soon as the loop starts. The URL goes through
  ``validate_destination`` on save (``check_ping_url``) and again on every ping (DNS
  rebinding), the client is pinned to the validated addresses and never follows a redirect
  (a 3xx is ``redirect_refused``), the timeout is ``PING_TIMEOUT_S``, the body sent is empty
  and the answer's body is never read.

Both are sent outside the rules and the queue: no quiet hours, no global pause, no buckets,
no quota, no dedup, no retry (the next beat is the retry). A success is counted
(``notification_counters``), never journaled; a failure writes no
``notification_deliveries`` row either.

**The MSHL-130 auto-pause.** A message heartbeat goes to the channel's own destination, so
it shares its fate: a failure counts towards ``notify.channel_failed`` and the auto-pause
like any send, a success resets the failures (and lifts a pause), and no heartbeat is sent
while the auto-pause holds (``probe_tick`` owns the recovery). A ping goes to another
service: it runs whatever the channel's status, its failure is a coded warning in the log
only (the pinged service alerts on the silence), and it never pauses or resumes the channel.

**Loops.** One ``app.background_tasks.spawn`` task per channel (``notify-heartbeat:<id>``),
sleeping with the dispatcher's injected sleep. ``HeartbeatManager.reconcile`` (after a
channel is created, edited, deleted, resumed or restored, at startup, and every
``QUIET_TICK_S`` as a safety net) starts the missing loops and cancels the ones whose
channel is gone, disabled, unreadable or changed; ``drain_background_tasks`` cancels them
at shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from app.net.egress_guard import (
    EgressBlocked,
    pinned_async_client,
    tls_check_may_be_off,
    validate_destination,
)
from app.notifications.channels import (
    HEARTBEAT_URL_KEY,
    Channel,
    ChannelError,
    heartbeat_supported,
)
from app.notifications.transports.base import Delivery, SendResult, result_from_exception

if TYPE_CHECKING:
    from app.notifications.dispatcher import Dispatcher

logger = logging.getLogger(__name__)

PING_TIMEOUT_S = 10.0
FALLBACK_INSTANCE = "Meshloom"


async def check_ping_url(url: str, *, allow_private_network: bool, verify_tls: bool) -> None:
    """Refuse a ping URL the guard would refuse at send (``ChannelError`` with its code)."""
    try:
        dest = await validate_destination(url, allow_private_network=allow_private_network)
    except EgressBlocked as exc:
        raise ChannelError(exc.code) from None
    if not verify_tls and not tls_check_may_be_off(dest.ips):
        raise ChannelError("verify_tls_requires_private_destination")


async def ping(
    url: str,
    method: str,
    *,
    allow_private_network: bool,
    verify_tls: bool,
    timeout: float = PING_TIMEOUT_S,
) -> SendResult:
    """One GET or POST to ``url``, validated again now. Never raises, never reads the body."""
    verb = "POST" if method == "POST" else "GET"
    try:
        dest = await validate_destination(url, allow_private_network=allow_private_network)
        if not verify_tls and not tls_check_may_be_off(dest.ips):
            return SendResult("failed", error_code="verify_tls_requires_private_destination")
        async with (
            pinned_async_client(
                dest, verify_tls=verify_tls, timeout=httpx.Timeout(timeout)
            ) as client,
            client.stream(verb, content=b"" if verb == "POST" else None) as response,
        ):
            status = response.status_code
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        return result_from_exception(exc)
    if 200 <= status < 300:
        return SendResult("ok")
    if 300 <= status < 400:
        return SendResult("failed", error_code="redirect_refused")
    if status == 429:
        return SendResult("retry", error_code="rate_limited")
    if status >= 500:
        return SendResult("retry", error_code="service_error")
    return SendResult("failed", error_code=f"http_{status}")


def wants_heartbeat(channel: Channel) -> bool:
    """A loop runs for an enabled, readable channel of a supported type with an interval."""
    return (
        channel.enabled
        and not channel.secret_unreadable
        and channel.heartbeat_interval_s > 0
        and heartbeat_supported(channel.type, channel.preset)
    )


def _signature(channel: Channel) -> tuple[int, bool]:
    return channel.heartbeat_interval_s, HEARTBEAT_URL_KEY in channel.secret_fields_set


class HeartbeatManager:
    """The heartbeat loops of one dispatcher (one per channel)."""

    def __init__(self, dispatcher: Dispatcher) -> None:
        self._d = dispatcher
        self._started_at = dispatcher._clock()
        self._beats: dict[str, tuple[asyncio.Task[None], tuple[int, bool]]] = {}
        self._lock: asyncio.Lock | None = None
        # Off until the app starts the dispatcher: a test or a script that only builds one
        # never gets a loop.
        self._active = False

    @property
    def running(self) -> dict[str, tuple[int, bool]]:
        """``{channel id: (interval, ping mode)}`` of the live loops."""
        return {cid: sig for cid, (task, sig) in self._beats.items() if not task.done()}

    async def start(self) -> None:
        """Turn the loops on (``Dispatcher.start``) and start the ones the channels want."""
        self._active = True
        await self.reconcile()

    async def reconcile(self, restart: str | None = None) -> None:
        """Match the loops to the channels: start the missing ones, cancel the stale ones.
        ``restart`` (a channel just edited) is restarted even when its settings look the
        same, so a new URL starts from a fresh interval. Never raises."""
        from app.background_tasks import spawn
        from app.repository.notification_channels import NotificationChannelRepository

        if not self._active:
            return
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            try:
                channels = await NotificationChannelRepository.list_all()
            except Exception:
                logger.debug("heartbeat channels not read", exc_info=True)
                return
            wanted = {c.id: _signature(c) for c in channels if wants_heartbeat(c)}
            for cid, (task, sig) in list(self._beats.items()):
                if task.done() or wanted.get(cid) != sig or cid == restart:
                    task.cancel()
                    del self._beats[cid]
            for cid, sig in wanted.items():
                if cid not in self._beats:
                    task = spawn(self._loop(cid, *sig), name=f"notify-heartbeat:{cid}")
                    self._beats[cid] = (task, sig)

    async def stop(self) -> None:
        """Cancel every loop and wait for them to end; ``reconcile`` starts none after it."""
        self._active = False
        tasks = [task for task, _ in self._beats.values()]
        self._beats.clear()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _loop(self, channel_id: str, interval: int, ping_mode: bool) -> None:
        if not ping_mode:
            await self._d._sleep(interval)
        while True:
            try:
                await self.beat(channel_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("heartbeat of channel %s failed", channel_id)
            await self._d._sleep(interval)

    def payload(self) -> dict[str, object]:
        """The ``notify.heartbeat`` payload: instance name, uptime, version."""
        from app.notifications.render import HEARTBEAT_EVENT
        from app.notifications.webhook_v2 import radio_instance
        from app.version_info import get_app_build_info

        try:
            version = get_app_build_info().version or ""
        except Exception:
            version = ""
        return {
            "event": HEARTBEAT_EVENT,
            "instance": radio_instance().name or FALLBACK_INSTANCE,
            "uptime_s": max(0, int(self._d._clock() - self._started_at)),
            "version": version,
        }

    async def beat(self, channel_id: str) -> SendResult | None:
        """One heartbeat of ``channel_id``, read fresh. ``None`` when nothing was sent."""
        from app.notifications.events import event_from_payload
        from app.repository.notification_channels import NotificationChannelRepository

        loaded = await NotificationChannelRepository.get_for_send(channel_id)
        if loaded is None:
            return None
        channel, secrets = loaded
        if not wants_heartbeat(channel):
            return None
        url = secrets.get(HEARTBEAT_URL_KEY)
        if url:
            result = await ping(
                url,
                channel.heartbeat_method,
                allow_private_network=channel.allow_private_network,
                verify_tls=channel.verify_tls,
            )
            if result.ok:
                self._d.count_sent(channel.id)
            else:
                # The code only: the URL carries the check's token.
                logger.warning("heartbeat ping of channel %s: %s", channel.id, result.error_code)
            return result
        if channel.paused_until is not None and channel.paused_until > int(self._d._wall()):
            return None  # auto-paused (MSHL-130): ``probe_tick`` owns the recovery
        transport = self._d._transport_for(channel)
        if transport is None:
            return None
        payload = self.payload()
        delivery = Delivery(
            name="notify.heartbeat", payload=payload, event=event_from_payload(payload)
        )
        try:
            result = await transport.send(channel, None, delivery, secrets)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("heartbeat on %s raised %s", channel.type, type(exc).__name__)
            result = SendResult("failed", error_code="send_failed")
        if result.ok:
            await self._d._succeeded(channel)
        else:
            await self._d._record_failure(channel, result.error_code, secrets)
        return result
