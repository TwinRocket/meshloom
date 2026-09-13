"""Companion command policy for the virtual radio proxy."""

from __future__ import annotations

from enum import StrEnum

from meshcore.packets import CommandType


class CommandDisposition(StrEnum):
    VIRTUALIZE = "virtualize"
    SEND = "send"
    DISABLED = "disabled"
    ERROR = "error"


_VIRTUALIZE = frozenset(
    {
        CommandType.APP_START.value,
        CommandType.GET_CONTACTS.value,
        CommandType.GET_DEVICE_TIME.value,
        CommandType.SET_DEVICE_TIME.value,
        CommandType.ADD_UPDATE_CONTACT.value,
        CommandType.SYNC_NEXT_MESSAGE.value,
        CommandType.RESET_PATH.value,
        CommandType.REMOVE_CONTACT.value,
        CommandType.GET_BATT_AND_STORAGE.value,
        CommandType.DEVICE_QEURY.value,
        CommandType.HAS_CONNECTION.value,
        CommandType.GET_CONTACT_BY_KEY.value,
        CommandType.GET_CHANNEL.value,
        CommandType.SET_CHANNEL.value,
    }
)

_SEND = frozenset(
    {
        CommandType.SEND_TXT_MSG.value,
        CommandType.SEND_CHANNEL_TXT_MSG.value,
    }
)

_DISABLED = frozenset({CommandType.EXPORT_PRIVATE_KEY.value})


def classify_command(code: int) -> CommandDisposition:
    if code in _DISABLED:
        return CommandDisposition.DISABLED
    if code in _VIRTUALIZE:
        return CommandDisposition.VIRTUALIZE
    if code in _SEND:
        return CommandDisposition.SEND
    return CommandDisposition.ERROR


def send_txt_is_plain(txt_type: int) -> bool:
    return txt_type == 0
