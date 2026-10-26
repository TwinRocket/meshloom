"""Per-channel message templates (MSHL-127).

A channel of a text-body type (a generic Apprise channel or a preset, ntfy, Discord) may
carry ``template_dm`` and ``template_channel``: the body of a direct message and of a
channel message, written with the variables of the fanout Apprise module plus ``title``
(``{sender_name}: {text} via {hops}``). Empty means the renderer's default body.

The substitution is the fanout module's single pass (``apprise_mod._apply_format``): a value
that is put in is never expanded again, and a ``{name}`` nobody recognises stays as typed.
The renderer (``render.event_notification_text``) applies a template to ``message.*`` events
only, after the text policy (``{text}`` is empty when the policy strips the text), the
mention neutralising and, on a Markdown channel, the escaping of the values that come from
the mesh. A template is validated on save against dummy variables and capped in length.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.fanout.apprise_mod import FORMAT_VARIABLES, _apply_format, _build_template_vars
from app.notifications.channels import ChannelError
from app.notifications.markdown import escape_markdown, neutralise_mentions

TEMPLATE_VARIABLES: tuple[str, ...] = (*FORMAT_VARIABLES, "title")
MAX_TEMPLATE_LENGTH = 500
# The row types whose transport builds its body with ``event_notification_text``
# (a preset rides on ``apprise``). E-mail, webhook and Web Push have their own bodies.
TEMPLATE_ROW_TYPES = frozenset({"apprise", "ntfy", "discord"})
# Variables that carry text typed by someone on the mesh: escaped on a Markdown channel.
# The others (hex, numbers, the backticked hop list) are safe and keep their form.
MESH_TEXT_VARIABLES = frozenset({"text", "sender_name", "channel_name", "title"})

_DUMMY = dict.fromkeys(TEMPLATE_VARIABLES, "test")

SAMPLE_PATHS = [{"path": "a1b2c3", "path_len": 3, "rssi": -87, "snr": 6.5}]
SAMPLE_DM: dict[str, Any] = {
    "event": "message",
    "type": "PRIV",
    "conversation_key": "ab" * 32,
    "sender_key": "ab" * 32,
    "sender_name": "Alice",
    "text": "See you at the summit",
    "paths": SAMPLE_PATHS,
}
SAMPLE_CHANNEL: dict[str, Any] = {
    "event": "message",
    "type": "CHAN",
    "conversation_key": "cd" * 16,
    "channel_name": "#hiking",
    "sender_name": "Bob",
    "text": "Bob: Anyone on the ridge?",
    "paths": SAMPLE_PATHS,
}


def validate_template(template: object, *, field: str = "template") -> str:
    """The template as stored (``""`` when blank), or a ``ChannelError``.

    Checked against dummy variables, the way the fanout Apprise formats are; the length cap
    and a control-character refusal (a newline and a tab are fine) keep a stored template
    harmless.
    """
    if template is None:
        return ""
    if not isinstance(template, str):
        raise ChannelError("template_invalid", field=field)
    if len(template) > MAX_TEMPLATE_LENGTH:
        raise ChannelError("template_too_long", field=field, max=MAX_TEMPLATE_LENGTH)
    if any(ord(c) < 32 and c not in "\n\t" for c in template) or "\x7f" in template:
        raise ChannelError("template_invalid", field=field)
    if not template.strip():
        return ""
    try:
        _apply_format(template, _DUMMY)
    except Exception:
        raise ChannelError("template_invalid", field=field) from None
    return template


def check_templates(
    row_type: str, template_dm: object, template_channel: object
) -> tuple[str, str]:
    """Both templates validated for a channel of ``row_type`` (an unsupported type refuses
    a non-empty one: ``template_unsupported``)."""
    dm = validate_template(template_dm, field="template_dm")
    channel = validate_template(template_channel, field="template_channel")
    if (dm or channel) and row_type not in TEMPLATE_ROW_TYPES:
        raise ChannelError("template_unsupported", type=row_type)
    return dm, channel


def template_for(data: Mapping[str, Any], template_dm: str, template_channel: str) -> str:
    """The template that applies to this message payload ('' when none)."""
    return template_dm if data.get("type") == "PRIV" else template_channel


def build_variables(
    data: Mapping[str, Any], *, title: str, text: str, markdown: bool
) -> dict[str, str]:
    """The variables of a message: the fanout set, ``text`` as the notification shows it
    (already policy-checked and share-summarised, so empty when the policy strips it) and
    ``title``. Every value has its mentions neutralised; on a Markdown channel the ones
    typed on the mesh are escaped too."""
    values = _build_template_vars(dict(data))
    values["text"] = text
    values["title"] = title
    out: dict[str, str] = {}
    for name, value in values.items():
        value = neutralise_mentions(str(value))
        if markdown and name in MESH_TEXT_VARIABLES:
            value = escape_markdown(value)
        out[name] = value
    return out


def apply_template(template: str, variables: Mapping[str, str]) -> str:
    """One pass over the template: substituted values are not expanded again."""
    return _apply_format(template, dict(variables))


def preview(
    template_dm: str,
    template_channel: str,
    *,
    language: str,
    markdown: bool,
    text_policy: str,
) -> dict[str, str]:
    """The rendered samples for ``POST /api/notifications/channels/preview-template``: the
    same renderer a send goes through, on a sample direct and channel message. Raises
    ``ChannelError`` for a template ``validate_template`` refuses."""
    from app.notifications.render import event_notification_text

    dm = validate_template(template_dm, field="template_dm")
    channel = validate_template(template_channel, field="template_channel")
    rendered: dict[str, str] = {}
    for key, sample in (("dm", SAMPLE_DM), ("channel", SAMPLE_CHANNEL)):
        _title, body = event_notification_text(
            dict(sample),
            language,
            include_message_text=text_policy,
            markdown=markdown,
            template_dm=dm,
            template_channel=channel,
        )
        rendered[key] = body
    return rendered
