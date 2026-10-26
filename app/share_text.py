"""Text-only summary of a share message, for push and e-mail bodies (MSHL-250).

A message the composer wrote as ``📷 <link>``, ``🔒 <link>`` or ``<link> caption``
(``<link>`` a service link such as ``https://meshl.ink/AbCdEfG``, behind an optional
``@[Name] `` reply mention) is summarised as ``[image]``, ``[encrypted file]`` or
``[link]`` (``[image]``/``[fichier chiffré]``/``[lien]`` in French), followed by the
caption when there is one. So a notification never shows only a URL.

Pure and syntactic: the summary comes from the text alone. No I/O, no import of the
share service, no request for the link's meta, and the URL, its path and its
``#fragment`` never reach the
output. A caption has any URL-looking word removed for the same reason (scheme links,
``www.`` names, ``host.tld/path`` words and ``javascript:``/``data:`` style tokens).
Any ``#fragment`` after a link is dropped unseen, defensively (the composer writes none).
Mirrors ``describeSharePreview`` (``frontend/src/utils/messagePreview.ts``).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal
from urllib.parse import urlsplit

# ``app.services.share.SHARE_HOSTS`` (a test pins the equality); not imported here so
# this module stays free of the HTTP client and the repository.
DEFAULT_SHARE_HOSTS: tuple[str, ...] = ("meshl.ink",)

# The id grammar of ``app.services.share.SHARE_ID_RE``, optionally behind ``/l/``.
_SHARE_PATH = re.compile(r"/(?:l/)?[1-9A-HJ-NP-Za-km-z]{7}/?")
_REPLY_MENTIONS = re.compile(r"(?:@\[[^\]]*\]\s+)+")
# Invisible characters some clients put before a text, and the emoji presentation selector.
_INVISIBLE = re.compile("[\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")
_FILE_PREFIX = re.compile("(📷|🔒)\ufe0f?\\s+(\\S+)(?:\\s+(.*))?", re.DOTALL)
# A word that reads as an address: ``scheme://``, ``scheme:`` (javascript:, data:, mailto:),
# ``www.`` or ``host.tld/path`` (also ``host.tld#frag``).
_URLISH_WORD = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://\S*|(?:javascript|data|vbscript|mailto|file|blob|about|tel|sms):\S*|www\.\S*|[\w-]+(?:\.[\w-]+)+(?:[/#?:]\S*)?)$",
    re.IGNORECASE,
)

Kind = Literal["image", "encrypted", "link"]

_LABELS: dict[str, dict[Kind, str]] = {
    "en": {"image": "[image]", "encrypted": "[encrypted file]", "link": "[link]"},
    "fr": {"image": "[image]", "encrypted": "[fichier chiffré]", "link": "[lien]"},
}


def _is_share_url(url: str, hosts: Sequence[str]) -> bool:
    """A service link: https, an allowed host, a share id path, no query, no port, no user info."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme != "https" or parts.username is not None or parts.password is not None:
        return False
    if port is not None or parts.query or not parts.hostname:
        return False
    host = parts.hostname.lower().rstrip(".")
    if host not in {h.lower() for h in hosts}:
        return False
    return _SHARE_PATH.fullmatch(parts.path) is not None


def _clean_caption(caption: str) -> str:
    """The caption on one line, without any URL-looking word."""
    return " ".join(w for w in caption.split() if not _URLISH_WORD.match(w))


def share_text_summary(
    text: str, lang: str = "en", hosts: Sequence[str] = DEFAULT_SHARE_HOSTS
) -> str | None:
    """``[image]``, ``[encrypted file]`` or ``[link]`` (plus the caption), None for any other text.

    ``lang`` is ``fr`` or ``en`` (anything else reads French, like the notification
    renderer). ``hosts`` are the share hosts (``/api/share/status``).
    """
    if not isinstance(text, str):
        return None
    body = _INVISIBLE.sub("", text).strip()
    mention = _REPLY_MENTIONS.match(body)
    if mention:
        body = body[mention.end() :]
    kind: Kind
    file_match = _FILE_PREFIX.fullmatch(body)
    if file_match:
        url = file_match.group(2).split("#", 1)[0]
        if not _is_share_url(url, hosts):
            return None
        kind = "image" if file_match.group(1) == "📷" else "encrypted"
        caption = file_match.group(3) or ""
    else:
        parts = body.split(None, 1)
        if not parts:
            return None
        first, rest = parts[0].split("#", 1)[0], parts[1] if len(parts) > 1 else ""
        if not _is_share_url(first, hosts):
            return None
        kind = "link"
        caption = rest
    label = _LABELS["en" if lang == "en" else "fr"][kind]
    caption = _clean_caption(caption)
    return f"{label} {caption}" if caption else label
