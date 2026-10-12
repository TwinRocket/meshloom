"""Contract tests for meshcore ``send_cmd`` destination handling (pinned 2.3.15)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from meshcore.commands.messaging import MessagingCommands
from meshcore.events import EventType
from meshcore.packets import TxtType

from app.models import Contact
from app.routers.server_control import _send_cmd_destination


def _handler() -> MessagingCommands:
    handler = MessagingCommands()
    handler.send = AsyncMock(return_value=MagicMock(type=EventType.MSG_SENT, payload={}))
    return handler


@pytest.mark.asyncio
async def test_library_send_cmd_treats_public_key_string_as_repeater():
    """Since meshcore 2.3.15 a bare key no longer crashes, but the type is lost.

    The library assumes a repeater (``CLI_DATA``), which is wrong for a chat
    contact. Meshloom therefore keeps passing the full contact dict.
    """
    handler = _handler()
    await handler.send_cmd("aa" * 32, "ver")

    payload = handler.send.await_args.args[0]
    assert payload[1] == TxtType.CLI_DATA.value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("contact_type", "expected_txt_type"),
    [
        (1, TxtType.CLI_CMD.value),
        (2, TxtType.CLI_DATA.value),
        (3, TxtType.CLI_DATA.value),
        (4, TxtType.CLI_DATA.value),
    ],
)
async def test_library_send_cmd_uses_contact_type_for_txt_type(contact_type, expected_txt_type):
    handler = _handler()
    dst = {"public_key": "aa" * 32, "type": contact_type}

    await handler.send_cmd(dst, "ver")

    payload = handler.send.await_args.args[0]
    assert payload[0] == 2  # CommandType.SEND_TXT_MSG
    assert payload[1] == expected_txt_type


def test_send_cmd_destination_is_radio_contact_dict():
    contact = Contact(public_key="bb" * 32, name="Rpt", type=2)
    dst = _send_cmd_destination(contact)
    assert dst["public_key"] == "bb" * 32
    assert dst["type"] == 2
    assert "adv_name" in dst
