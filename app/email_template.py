"""HTML notification emails (instance language, Meshloom mark, richer body).

The email loads nothing from the network when it is opened: the mark is a PNG
from this package, sent as an inline MIME part (``multipart/related``) and
referenced by ``cid:``. No remote image, no link to a Meshloom site.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.notifications.render import (  # noqa: F401  (re-exported: callers import them from here)
    LOGO_CID,
    AppLanguage,
    enrich_notification_payload,
    event_notification_text,
    normalize_email_language,
    notification_email_lines,
    render_html_email,
    render_plain_email,
    rule_kind,
    telemetry_email_lines,
)

# Inline logo: Apprise gives the part ``Content-ID: <LOGO_CID>`` because the
# attachment name matches the ``cid:`` reference in the HTML (see notify.py).
LOGO_PATH = Path(__file__).resolve().parent / "assets" / LOGO_CID


def email_subject(payload: Mapping[str, Any], lang: AppLanguage) -> str:
    title, _ = event_notification_text(dict(payload), lang)
    title = title or "Meshloom"
    # An urgent event (radio offline) stands out in the inbox list.
    return f"[URGENT] {title}" if payload.get("urgent") else title


async def resolve_instance_language() -> AppLanguage:
    """The language of the e-mail and Apprise text.

    ``app_settings.notification_language`` when it is ``fr`` or ``en``; with
    ``auto`` (the default), the browsers' language: English only with a strict
    majority of English push subscriptions, a tie is French.
    """
    try:
        from app.repository.settings import AppSettingsRepository

        chosen = (await AppSettingsRepository.get()).notification_language
    except Exception:
        chosen = "auto"
    if chosen in ("fr", "en"):
        return chosen
    try:
        from app.repository.push_subscriptions import PushSubscriptionRepository

        subs = await PushSubscriptionRepository.get_all()
    except Exception:
        return "fr"
    english = sum(1 for sub in subs if normalize_email_language(sub.get("language")) == "en")
    french = sum(1 for sub in subs if normalize_email_language(sub.get("language")) == "fr")
    return "en" if english > french else "fr"
