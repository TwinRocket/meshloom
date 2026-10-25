"""Apprise URLs through the egress guard: scheme allowlist, host check, redaction.

The host an Apprise URL reaches is not ``urlsplit().hostname``: ``tgram://TOKEN/CHAT``
has a token where the host would be, ``mailto://user:pass@gmail.com`` reaches
``smtp.gmail.com``. So each URL is instantiated with Apprise and the plugin is
read: a ``user_host`` plugin connects to a host the user typed
(``server.host``, or ``smtp_host`` for e-mail), which goes through
``validate_destination``; a ``fixed_host`` plugin only talks to its service's
own API. Every other plugin is refused (``egress_scheme``), whatever the
private-network opt-in: desktop and local ones (``dbus``, ``windows``,
``syslog``...), cloud-credential ones (``sns``, ``ses``) and the ones nobody
reviewed yet. ``tests/test_apprise_urls.py`` fails when an Apprise bump adds a
plugin that is in none of the three lists.

Apprise resolves and connects on its own (requests, smtplib, paho): the IP is
not pinned. The check runs at save and again right before each send, and
redirects are turned off on every plugin. Residual risk: DNS rebinding of a
host the user typed. See ``app/net/AGENTS.md``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from ipaddress import ip_address
from typing import Any, Literal
from urllib.parse import urlsplit

from app.net.egress_guard import (
    EgressBlocked,
    ResolvedDestination,
    egress_detail,
    validate_destination,
)

UrlClass = Literal["user_host", "fixed_host"]

# Plugins that connect to a host taken from the URL.
_USER_HOST = (
    "json jsons xml xmls form forms ntfy ntfys gotify gotifys hassio hassios "
    "matrix matrixs mailto mailtos mqtt mqtts signal signals rocket rockets "
    "mmost mmosts nctalk nctalks ncloud nclouds emby embys apprise apprises"
)
# Plugins that only reach their own service's API (fixed host names).
_FIXED_HOST = (
    "tgram discord pover slack pbul freemobile sfr gchat webex wxteams "
    "psafer psafers prowl ifttt join opsgenie"
)

ALLOWED_SCHEMES: dict[str, UrlClass] = {
    **dict.fromkeys(_USER_HOST.split(), "user_host"),
    **dict.fromkeys(_FIXED_HOST.split(), "fixed_host"),
}

# Every other plugin Apprise 2.0.1 ships, refused. Listed so that a plugin a
# new Apprise version adds is noticed (the test) instead of silently refused or
# allowed: classify it here, in one of the three lists.
REFUSED_SCHEMES = frozenset(
    [
        "46elks",
        "aprs",
        "atalk",
        "azure",
        "bark",
        "barks",
        "blink1",
        "bluesky",
        "brevo",
        "bsky",
        "bulksms",
        "bulkvs",
        "burstsms",
        "chanify",
        "chime",
        "clickatell",
        "clicksend",
        "d7sms",
        "dapnet",
        "dbus",
        "deltachat",
        "deltachats",
        "dingtalk",
        "dot",
        "eight00com",
        "elks",
        "enigma2",
        "enigma2s",
        "evolution",
        "evolutions",
        "exotel",
        "fcm",
        "feishu",
        "flock",
        "flowtriq",
        "flowtriqs",
        "fluxer",
        "fluxers",
        "gio",
        "glib",
        "gnome",
        "groupme",
        "growl",
        "guilded",
        "httpsms",
        "humhub",
        "humhubs",
        "irc",
        "ircs",
        "jellyfin",
        "jellyfins",
        "jira",
        "kavenegar",
        "kde",
        "kodi",
        "kodis",
        "kook",
        "kumulos",
        "lametric",
        "lametrics",
        "lark",
        "lauther",
        "line",
        "macosx",
        "mailersend",
        "mailgun",
        "mastodon",
        "mastodons",
        "misskey",
        "misskeys",
        "mobilemessage",
        "mobilemsg",
        "msg91",
        "msgbird",
        "nexmo",
        "notica",
        "noticas",
        "notifiarr",
        "notifico",
        "notificos",
        "notifyre",
        "o365",
        "octopush",
        "onesignal",
        "pagerduty",
        "pagertree",
        "parsep",
        "parseps",
        "pinglet",
        "pinglets",
        "pingram",
        "pjet",
        "pjets",
        "plivo",
        "postmark",
        "push",
        "pushdeer",
        "pushdeers",
        "pushed",
        "pushme",
        "pushplus",
        "pushward",
        "pushy",
        "qq",
        "qt",
        "reddit",
        "resend",
        "revolt",
        "ringc",
        "rsyslog",
        "ryver",
        "schan",
        "sendgrid",
        "sendpulse",
        "serwersms",
        "ses",
        "session",
        "sessions",
        "seven",
        "signalgrid",
        "signl4",
        "simplepush",
        "sinch",
        "smpp",
        "smpps",
        "smsc",
        "smseagle",
        "smseagles",
        "smsmanager",
        "smsmgr",
        "smtp2go",
        "sns",
        "sogs",
        "sparkpost",
        "spike",
        "splunk",
        "spugpush",
        "spush",
        "stackfield",
        "strmlabs",
        "synology",
        "synologys",
        "syslog",
        "telnyx",
        "threema",
        "toot",
        "toots",
        "trigv",
        "trigvs",
        "tweet",
        "twilio",
        "twist",
        "twitter",
        "vapid",
        "viber",
        "victorops",
        "voipms",
        "vonage",
        "wechat",
        "wecom",
        "wecombot",
        "whatsapp",
        "windows",
        "workflow",
        "workflows",
        "wpush",
        "wxpusher",
        "x",
        "xbmc",
        "xbmcs",
        "xmpp",
        "xmpps",
        "youlmk",
        "zoom",
        "zulip",
    ]
)

# Native URLs (https://discord.com/api/webhooks/...) are mapped to a plugin by Apprise.
_NATIVE_SCHEMES = frozenset({"http", "https"})

_SCHEME_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.-]*)://")

APPRISE_MESSAGES: dict[str, str] = {
    "egress_scheme": "Meshloom does not send to this kind of Apprise URL",
    "apprise_url_invalid": "Apprise does not understand this URL",
    "apprise_chat_id_required": "A Telegram URL needs a chat id: tgram://bot_token/chat_id",
}


class AppriseUrlRefused(Exception):
    """An Apprise URL refused before any connection. ``line`` is 1-based."""

    def __init__(self, code: str, *, line: int, scheme: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.line = line
        self.scheme = scheme

    def detail(self) -> dict[str, Any]:
        """A coded HTTP detail: the line and the scheme, never a host or a token."""
        params: dict[str, Any] = {"line": self.line, "context": "apprise", "scheme": self.scheme}
        message = APPRISE_MESSAGES.get(self.code) or egress_detail(self.code)["message"]
        return {
            "code": self.code,
            "message": f"Apprise URL {self.line}: {message}",
            "params": params,
        }


@dataclass(frozen=True)
class ValidatedUrl:
    """One Apprise URL, instantiated, with redirects off and its host checked."""

    url: str
    scheme: str
    url_class: UrlClass
    server: Any  # apprise.plugins.base.NotifyBase
    destination: ResolvedDestination | None  # None: fixed host, nothing to resolve


def split_apprise_urls(raw: str) -> list[str]:
    """One URL per non-empty line, as the Apprise editor stores them."""
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _scheme_of(url: str) -> str:
    match = _SCHEME_RE.match(url)
    return match.group(1).lower() if match else ""


def _plugin_schemes(server: Any) -> set[str]:
    schemes: set[str] = set()
    for attr in ("protocol", "secure_protocol"):
        value = getattr(type(server), attr, None)
        if isinstance(value, str):
            schemes.add(value.lower())
        elif isinstance(value, (tuple, list)):
            schemes.update(str(v).lower() for v in value)
    return schemes


def _plugin_class(server: Any) -> UrlClass | None:
    """The class of a plugin: every scheme it registers must be allowed, with one class."""
    classes: set[UrlClass | None] = {ALLOWED_SCHEMES.get(s) for s in _plugin_schemes(server)}
    if len(classes) != 1:
        return None
    return classes.pop()


def _user_host_target(server: Any) -> tuple[str, int, bool] | None:
    """The host, port and TLS flag a ``user_host`` plugin connects to, or None
    when the plugin runs in a mode that only reaches its fixed cloud service."""
    from apprise.plugins.matrix import NotifyMatrix
    from apprise.plugins.ntfy import NotifyNtfy, NtfyMode

    if isinstance(server, NotifyNtfy) and server.mode == NtfyMode.CLOUD:
        return None  # ntfy.sh
    if isinstance(server, NotifyMatrix) and server.mode == "t2bot":
        return None  # webhooks.t2bot.io
    host = getattr(server, "smtp_host", "") or server.host  # e-mail: the SMTP server
    secure = bool(getattr(server, "secure", False))
    port = server.port if isinstance(server.port, int) else (443 if secure else 80)
    return str(host or ""), port, secure


def _disable_secondary_hosts(server: Any) -> None:
    """Turn off the lookups that would reach a host other than the checked one.

    Matrix server discovery (``discovery=yes``, the default for ``matrixs``)
    reads ``https://<host>/.well-known/matrix/client`` and then sends every
    request, the access token included, to the ``base_url`` it returns: any
    address. E-mail ``wkd=yes`` fetches the PGP key from
    ``openpgpkey.<recipient domain>``, a host nobody checked. Set after
    parsing, so the URL cannot turn them back on.
    """
    from apprise.plugins.email import NotifyEmail
    from apprise.plugins.matrix import NotifyMatrix

    if isinstance(server, NotifyMatrix):
        server.discovery = False
        server.store.clear(server.discovery_base_key, server.discovery_identity_key)
    if isinstance(server, NotifyEmail):
        server.use_wkd = False
        pgp = getattr(server, "pgp", None)
        if pgp is not None:
            pgp.wkd = None


def parse_apprise_urls(urls: list[str]) -> list[ValidatedUrl]:
    """Allowlist, instantiate, turn redirects off, refuse a Telegram URL with no chat id.

    No DNS lookup: ``validate_apprise_urls`` adds the host check. Raises
    ``AppriseUrlRefused``. The URLs' secret parts are registered with
    ``secret_scope`` meanwhile: Apprise may log a URL at DEBUG while parsing.
    """
    from app.log_redaction import secret_scope

    with secret_scope(*(part for url in urls for part in apprise_url_secret_parts(url))):
        return _parse_apprise_urls(urls)


def _parse_apprise_urls(urls: list[str]) -> list[ValidatedUrl]:
    """``parse_apprise_urls`` without the secret scope."""
    import apprise as apprise_lib
    from apprise.plugins.telegram import NotifyTelegram

    asset = apprise_lib.AppriseAsset()
    asset.http_redirects = False
    parsed: list[ValidatedUrl] = []
    for line, url in enumerate(urls, start=1):
        scheme = _scheme_of(url)
        safe_scheme = scheme if len(scheme) <= 32 else ""
        if scheme not in ALLOWED_SCHEMES and scheme not in _NATIVE_SCHEMES:
            # Refused before Apprise sees it: instantiating some plugins
            # (dbus, glib, windows) already touches the local machine.
            raise AppriseUrlRefused("egress_scheme", line=line, scheme=safe_scheme)
        try:
            server = apprise_lib.Apprise.instantiate(url, asset=asset)
        except Exception:
            server = None
        if server is None:
            raise AppriseUrlRefused("apprise_url_invalid", line=line, scheme=safe_scheme)
        url_class = _plugin_class(server)
        if url_class is None or (
            scheme in ALLOWED_SCHEMES and ALLOWED_SCHEMES[scheme] != url_class
        ):
            raise AppriseUrlRefused("egress_scheme", line=line, scheme=safe_scheme)
        # A URL's ?redirect=yes must not win: set after parsing, on every plugin.
        server.redirects = False
        _disable_secondary_hosts(server)
        if isinstance(server, NotifyTelegram) and not server.targets:
            # Apprise would call getUpdates and message whoever wrote to the bot last.
            raise AppriseUrlRefused("apprise_chat_id_required", line=line, scheme=safe_scheme)
        parsed.append(
            ValidatedUrl(
                url=url, scheme=scheme, url_class=url_class, server=server, destination=None
            )
        )
    return parsed


async def validate_apprise_urls(
    urls: list[str], *, allow_private_network: bool
) -> list[ValidatedUrl]:
    """``parse_apprise_urls`` plus ``validate_destination`` on every user-chosen host.

    Raises ``AppriseUrlRefused`` (``egress_*`` codes included) before any connection.
    """
    validated: list[ValidatedUrl] = []
    for line, item in enumerate(parse_apprise_urls(urls), start=1):
        if item.url_class == "fixed_host":
            validated.append(item)
            continue
        target = _user_host_target(item.server)
        if target is None:
            validated.append(item)
            continue
        host, port, secure = target
        if not host:
            raise AppriseUrlRefused("apprise_url_invalid", line=line, scheme=item.scheme)
        bracketed = f"[{host}]" if ":" in host else host
        probe = f"{'https' if secure else 'http'}://{bracketed}:{port}/"
        try:
            dest = await validate_destination(probe, allow_private_network=allow_private_network)
        except EgressBlocked as exc:
            raise AppriseUrlRefused(exc.code, line=line, scheme=item.scheme) from None
        validated.append(
            ValidatedUrl(
                url=item.url,
                scheme=item.scheme,
                url_class=item.url_class,
                server=item.server,
                destination=dest,
            )
        )
    return validated


# A host name ``redact_url`` may show: letters, digits, dots and hyphens.
_SHOWABLE_HOST_RE = re.compile(r"^[a-z0-9.-]+$")


def _showable_host(host: str) -> bool:
    """A DNS name or an IP literal: ``urlsplit().hostname`` keeps other
    characters (``host;token=abc``), which may be part of a token."""
    if _SHOWABLE_HOST_RE.match(host):
        return True
    try:
        ip_address(host)
    except ValueError:
        return False
    return True


def redact_url(url: str) -> str:
    """Scheme and host only of a URL that names its host: ``https://****@host:8080/****``.

    The user, password, path, query and fragment may hold a token (a webhook
    URL often does), so they are masked. A host that is neither a DNS name nor
    an IP literal is masked too (``https://****``). Used for the webhook URLs
    and the ``user_host`` Apprise schemes.
    """
    line = url.strip()
    scheme = _scheme_of(line)
    if not scheme:
        return "****"
    try:
        parts = urlsplit(line)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return f"{scheme}://****"
    if not host or not _showable_host(host):
        return f"{scheme}://****"
    netloc = f"[{host}]" if ":" in host else host
    if port:
        netloc = f"{netloc}:{port}"
    if parts.username or parts.password:
        netloc = f"****@{netloc}"
    rest = "/****" if (parts.path.strip("/") or parts.query or parts.fragment) else ""
    return f"{scheme}://{netloc}{rest}"


def redact_apprise_url(url: str) -> str:
    """Scheme and host only, for logs and errors: ``json://user:****@host:8080/****``.

    For a ``fixed_host`` scheme (or an unknown one) the part after ``://`` is a
    token (``tgram://123:ABC/456``), so all of it is masked. The frontend's
    ``censorAppriseUrl`` stays for display.
    """
    scheme = _scheme_of(url.strip())
    if not scheme:
        return "****"
    if ALLOWED_SCHEMES.get(scheme) != "user_host":
        return f"{scheme}://****"
    return redact_url(url)


_TOKEN_SPLIT = re.compile(r"[/:@?&=#,;]+")


def url_secret_parts(url: str) -> list[str]:
    """The parts of a URL that names its host that an error text must not show.

    The host and port stay readable (as in ``redact_url``); the user,
    password, path segments, query values and fragment are masked, except a
    part the host name already shows (``api`` in ``https://api.example.com/api``):
    scrubbing it would mask the host too. A URL with no readable host is a
    token as a whole, with its pieces.
    """
    line = url.strip()
    rest = line.split("://", 1)[-1]
    try:
        parts = urlsplit(line)
        hostname = parts.hostname
    except ValueError:
        hostname = None
    if not hostname or not _showable_host(hostname):
        return [rest, *_TOKEN_SPLIT.split(rest)]
    secrets: list[str] = []
    userinfo = parts.netloc.rpartition("@")[0] if "@" in parts.netloc else ""
    if userinfo:
        secrets.extend([userinfo, *userinfo.split(":", 1)])
    secrets.extend(segment for segment in parts.path.split("/") if segment)
    for item in parts.query.split("&"):
        key, sep, value = item.partition("=")
        secrets.append(value if sep else key)
    secrets.append(parts.fragment)
    host = hostname.lower()
    return [secret for secret in secrets if secret and secret.lower() not in host]


def apprise_url_secret_parts(url: str) -> list[str]:
    """The parts of an Apprise URL an error text must not show.

    The host and port of a ``user_host`` scheme stay readable
    (``url_secret_parts``). For any other scheme everything after ``://`` is a
    token (``tgram://123:ABC/456`` gives ``123:ABC/456``, ``123``, ``ABC``,
    ``456``).
    """
    line = url.strip()
    if ALLOWED_SCHEMES.get(_scheme_of(line)) == "user_host":
        return url_secret_parts(line)
    rest = line.split("://", 1)[-1]
    return [rest, *_TOKEN_SPLIT.split(rest)]
