import asyncio
import asyncio.base_events
import sys


async def on_message(params):
    via = params["via"]
    if via == "sys.modules":
        names = ("socket", "_socket", "ssl", "_ssl", "subprocess", "_posixsubprocess")
        return [name for name in names if name in sys.modules]
    if via == "asyncio":
        return repr(asyncio.base_events.socket.socket())
    reader, writer = await asyncio.open_connection("127.0.0.1", 9)
    writer.close()
    return "connected"
