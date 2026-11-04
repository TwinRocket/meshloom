"""A plugin child on any event loop: `subprocess.Popen`, plugged into asyncio streams.

`asyncio.create_subprocess_exec` hands its arguments to the running loop, and the loop
decides which ones it knows. uvloop, the loop uvicorn picks wherever it is installed
(the Docker image and the packages), refuses `user`, `group`, `extra_groups` and
`umask` (`ValueError: unexpected kwargs`), which are exactly what the `setuid` isolation
level launches with: no plugin could start there, while the tests on the default loop
passed. `subprocess.Popen` takes every launch argument of a backend, on every loop, and
drops privileges in its C child code, before anything of Python runs as the child.

Only the pipes go through the loop (`connect_read_pipe`, `connect_write_pipe`). A daemon
thread per child blocks in `Popen.wait()` and reports the exit: this does not depend on
the loop's child watching and does not hold a slot of the default executor for the whole
life of a plugin. `wait()` returns when the child has exited, whatever a grandchild still
holds open, on every Python version (asyncio's waits for the pipes on 3.11, gh-119710).
"""

import asyncio
import contextlib
import subprocess
import threading
from asyncio.streams import FlowControlMixin
from collections.abc import Callable
from typing import Any


class ChildProcess:
    """The part of `asyncio.subprocess.Process` the supervisor uses, and `close_pipes`."""

    def __init__(self, popen: "subprocess.Popen[Any]") -> None:
        self._popen = popen
        self._loop = asyncio.get_running_loop()
        self._exited = asyncio.Event()
        self._transports: dict[int, asyncio.BaseTransport] = {}
        self.stdin: asyncio.StreamWriter | None = None
        self.stdout: asyncio.StreamReader | None = None
        self.stderr: asyncio.StreamReader | None = None

    @classmethod
    async def spawn(
        cls, argv: list[str], *, kill: Callable[[int], None], **kwargs: Any
    ) -> "ChildProcess":
        """Start `argv` with RPC pipes on stdin and stdout and a log pipe on stderr.

        `kill(pid)` takes the child down if it cannot be plugged in: through the backend,
        since a server without `CAP_KILL` cannot signal a child under another uid."""
        # Fails synchronously (`OSError`, `ValueError`) with no child left. On the loop's
        # thread, as asyncio's own unix transport does.
        popen = subprocess.Popen(argv, stderr=subprocess.PIPE, bufsize=0, **kwargs)
        child = cls(popen)
        try:
            threading.Thread(
                target=child._watch_exit, name=f"plugin-child-{popen.pid}", daemon=True
            ).start()
            await child._connect_pipes()
        except BaseException:
            kill(popen.pid)
            child._close_all()
            raise
        return child

    async def _connect_pipes(self) -> None:
        popen, loop = self._popen, self._loop
        assert popen.stdin is not None and popen.stdout is not None and popen.stderr is not None
        self.stdout = await self._reader(1, popen.stdout)
        self.stderr = await self._reader(2, popen.stderr)
        transport, protocol = await loop.connect_write_pipe(
            lambda: FlowControlMixin(loop=loop), popen.stdin
        )
        self._transports[0] = transport
        self.stdin = asyncio.StreamWriter(transport, protocol, None, loop)

    async def _reader(self, fd: int, pipe: Any) -> asyncio.StreamReader:
        reader = asyncio.StreamReader(loop=self._loop)
        transport, _protocol = await self._loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader, loop=self._loop), pipe
        )
        self._transports[fd] = transport
        return reader

    def _watch_exit(self) -> None:
        self._popen.wait()
        # The loop may be gone when a process outlives it (a test, the end of the server).
        with contextlib.suppress(RuntimeError):
            self._loop.call_soon_threadsafe(self._exited.set)

    @property
    def pid(self) -> int:
        return self._popen.pid

    @property
    def returncode(self) -> int | None:
        return self._popen.returncode

    async def wait(self) -> int | None:
        await self._exited.wait()
        return self._popen.returncode

    def _close_all(self) -> None:
        """After a failed spawn: the transports, and the pipes no transport took."""
        self.close_pipes()
        pipes = {0: self._popen.stdin, 1: self._popen.stdout, 2: self._popen.stderr}
        for fd, pipe in pipes.items():
            if fd not in self._transports and pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()

    def close_pipes(self, *fds: int) -> None:
        """Close the host's end of these pipes (0, 1, 2), all of them by default."""
        for fd in fds or tuple(self._transports):
            transport = self._transports.get(fd)
            if transport is not None:
                transport.close()
