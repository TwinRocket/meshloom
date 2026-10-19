"""The shared Markdown escaper, against hostile node names and messages."""

from __future__ import annotations

import re

import pytest

from app.notifications.markdown import escape_markdown, markdown_safe, neutralise_mentions
from app.notifications.render import event_notification_text

ZWSP = "​"

HOSTILE = [
    "venez @everyone [ici](https://evil)",
    "@here wake up",
    "<@123456789012345678> <@&987654321098765432> <@!42>",
    "<#1234> <!channel> <!here>",
    "[click](javascript:alert(1)) ![img](https://evil/x.png)",
    "**bold** _it_ ~~strike~~ ||spoiler|| `code` ```block```",
    "# heading\n> quote\n- list\n1. item\n===\n---",
    "@Everyone @HERE @channel @all @room",
    "@everyone1 @heres",
    "a\\b\\\\c",
    "<script>alert(1)</script> &amp; &lt;",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_no_markdown_construct_or_mention_survives(text):
    out = markdown_safe(text)
    # Every Markdown-significant character is backslash-escaped.
    stripped = out.replace("\\\\", "")
    for char in "*_[]()#>~<`|":
        for index, found in enumerate(stripped):
            if found == char:
                assert index and stripped[index - 1] == "\\", (char, out)
    # No ping token is left intact: a zero-width space breaks each one.
    bare = out.replace("\\", "")
    assert not re.search(r"@(everyone|here)", bare, re.IGNORECASE), out
    assert not re.search(r"@(channel|all|room)\b", bare, re.IGNORECASE), out
    assert not re.search(r"<[@!#](?!\u200b)", bare), out


def test_the_rule_in_the_ticket():
    out = markdown_safe("venez @everyone [ici](https://evil)")
    assert out == f"venez @{ZWSP}everyone \\[ici\\]\\(https://evil\\)"


def test_mention_tokens_are_broken_not_removed():
    assert neutralise_mentions("@everyone") == f"@{ZWSP}everyone"
    assert neutralise_mentions("<@&5>") == f"<@{ZWSP}&5>"
    assert neutralise_mentions("@everyoneX") == f"@{ZWSP}everyoneX"
    assert neutralise_mentions("mail me@example.org") == "mail me@example.org"


def test_escape_is_idempotent_free_of_surprises_on_plain_text():
    assert escape_markdown("hello world") == "hello world"
    assert escape_markdown("a\\b") == "a\\\\b"


@pytest.mark.parametrize("name", ["@everyone", "[x](https://evil)", "<@&1>", "*Hill*"])
def test_node_names_are_escaped_in_every_markdown_text(name):
    payload = {"event": "first_seen", "name": name, "contact_type": 2}
    title, body = event_notification_text(payload, "en", markdown=True)
    plain_title, plain_body = event_notification_text(payload, "en")
    assert "@everyone" not in title + body and "<@&1" not in title + body
    assert "](" not in title + body
    assert "*Hill*" not in title + body
    # Plain text is not Markdown-escaped, but mentions are neutralised there too.
    assert "@everyone" not in plain_title + plain_body


def test_a_channel_message_through_a_markdown_channel_is_escaped():
    payload = {
        "event": "message",
        "type": "CHAN",
        "conversation_key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72",
        "channel_name": "Public",
        "sender_name": "Eve",
        "text": "venez @everyone [ici](https://evil)",
    }
    _, body = event_notification_text(payload, "en", markdown=True)
    assert "@everyone" not in body
    assert "\\[ici\\]\\(https://evil\\)" in body
