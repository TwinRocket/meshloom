from meshcore.packets import CommandType

from app.radio_proxy.policy import CommandDisposition, classify_command, send_txt_is_plain


def test_virtualize_reads_and_session_mutations():
    for code in (
        CommandType.APP_START,
        CommandType.DEVICE_QEURY,
        CommandType.GET_CONTACTS,
        CommandType.GET_CHANNEL,
        CommandType.SET_CHANNEL,
        CommandType.SYNC_NEXT_MESSAGE,
        CommandType.ADD_UPDATE_CONTACT,
        CommandType.REMOVE_CONTACT,
        CommandType.SET_DEVICE_TIME,
        CommandType.RESET_PATH,
    ):
        assert classify_command(code.value) is CommandDisposition.VIRTUALIZE


def test_sends_and_export_and_deny():
    assert classify_command(CommandType.SEND_TXT_MSG.value) is CommandDisposition.SEND
    assert classify_command(CommandType.SEND_CHANNEL_TXT_MSG.value) is CommandDisposition.SEND
    assert classify_command(CommandType.EXPORT_PRIVATE_KEY.value) is CommandDisposition.DISABLED
    for code in (
        CommandType.REBOOT,
        CommandType.SEND_LOGIN,
        CommandType.SEND_STATUS_REQ,
        CommandType.PATH_DISCOVERY,
        CommandType.RUN_CLI_COMMAND,
        CommandType.SET_RADIO_PARAMS,
        CommandType.SEND_SELF_ADVERT,
        CommandType.IMPORT_PRIVATE_KEY,
    ):
        assert classify_command(code.value) is CommandDisposition.ERROR


def test_plain_txt_only():
    assert send_txt_is_plain(0)
    assert not send_txt_is_plain(1)
    assert not send_txt_is_plain(2)
    assert not send_txt_is_plain(3)
