import asyncio


def _ping(params):
    return "pong" if params.get("ping") else None


async def on_message(params):
    if _ping(params):
        return "pong"
    while True:
        for _ in range(100000):
            pass
        await asyncio.sleep(0)
