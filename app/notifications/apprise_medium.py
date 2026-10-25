"""Apprise as a notification medium: per-URL sends, retries and the minimal limiter.

Each URL gets its own Apprise object and its own result, so one dead service
neither hides nor repeats the others. A URL is re-checked by the egress guard
(``validate_apprise_urls``) right before each send. Retries follow
``RETRY_WAITS`` (0 s, 5 s, 30 s) on a network error, a 5xx or a 429 (its
``Retry-After`` wins), never on another 4xx. A URL with several targets is
tried once: a retry would resend to the targets already reached. While a URL
is checked or sent, its secret parts are registered with ``secret_scope``, so
Apprise's own DEBUG lines (request URLs) are masked by the redacting formatter.

Apprise plugins swallow the HTTP answer and return a bool, so the status is
read from ``requests`` itself: ``_install_response_capture`` wraps
``requests.Session.send`` once, and the wrapper records the status, the
``Retry-After`` header or the connection error only in a context that set a
record (``_capture``, a context variable set by ``_send_sync``; Apprise 2
runs the plugin in its own pool with ``submit_with_context``, which carries
it). Every other ``requests`` caller is untouched. A plugin that does not use ``requests``
(SMTP, MQTT) gives no status: a failure there is ``send_failed``, not retried.

The limiter in front of the medium (one send at a time, 2 s apart, "+N events",
the first-seen cap) is the dispatcher's worker for the channel
(``app/notifications/dispatcher.py``).
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

from tenacity import AsyncRetrying, RetryCallState, retry_if_exception_type, stop_after_attempt

from app.log_redaction import secret_scope
from app.net.apprise_urls import (
    AppriseUrlRefused,
    apprise_url_secret_parts,
    redact_apprise_url,
    validate_apprise_urls,
)
from app.net.egress_guard import tls_check_may_be_off
from app.notifications.executor import run_blocking

logger = logging.getLogger(__name__)

# Wait before each attempt: the first goes at once.
RETRY_WAITS = (0.0, 5.0, 30.0)
# A Retry-After longer than this is cut: the queue waits behind this send.
MAX_RETRY_AFTER_S = 120.0
CONNECT_TIMEOUT_S = 4.0
READ_TIMEOUT_S = 8.0


class AppriseSendFailed(Exception):
    """One URL failed for good. ``line`` is 1-based; ``url`` is redacted."""

    def __init__(self, code: str, *, line: int, url: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.line = line
        self.url = url
        self.status = status

    @property
    def delivery_code(self) -> str:
        """The code a delivery records and a test answers: a 401 or 403 is ``auth_failed``."""
        if self.code == "service_rejected" and self.status in (401, 403):
            return "auth_failed"
        return self.code

    def detail(self) -> dict[str, Any]:
        """A coded HTTP detail: line, scheme and host only, never a token."""
        params: dict[str, Any] = {"line": self.line, "context": "apprise", "url": self.url}
        if self.status is not None:
            params["status"] = self.status
        return {
            "code": self.code,
            "message": f"Apprise URL {self.line}: {_SEND_MESSAGES.get(self.code, self.code)}",
            "params": params,
        }


_SEND_MESSAGES = {
    "network_error": "The service could not be reached",
    "rate_limited": "The service asked to slow down (HTTP 429)",
    "service_error": "The service answered with a server error",
    "service_rejected": "The service refused the notification",
    "send_failed": "Apprise could not send the notification",
}


class _RetryableSend(Exception):
    def __init__(self, failure: AppriseSendFailed, retry_after: float | None = None) -> None:
        super().__init__(failure.code)
        self.failure = failure
        self.retry_after = retry_after


@dataclass
class _Response:
    status: int | None = None
    retry_after: str | None = None
    network_error: bool = False


_capture: contextvars.ContextVar[_Response | None] = contextvars.ContextVar(
    "apprise_response_capture", default=None
)
_capture_installed = False
_capture_lock = threading.Lock()


def _body_retry_after(response: Any) -> str | None:
    """Telegram answers a 429 with ``{"parameters": {"retry_after": N}}`` (seconds)."""
    if response.status_code != 429:
        return None
    try:
        seconds = response.json()["parameters"]["retry_after"]
    except (ValueError, KeyError, TypeError):
        return None
    return str(int(seconds)) if isinstance(seconds, int | float) and seconds >= 0 else None


def _install_response_capture() -> None:
    global _capture_installed
    with _capture_lock:
        if _capture_installed:
            return
        import requests

        original = requests.Session.send

        def send(self: requests.Session, request: Any, **kwargs: Any) -> Any:
            record = _capture.get()
            if record is None:
                return original(self, request, **kwargs)
            try:
                response = original(self, request, **kwargs)
            except requests.RequestException:
                record.network_error = True
                raise
            record.status = response.status_code
            record.retry_after = response.headers.get("Retry-After") or _body_retry_after(response)
            record.network_error = False
            return response

        requests.Session.send = send  # type: ignore[method-assign]
        _capture_installed = True


def _send_sync(server: Any, title: str, body: str, markdown: bool) -> tuple[bool, _Response]:
    """One Apprise object for one plugin instance; the last HTTP answer it got."""
    import apprise as apprise_lib
    from apprise import NotifyFormat

    _install_response_capture()
    record = _Response()
    token = _capture.set(record)
    try:
        notifier = apprise_lib.Apprise()
        notifier.add(server)
        body_format = NotifyFormat.MARKDOWN if markdown else NotifyFormat.TEXT
        ok = bool(notifier.notify(title=title, body=body, body_format=body_format))
    finally:
        _capture.reset(token)
    return ok, record


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, when.timestamp() - time.time())


def _check_outcome(ok: bool, response: _Response, *, line: int, url: str) -> None:
    if ok:
        return
    status = response.status
    if response.network_error:
        raise _RetryableSend(AppriseSendFailed("network_error", line=line, url=url))
    if status == 429:
        raise _RetryableSend(
            AppriseSendFailed("rate_limited", line=line, url=url, status=status),
            _parse_retry_after(response.retry_after),
        )
    if status is not None and status >= 500:
        raise _RetryableSend(AppriseSendFailed("service_error", line=line, url=url, status=status))
    if status is not None and status >= 400:
        raise AppriseSendFailed("service_rejected", line=line, url=url, status=status)
    raise AppriseSendFailed("send_failed", line=line, url=url, status=status)


def _retry_wait(state: RetryCallState) -> float:
    exc = state.outcome.exception() if state.outcome else None
    if isinstance(exc, _RetryableSend) and exc.retry_after is not None:
        return min(exc.retry_after, MAX_RETRY_AFTER_S)
    return RETRY_WAITS[min(state.attempt_number, len(RETRY_WAITS) - 1)]


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def _send_one_url(
    url: str,
    *,
    line: int,
    allow_private_network: bool,
    title: str,
    body: str,
    markdown: bool,
    verify_tls: bool = True,
) -> None:
    # Apprise logs request URLs at DEBUG: its tokens are masked by the
    # redacting formatter while this URL is in flight.
    with secret_scope(*apprise_url_secret_parts(url)):
        await _send_one_url_scoped(
            url,
            line=line,
            allow_private_network=allow_private_network,
            title=title,
            body=body,
            markdown=markdown,
            verify_tls=verify_tls,
        )


async def _send_one_url_scoped(
    url: str,
    *,
    line: int,
    allow_private_network: bool,
    title: str,
    body: str,
    markdown: bool,
    verify_tls: bool,
) -> None:
    redacted = redact_apprise_url(url)
    try:
        (validated,) = await validate_apprise_urls(
            [url], allow_private_network=allow_private_network
        )
    except AppriseUrlRefused as exc:
        raise AppriseUrlRefused(exc.code, line=line, scheme=exc.scheme) from None
    server = validated.server
    server.socket_connect_timeout = CONNECT_TIMEOUT_S
    server.socket_read_timeout = READ_TIMEOUT_S
    if not verify_tls:
        # Only for a user-chosen host whose every address is private: a fixed-host
        # service (Telegram, Discord...) or a public host keeps its certificate check.
        destination = validated.destination
        if destination is None or not tls_check_may_be_off(destination.ips):
            raise AppriseUrlRefused(
                "verify_tls_requires_private_destination", line=line, scheme=validated.scheme
            )
        server.verify_certificate = False
    if validated.scheme == "discord":
        # A webhook's avatar is fetched by Discord from an Apprise-hosted image.
        server.avatar = False
    # A retry resends to every target of the URL: with several (ntfy topics,
    # Telegram chats, e-mail recipients) the ones already reached would get
    # duplicates, so such a URL is tried once.
    attempts = len(RETRY_WAITS) if len(server) <= 1 else 1
    retrying = AsyncRetrying(
        stop=stop_after_attempt(attempts),
        wait=_retry_wait,
        retry=retry_if_exception_type(_RetryableSend),
        sleep=lambda seconds: _sleep(seconds),
        reraise=True,
    )
    try:
        async for attempt in retrying:
            with attempt:
                ok, response = await run_blocking(_send_sync, server, title, body, markdown)
                _check_outcome(ok, response, line=line, url=redacted)
    except _RetryableSend as exc:
        raise exc.failure from None


@dataclass(frozen=True)
class UrlResult:
    line: int
    url: str  # redacted
    error: AppriseUrlRefused | AppriseSendFailed | None = None


async def send_to_urls(
    urls: list[str],
    *,
    allow_private_network: bool,
    title: str,
    body: str,
    markdown: bool,
    verify_tls: bool = True,
) -> list[UrlResult]:
    """Send to every URL, each on its own; one result per URL, in order."""
    results: list[UrlResult] = []
    for line, url in enumerate(urls, start=1):
        try:
            await _send_one_url(
                url,
                line=line,
                allow_private_network=allow_private_network,
                title=title,
                body=body,
                markdown=markdown,
                verify_tls=verify_tls,
            )
        except (AppriseUrlRefused, AppriseSendFailed) as exc:
            results.append(UrlResult(line=line, url=redact_apprise_url(url), error=exc))
        else:
            results.append(UrlResult(line=line, url=redact_apprise_url(url)))
    return results
