"""The one escaper for text that reaches a Markdown-format channel.

Mesh text (node names, message bodies, channel names) is hostile: a node called
``[click](https://evil)`` or a message ``@everyone`` must arrive as text and ping
nobody. Every Markdown channel (the native Discord transport, a generic Apprise
channel in Markdown format, the Markdown preset bodies) goes through
``neutralise_mentions`` then ``escape_markdown``, via
``render.event_notification_text(markdown=True)``. HTML uses ``html.escape``.

Escaping is not enough on its own for a webhook that can ping (Discord), so that
transport also sends ``allowed_mentions: {"parse": []}``.
"""

from __future__ import annotations

import re

# Room-wide pings of Discord, Slack, Mattermost, Rocket.Chat and Matrix, and the
# <@user>, <@&role>, <#channel> and <!channel> tokens of Discord and Slack. No word
# boundary after ``everyone``/``here``: Discord pings on ``@everyoneX`` too.
_MENTION = re.compile(r"@(everyone|here)|@(channel|all|room)\b|<[@!#]", re.IGNORECASE)
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~<])")

ZERO_WIDTH_SPACE = "​"


def neutralise_mentions(text: str) -> str:
    """``@everyone``, ``@channel``, ``<@id>``, ``<@&id>``, ``<!here>``... no longer ping:
    a zero-width space goes after ``@`` (or after ``<@``, ``<!`` and ``<#``)."""
    return _MENTION.sub(_break_mention, text)


def _break_mention(match: re.Match[str]) -> str:
    token = match.group(0)
    if token.startswith("<"):
        return f"{token}{ZERO_WIDTH_SPACE}"
    return f"@{ZERO_WIDTH_SPACE}{token[1:]}"


def escape_markdown(text: str) -> str:
    """User text shown as text: no injected link, heading, emphasis, code or spoiler."""
    return _MARKDOWN_SPECIAL.sub(r"\\\1", text)


def markdown_safe(text: str) -> str:
    """Mentions neutralised, then Markdown escaped: the rule for a Markdown channel."""
    return escape_markdown(neutralise_mentions(text))
