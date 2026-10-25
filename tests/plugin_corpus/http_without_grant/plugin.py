import meshloom_plugin
from meshloom_plugin.errors import HostError


def _ping(params):
    return "pong" if params.get("ping") else None


async def _call(method, params):
    try:
        return await meshloom_plugin.host().call(method, params)
    except HostError as exc:
        data = exc.error.get("data") or {}
        return {"refused": data.get("code") or exc.error.get("message")}


async def on_message(params):
    if _ping(params):
        return "pong"
    return await _call("http.fetch", {"url": "https://example.com/", "method": "GET"})
