import os

import meshloom_plugin


def _ping(params):
    return "pong" if params.get("ping") else None


def on_message(params):
    if _ping(params):
        return "pong"
    fd = meshloom_plugin.host()._out
    chunk = b"x" * 65536
    for _ in range(160):
        os.write(fd, chunk)
    return "written"
