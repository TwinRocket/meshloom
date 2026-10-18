"""The e-mail transport: one ephemeral Apprise ``mailto(s)://`` URL, one SMTP send.

Apprise (2.x, maintained) speaks SMTP with TLS by default (``mailtos`` + STARTTLS,
or ``ssl``; ``none`` is a plain ``mailto`` the user chose). The URL carries the
password, so it is built per send and never stored or logged. ``to`` takes several
recipients (one per line in the UI; commas and semicolons work too) and ``cc`` an
optional list (the Cc addresses join the recipients: each gets its own message once). Apprise's mailto plugin sends one message per recipient inside that
one URL and reports a single bool, so a rejected recipient fails the whole delivery
while the others may have been reached (documented in ``docs/user``). The dispatcher
never retries it, which would resend to who already got it: the transport answers
``failed``.

The text comes from the renderer in the channel's language. When the channel's text
policy withholds a message's text (``never``, or ``public_only`` for a DM or a
private channel), the subject is the neutral "New message" and the body keeps the
sender and the channel but not the text. SMTP runs inside Apprise (``smtplib``), so
the host is checked by the egress guard right before the send, but the connection
cannot be pinned to the validated address (the residual of the Apprise channels).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

from app.log_redaction import secret_scope
from app.net.egress_guard import EgressBlocked, tls_check_may_be_off, validate_destination
from app.notifications.channels import Channel
from app.notifications.executor import run_blocking
from app.notifications.render import Rendered, _is_message, message_text_is_public
from app.notifications.transports.base import SEND_OK, Delivery, SendResult, channel_language

logger = logging.getLogger(__name__)

_RECIPIENT_SPLIT = re.compile(r"[,;\r\n]+")
MAX_RECIPIENTS = 20
_DEFAULT_PORTS = {"ssl": 465, "none": 25}


def parse_recipients(raw: str | None) -> list[str]:
    """Addresses from a free-form list (newlines, commas, semicolons), without duplicates."""
    seen: dict[str, None] = {}
    for item in _RECIPIENT_SPLIT.split(raw or ""):
        item = item.strip()
        if item and "@" in item:
            seen.setdefault(item, None)
    return list(seen)[:MAX_RECIPIENTS]


def build_email_apprise_url(email: Mapping[str, Any], *, verify_tls: bool = True) -> str:
    """Build an ephemeral Apprise mailto/mailtos URL. Never persist this string."""
    mode = str(email.get("mode") or "starttls")
    if mode == "none":
        scheme = "mailto"
        extra: dict[str, str] = {}
    elif mode == "ssl":
        scheme = "mailtos"
        extra = {"mode": "ssl"}
    else:
        scheme = "mailtos"
        extra = {}

    params: dict[str, str] = {}
    user = str(email.get("user") or "")
    password = str(email.get("password") or "")
    host = str(email.get("host") or "")
    from_addr = str(email.get("from") or email.get("from_address") or "")
    # Apprise sends one message per ``to`` address and copies the whole Cc on each, so
    # N recipients would give every Cc address N copies: Cc addresses are plain
    # recipients here and get their own message once.
    to_addrs = parse_recipients(f"{email.get('to') or ''},{email.get('cc') or ''}")
    port = email.get("port")
    if user:
        params["user"] = user
    if password:
        params["pass"] = password
    if host:
        params["smtp"] = host
    if from_addr:
        params["from"] = from_addr
    if to_addrs:
        params["to"] = ",".join(to_addrs)
    if port not in (None, ""):
        params["port"] = str(port)
    params.update(extra)
    if not verify_tls:
        params["verify"] = "no"
    # Embed image attachments referenced by cid: (the logo) as inline parts of
    # a multipart/related message, instead of downloadable attachments.
    params["inline"] = "yes"
    return f"{scheme}://_?{urlencode(params)}"


def send_email_sync(url: str, title: str, body: str) -> bool:
    import apprise as apprise_lib
    from apprise import NotifyFormat
    from apprise.attachment.file import AttachFile

    from app.email_template import LOGO_CID, LOGO_PATH

    notifier = apprise_lib.Apprise()
    if not notifier.add(url):
        return False
    # The logo travels inside the email (cid:), so opening it fetches nothing.
    attach = apprise_lib.AppriseAttachment()
    attach.add(AttachFile(str(LOGO_PATH), name=LOGO_CID, mimetype="image/png"))
    return bool(
        notifier.notify(title=title, body=body, body_format=NotifyFormat.HTML, attach=attach)
    )


def text_is_withheld(payload: Mapping[str, Any], policy: str) -> bool:
    """True when the policy keeps a message's text out of the mail."""
    return _is_message(payload) and (
        policy == "never" or (policy == "public_only" and not message_text_is_public(payload))
    )


def render_email(payload: Mapping[str, Any], lang: str, policy: str) -> tuple[str, str]:
    """(subject, HTML body) in ``lang`` under the channel's text policy."""
    from app.email_template import email_subject, render_html_email

    language = "en" if lang == "en" else "fr"
    if text_is_withheld(payload, policy):
        subject = "New message" if language == "en" else "Nouveau message"
        return subject, render_html_email(
            {k: v for k, v in payload.items() if k != "text"}, language
        )
    return email_subject(payload, language), render_html_email(payload, language)


class EmailTransport:
    async def send(
        self,
        channel: Channel,
        rendered: Rendered | None,
        event: Delivery,
        secrets: dict[str, str],
    ) -> SendResult:
        config = {**channel.config, "password": secrets.get("password", "")}
        if not config.get("host") or not parse_recipients(str(config.get("to") or "")):
            return SendResult("failed", error_code="not_configured")
        host = str(config["host"]).strip()
        bracketed = f"[{host}]" if ":" in host else host
        mode = str(config.get("mode") or "starttls")
        try:
            target = await validate_destination(
                f"http://{bracketed}:{config.get('port') or _DEFAULT_PORTS.get(mode, 587)}/",
                allow_private_network=channel.allow_private_network,
            )
        except EgressBlocked as exc:
            return SendResult("failed", error_code=exc.code)
        private = tls_check_may_be_off(target.ips)
        if not channel.verify_tls and not private:
            return SendResult("failed", error_code="verify_tls_requires_private_destination")
        if mode == "none" and (config.get("user") or config.get("password")) and not private:
            return SendResult("failed", error_code="smtp_plaintext_auth_refused")
        subject, html = render_email(
            event.payload, await channel_language(channel), channel.include_message_text
        )
        url = build_email_apprise_url(config, verify_tls=channel.verify_tls)
        try:
            with secret_scope(*(v for v in (secrets.get("password"),) if v)):
                ok = await run_blocking(send_email_sync, url, subject, html)
        except Exception as exc:
            logger.warning("notification email %s: %s", channel.id, type(exc).__name__)
            return SendResult("failed", error_code="send_failed")
        return SEND_OK if ok else SendResult("failed", error_code="send_failed")
