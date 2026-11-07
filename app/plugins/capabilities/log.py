"""`log {level, message}` (no permission; usually a notification): one line in
the instance's log ring (the stderr ring of the supervisor), as
`[plugin <level>] <message>`. `level` outside `LEVELS` reads as `info`; control
characters are dropped and the ring cuts the line at 2 KiB.
"""

import re
from typing import Any

from app.plugins.capabilities.base import Call, capability

LEVELS = ("debug", "info", "warning", "error")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f  ]+")


def log_line(params: Any) -> bytes:
    params = params if isinstance(params, dict) else {}
    level = params.get("level")
    level = level if level in LEVELS else "info"
    message = params.get("message")
    text = " ".join(_CONTROL_RE.sub(" ", message if isinstance(message, str) else "").split())
    return f"[plugin {level}] {text}\n".encode("utf-8", "replace")


@capability("log", None)
async def log(call: Call) -> None:
    call.instance.log.feed(log_line(call.params))
