"""`http.fetch {url, method?, headers?, body?, timeout?, verify_tls?}` (permission `http`).

The server makes the request for the child, through the egress guard's
fixed-host path (`follow_checked_redirects`, `app/net/AGENTS.md`). Checks in
order, all before any DNS lookup or connection:

- the params (`invalid_params`, `params.fields`): an `http(s)` URL without user
  info, `GET`/`POST`/`PUT`/`PATCH`/`DELETE`, at most `MAX_HEADERS` string
  headers, a text body of at most `MAX_BODY_BYTES` UTF-8 bytes (`params.limit`),
  a positive timeout (clamped to `MAX_TIMEOUT_SECONDS`);
- `verify_tls` (optional boolean, default true): false skips the certificate
  check, allowed only when the URL is a declared `lan_hosts` device (a
  self-signed device, MSHL-331; `invalid_params` otherwise) and applied only to
  the hops to such a device; a redirect to a public host is still checked;
- the host is one of the instance's effective `http.hosts` or `http.lan_hosts`
  (`host_not_allowed`): exact match, the declared port or else the scheme's
  default port only; `meshloom.app` and its subdomains only for a `meshloom.*`
  plugin;
- HTTPS (`https_required`) for a public host; a declared LAN host (MSHL-282)
  may also use plain HTTP;
- the quota, `PER_HOUR` requests per instance and clock hour, in memory
  (`quota_exceeded`, `params.period`, `params.limit`, `params.retry_after`).

Then the guard: pinned resolution, private and reserved addresses refused for a
public host (`host_not_allowed`), a LAN host that does not resolve to LAN
addresses only (10/8, 172.16/12, 192.168/16, 100.64/10, fc00::/7), or resolves to
an address of the Meshloom host itself, refused too (`host_not_allowed`, DNS
rebinding, MSHL-330), at most `MAX_REDIRECTS` redirects, each hop
re-validated against the same hosts (`host_not_allowed`, or `https_required` for
a hop to `http:` on a public host), credentials dropped when a hop leaves the origin, `Host`,
`Content-Length`, `Transfer-Encoding`, `Cookie` and the hop-by-hop headers
never sent, no cookie jar, an answer cut at `MAX_RESPONSE_BYTES` while it
streams (`truncated: true`). Any other failure is `fetch_failed` with
`params.reason` (`timeout`, `tls_error`, `connect_error`, an `egress_*` code).
An HTTP error status is an answer, not a refusal.

Answers `{status, headers, body, encoding?, truncated}`: `headers` is the
`RESPONSE_HEADERS` subset (each value cut at 1024 characters), `body` the UTF-8 text, or base64 with
`encoding: "base64"` when the bytes are not UTF-8 or base64 is shorter. URLs are
logged without user info, query or fragment (`meshloom_plugin.sdk.safe_url`).
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.net.egress_guard import (
    EgressBlocked,
    HostAllowlist,
    error_code,
    follow_checked_redirects,
)
from app.plugins.capabilities.base import Call, CapabilityError, capability, invalid_params
from app.plugins.permissions import is_meshloom_domain, lan_host_name
from meshloom_plugin.sdk import safe_url

logger = logging.getLogger(__name__)

METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
MAX_URL_LENGTH = 2048
MAX_HEADERS = 32
MAX_HEADER_BYTES = 4096
MAX_BODY_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TIMEOUT_SECONDS = 10.0
MAX_REDIRECTS = 3
PER_HOUR = 60
MAX_RESPONSE_HEADER_CHARS = 1024
USER_AGENT = "Meshloom-Plugin"
# What the child gets of the answer's headers: never `Set-Cookie` or anything
# that could carry the server's own state.
RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-language",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
        "link",
        "location",
        "ratelimit-limit",
        "ratelimit-remaining",
        "ratelimit-reset",
        "retry-after",
        "x-ratelimit-limit",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
    }
)
_HEADER_NAME = frozenset(
    "!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
_DEFAULT_PORTS = {"http": 80, "https": 443}
# Guard refusals that mean "not a host this plugin may reach".
_HOST_REFUSALS = frozenset(
    {
        "egress_host_not_allowed",
        "egress_private_address",
        "egress_lan_address",
        "egress_self_address",
        "egress_supervisor_address",
    }
)


class HourlyQuota:
    """`limit` requests per key and clock hour, in memory (a restart resets it)."""

    def __init__(self, limit: int, clock: Callable[[], float] = time.time):
        self.limit = limit
        self.clock = clock
        self._windows: dict[str, tuple[int, int]] = {}

    def take(self, key: str) -> int | None:
        """Count one request; None when it fits, else the seconds until the next hour."""
        now = self.clock()
        hour = int(now // 3600)
        start, count = self._windows.get(key, (hour, 0))
        if start != hour:
            count = 0
        if count >= self.limit:
            return max(1, (hour + 1) * 3600 - int(now))
        # Old windows go when their key is used again: one entry per instance.
        self._windows[key] = (hour, count + 1)
        return None


QUOTA = HourlyQuota(PER_HOUR)


def _allowlist(call: Call, key: str = "hosts") -> HostAllowlist:
    hosts = call.instance.permissions.get("http", {}).get(key, [])
    if not call.instance.plugin_id.startswith("meshloom."):
        hosts = [h for h in hosts if not is_meshloom_domain(lan_host_name(h))]
    return HostAllowlist.parse(hosts)


def _target(url: httpx.URL) -> tuple[str, str, int]:
    host = url.host.rstrip(".").lower()
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        host = url.raw_host.decode("ascii").rstrip(".").lower()
    scheme = url.scheme.lower()
    return scheme, host, url.port or _DEFAULT_PORTS[scheme]


def _url(params: dict[str, Any]) -> tuple[str, httpx.URL]:
    raw = params.get("url")
    if not isinstance(raw, str) or not raw or len(raw) > MAX_URL_LENGTH:
        raise invalid_params(
            f"url must be a string of at most {MAX_URL_LENGTH} characters", fields=["url"]
        )
    try:
        url = httpx.URL(raw)
    except Exception:
        raise invalid_params("url is not a valid URL", fields=["url"]) from None
    if url.scheme.lower() not in _DEFAULT_PORTS or not url.host:
        raise invalid_params("url must be an http or https URL", fields=["url"])
    if url.userinfo:
        raise invalid_params("credentials in the URL are not allowed", fields=["url"])
    return raw, url


def _method(params: dict[str, Any]) -> str:
    method = params.get("method", "GET")
    if not isinstance(method, str) or method.upper() not in METHODS:
        raise invalid_params("method must be GET, POST, PUT, PATCH or DELETE", fields=["method"])
    return method.upper()


def _headers(params: dict[str, Any]) -> dict[str, str]:
    headers = params.get("headers")
    if headers is None:
        return {}
    if not isinstance(headers, dict) or len(headers) > MAX_HEADERS:
        raise invalid_params(
            f"headers must be an object of at most {MAX_HEADERS} entries", fields=["headers"]
        )
    for name, value in headers.items():
        if (
            not name
            or not set(name) <= _HEADER_NAME
            or not isinstance(value, str)
            or len(name) + len(value.encode("utf-8")) > MAX_HEADER_BYTES
            or any(c in value for c in "\r\n\0")
        ):
            raise invalid_params("invalid header", fields=["headers"])
    return dict(headers)


def _body(params: dict[str, Any]) -> bytes | None:
    body = params.get("body")
    if body is None:
        return None
    if not isinstance(body, str):
        raise invalid_params("body must be a string", fields=["body"])
    data = body.encode("utf-8")
    if len(data) > MAX_BODY_BYTES:
        raise invalid_params("the body is too large", fields=["body"], limit=MAX_BODY_BYTES)
    return data


def _timeout(params: dict[str, Any]) -> float:
    value = params.get("timeout", MAX_TIMEOUT_SECONDS)
    # bool is an int: refused, like NaN.
    if type(value) not in (int, float) or value != value or value <= 0:
        raise invalid_params("timeout must be a positive number", fields=["timeout"])
    return min(float(value), MAX_TIMEOUT_SECONDS)


def _verify_tls(params: dict[str, Any]) -> bool:
    value = params.get("verify_tls", True)
    if type(value) is not bool:
        raise invalid_params("verify_tls must be a boolean", fields=["verify_tls"])
    return value


def _encode_body(content: bytes) -> dict[str, Any]:
    encoded = base64.b64encode(content).decode("ascii")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return {"body": encoded, "encoding": "base64"}
    # JSON escaping can make text far longer than base64 (control characters).
    if len(json.dumps(text, ensure_ascii=False).encode("utf-8")) > len(encoded) + 2:
        return {"body": encoded, "encoding": "base64"}
    return {"body": text}


def _refusal(code: str) -> CapabilityError:
    if code in _HOST_REFUSALS:
        return CapabilityError("host_not_allowed", "this host is not allowed for this plugin")
    if code == "egress_scheme":
        return CapabilityError("https_required", "only https URLs are allowed")
    return CapabilityError("fetch_failed", "the request failed", {"reason": code})


@capability("http.fetch", "http")
async def fetch(call: Call) -> dict[str, Any]:
    raw, url = _url(call.params)
    method = _method(call.params)
    headers = _headers(call.params)
    body = _body(call.params)
    timeout = _timeout(call.params)
    verify_tls = _verify_tls(call.params)
    shown = safe_url(raw)
    instance_id = call.instance.instance_id

    allowlist = _allowlist(call)
    lan_hosts = _allowlist(call, "lan_hosts")
    scheme, host, port = _target(url)
    on_lan = lan_hosts.allows(scheme, host, port)
    if not on_lan and not allowlist.allows(scheme, host, port):
        logger.info("Plugin %s http.fetch %s %s: host_not_allowed", instance_id, method, shown)
        raise CapabilityError("host_not_allowed", "this host is not allowed for this plugin")
    if not on_lan and scheme != "https":
        logger.info("Plugin %s http.fetch %s %s: https_required", instance_id, method, shown)
        raise CapabilityError("https_required", "only https URLs are allowed")
    if not on_lan and not verify_tls:
        raise invalid_params(
            "verify_tls may be false only for a declared lan_hosts device",
            fields=["verify_tls"],
        )
    retry_after = QUOTA.take(instance_id)
    if retry_after is not None:
        raise CapabilityError(
            "quota_exceeded",
            "too many requests this hour",
            {"period": "hour", "limit": QUOTA.limit, "retry_after": retry_after},
        )

    if not any(name.lower() == "user-agent" for name in headers):
        headers["User-Agent"] = USER_AGENT
    try:
        response = await follow_checked_redirects(
            method,
            raw,
            timeout=httpx.Timeout(timeout),
            total_timeout=timeout,
            allowed_hosts=allowlist,
            lan_hosts=lan_hosts,
            schemes=frozenset({"https"}),
            max_redirects=MAX_REDIRECTS,
            max_request_bytes=MAX_BODY_BYTES,
            max_response_bytes=MAX_RESPONSE_BYTES,
            truncate_response=True,
            content=body,
            headers=headers,
            lan_verify_tls=verify_tls,
        )
    except (EgressBlocked, httpx.HTTPError, TimeoutError) as exc:
        code = error_code(exc)
        logger.info("Plugin %s http.fetch %s %s: %s", instance_id, method, shown, code)
        raise _refusal(code) from None
    logger.info(
        "Plugin %s http.fetch %s %s: %d%s",
        instance_id,
        method,
        shown,
        response.status_code,
        " (truncated)" if response.truncated else "",
    )
    return {
        "status": response.status_code,
        "headers": {
            name: value[:MAX_RESPONSE_HEADER_CHARS]
            for name, value in ((k.lower(), v) for k, v in response.headers.items())
            if name in RESPONSE_HEADERS
        },
        **_encode_body(response.content),
        "truncated": response.truncated,
    }
