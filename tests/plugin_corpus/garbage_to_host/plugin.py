import os

import meshloom_plugin


def _ping(params):
    return "pong" if params.get("ping") else None


def on_message(params):
    if _ping(params):
        return "pong"
    fd = meshloom_plugin.host()._out
    os.write(fd, b"{not json\n")
    os.write(fd, b"\xff\xfe\n")
    os.write(fd, b'{"jsonrpc": "2.0", "id": 123456789012345678901234567890, "result": 1}\n')
    os.write(fd, b'{"jsonrpc": "2.0", "id": 1, "result": "forged"}\n')
    os.write(
        fd,
        b'{"jsonrpc": "2.0", "method": "log.write", "params": ' + b"[" * 200 + b"]" * 200 + b"}\n",
    )
    return "sent"
