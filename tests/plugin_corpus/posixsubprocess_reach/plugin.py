import asyncio
import asyncio.unix_events


async def on_message(params):
    via = params["via"]
    if via == "import":
        import _posixsubprocess

        return repr(_posixsubprocess.fork_exec)
    if via == "asyncio":
        return repr(asyncio.unix_events.subprocess.Popen(["/bin/true"]))
    process = await asyncio.create_subprocess_exec("/bin/true")
    return await process.wait()
