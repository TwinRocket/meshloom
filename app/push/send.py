"""Thin wrapper around pywebpush for sending push notifications.

Isolates the pywebpush dependency and runs the synchronous send in the
bounded notification pool (``app/notifications/executor.py``), not the event
loop's default one, so a slow push service never starves other threads.

Every send goes through the egress guard first (``app/net/egress_guard.py``),
then connects only to the addresses it validated: the urllib3 connections
below never resolve the endpoint's host again (no DNS rebinding between the
check and the send). TLS still verifies the certificate against the host name.
The session ignores proxy variables (a proxy would resolve the host itself)
and follows no redirect.
"""

import ipaddress
import logging
import socket
from collections.abc import Sequence
from typing import Any, cast

import requests
import urllib3.connection
import urllib3.connectionpool
from pywebpush import webpush
from requests.adapters import HTTPAdapter
from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import ConnectTimeout as RequestsConnectTimeout
from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

from app.net.egress_guard import ResolvedDestination, validate_destination
from app.notifications.executor import run_blocking

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = object()
DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS = 3
IPV4_FALLBACK_CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_PUSH_READ_TIMEOUT_SECONDS = 10


def _create_pinned_connection(
    addresses: Sequence[str],
    port: int,
    timeout: float | None | object = DEFAULT_TIMEOUT,
    source_address: tuple[str, int] | None = None,
    socket_options=None,
) -> socket.socket:
    """Connect to the first reachable address of a fixed list, never resolving."""
    err: OSError | None = None
    for address in addresses:
        ip = ipaddress.ip_address(address)
        family = socket.AF_INET if ip.version == 4 else socket.AF_INET6
        sock = None
        try:
            sock = socket.socket(family, socket.SOCK_STREAM)
            if socket_options:
                for opt in socket_options:
                    sock.setsockopt(*opt)
            if timeout is not DEFAULT_TIMEOUT:
                sock.settimeout(cast(float | None, timeout))
            if source_address:
                sock.bind(source_address)
            sock.connect((address, port))
            return sock
        except OSError as exc:
            err = exc
            if sock is not None:
                sock.close()

    if err is not None:
        raise err
    raise OSError("no validated address to connect to")


def _pinned_new_conn(conn: Any) -> socket.socket:
    try:
        return _create_pinned_connection(
            conn.pinned_addresses,
            conn.port,
            conn.timeout,
            source_address=conn.source_address,
            socket_options=conn.socket_options,
        )
    except TimeoutError as exc:
        raise ConnectTimeoutError(
            conn,
            f"Connection to {conn.host} timed out. (connect timeout={conn.timeout})",
        ) from exc
    except OSError as exc:
        raise NewConnectionError(conn, f"Failed to establish a new connection: {exc}") from exc


class PinnedHTTPConnection(urllib3.connection.HTTPConnection):
    """urllib3 HTTP connection to validated addresses only."""

    pinned_addresses: tuple[str, ...] = ()

    def _new_conn(self) -> socket.socket:
        return _pinned_new_conn(self)


class PinnedHTTPSConnection(urllib3.connection.HTTPSConnection):
    """urllib3 HTTPS connection to validated addresses only (SNI and cert: the host name)."""

    pinned_addresses: tuple[str, ...] = ()

    def _new_conn(self) -> socket.socket:
        return _pinned_new_conn(self)


class PinnedHTTPAdapter(HTTPAdapter):
    """requests adapter whose connections reach only ``addresses``."""

    def __init__(self, addresses: Sequence[str]) -> None:
        pinned = {"pinned_addresses": tuple(addresses)}
        http_conn = type("PinnedHTTPConnection", (PinnedHTTPConnection,), pinned)
        https_conn = type("PinnedHTTPSConnection", (PinnedHTTPSConnection,), pinned)
        self._pool_classes = {
            "http": type(
                "PinnedHTTPConnectionPool",
                (urllib3.connectionpool.HTTPConnectionPool,),
                {"ConnectionCls": http_conn},
            ),
            "https": type(
                "PinnedHTTPSConnectionPool",
                (urllib3.connectionpool.HTTPSConnectionPool,),
                {"ConnectionCls": https_conn},
            ),
        }
        self.pinned_addresses = tuple(addresses)
        super().__init__()

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            **self.poolmanager.pool_classes_by_scheme,
            **self._pool_classes,
        }


def _build_pinned_session(addresses: Sequence[str]) -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.max_redirects = 0
    adapter = PinnedHTTPAdapter(addresses)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _send_push_with_session(
    *,
    subscription_info: dict,
    payload: str,
    vapid_private_key: str,
    vapid_claims: dict,
    session: requests.Session,
    connect_timeout_seconds: int,
) -> int:
    response = webpush(
        subscription_info=subscription_info,
        data=payload,
        vapid_private_key=vapid_private_key,
        # pywebpush mutates this dict (adds aud/exp); copy so retries stay clean.
        vapid_claims=dict(vapid_claims),
        content_encoding="aes128gcm",
        timeout=cast(Any, (connect_timeout_seconds, DEFAULT_PUSH_READ_TIMEOUT_SECONDS)),
        requests_session=session,
    )
    return response.status_code  # type: ignore[union-attr]


def _send_push_with_fallback(
    dest: ResolvedDestination,
    subscription_info: dict,
    payload: str,
    vapid_private_key: str,
    vapid_claims: dict,
) -> int:
    """Send to every validated address, then retry the IPv4 ones on connect failures."""
    addresses = [str(ip) for ip in dest.ips]
    session = _build_pinned_session(addresses)
    try:
        return _send_push_with_session(
            subscription_info=subscription_info,
            payload=payload,
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
            session=session,
            connect_timeout_seconds=DEFAULT_PUSH_CONNECT_TIMEOUT_SECONDS,
        )
    except (RequestsConnectTimeout, RequestsConnectionError) as exc:
        ipv4 = [a for a in addresses if ipaddress.ip_address(a).version == 4]
        if not ipv4:
            raise
        logger.info(
            "Push delivery retrying via IPv4 after initial network failure: %s",
            type(exc).__name__,
        )
    finally:
        session.close()

    session = _build_pinned_session(ipv4)
    try:
        return _send_push_with_session(
            subscription_info=subscription_info,
            payload=payload,
            vapid_private_key=vapid_private_key,
            vapid_claims=vapid_claims,
            session=session,
            connect_timeout_seconds=IPV4_FALLBACK_CONNECT_TIMEOUT_SECONDS,
        )
    finally:
        session.close()


async def send_push(
    subscription_info: dict,
    payload: str,
    vapid_private_key: str,
    vapid_claims: dict,
    *,
    allow_private_network: bool = False,
) -> int:
    """Send an encrypted push notification.

    Args:
        subscription_info: {"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}
        payload: JSON string to encrypt and send
        vapid_private_key: base64url-encoded raw EC private key scalar
        vapid_claims: {"sub": "mailto:..."} or {"sub": "https://..."}
        allow_private_network: the ``push_allow_private_endpoints`` setting

    Returns:
        HTTP status code from the push service.

    Raises:
        EgressBlocked: the guard refused the endpoint; nothing was sent.
        WebPushException: on push service error (caller handles 403/404/410 cleanup).
    """
    dest = await validate_destination(
        str(subscription_info.get("endpoint") or ""),
        allow_private_network=allow_private_network,
    )
    # The notification pool, not the loop's default one: a slow notification
    # service must never starve the radio or the database backups.
    return await run_blocking(
        _send_push_with_fallback, dest, subscription_info, payload, vapid_private_key, vapid_claims
    )
