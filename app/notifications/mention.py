"""Mention detection: the matcher behind ``message.mention``.

A channel message mentions the radio when it holds the token ``@[<radio name>]`` (the one
``MessageRepository.get_unread_counts`` counts) or, with ``mention_whole_word``, the name as a
whole word. Both are case-insensitive. The name is a free-form string from the radio and the
text comes from the mesh, so the name is always ``re.escape``d and the pattern is compiled once
per (name, mode): the per-message cost is one regex search, with no database read.

A mention is forged by anyone in range (channel messages are not authenticated): nothing here
makes it urgent.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=8)
def _matcher(name: str, whole_word: bool) -> re.Pattern[str]:
    token = re.escape(f"@[{name}]")
    if not whole_word:
        return re.compile(token, re.IGNORECASE)
    # ``(?<!\w)``/``(?!\w)`` rather than ``\b``: a name that starts or ends with a symbol or an
    # emoji has no word boundary there.
    return re.compile(rf"{token}|(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)


def is_mention(text: str, name: str | None, *, whole_word: bool = False) -> bool:
    """Whether ``text`` mentions the radio called ``name`` (False without a name)."""
    if not name or not name.strip() or not text:
        return False
    return _matcher(name, whole_word).search(text) is not None
