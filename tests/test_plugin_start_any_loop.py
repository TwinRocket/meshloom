"""A plugin starts on the event loop the server really runs on (regression, 5.0.0).

uvicorn runs on uvloop wherever it is installed, and pytest on the default loop. Under
uvloop `asyncio.create_subprocess_exec` refused the launch arguments of the `setuid`
level (`ValueError: unexpected kwargs: user, group, extra_groups, umask`): installing
any plugin in the Docker image ended in `start_failed`, while every test passed. These
tests install the bundled plugins through the real manager on both loops, with a backend
that launches with those same arguments, as far as an unprivileged process can.
"""

import asyncio
import copy
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import meshloom_plugin
from app.plugins import host as host_module
from app.plugins import installer, launch
from app.plugins.child_process import ChildProcess
from app.plugins.host import Budgets, PluginManager
from app.plugins.isolation import selection, setuid
from app.plugins.isolation.base import IsolationBackend
from app.plugins.isolation.none import NoneBackend
from tests.conftest import patched_database
from tests.plugin_isolation_support import available_backends, shared_tmp_dir

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

GRANTS = {
    "meshloom.echo": {
        "messages.read": {"channels": "public", "direct": True, "outgoing": False},
        "messages.send": {"targets": "reply"},
    },
    "meshloom.auto-responder": {
        "messages.read": {"channels": "public", "direct": True, "outgoing": False},
        "messages.send": {"targets": "reply"},
        "storage": {},
    },
}


def _loops() -> list[Any]:
    loops: list[Any] = [pytest.param(asyncio.new_event_loop, id="asyncio")]
    uvloop = pytest.importorskip("uvloop")
    loops.append(pytest.param(uvloop.new_event_loop, id="uvloop"))
    return loops


class _DroppingBackend(IsolationBackend):
    """The `none` level with the launch arguments of `setuid`; `extra_groups` needs root."""

    def popen_kwargs(self, work_dir: Path) -> dict[str, Any]:
        kwargs = {
            **super().popen_kwargs(work_dir),
            "user": os.getuid(),
            "group": os.getgid(),
            "umask": 0o077,
        }
        if os.geteuid() == 0:
            kwargs["extra_groups"] = []
        return kwargs


FAST = Budgets(
    init_timeout=10.0,
    event_timeout=5.0,
    cancel_grace=0.6,
    shutdown_grace=1.0,
    stop_timeout=5.0,
    backoff_initial=0.05,
    backoff_max=0.2,
    stagger=0.05,
    message_settle=0.0,
)


async def _install_and_run(plugins_root: Path, plugin_id: str) -> None:
    import httpx

    from app.main import app
    from app.routers import plugins as router

    async with patched_database():
        manager = PluginManager(
            FAST, plugins_root=plugins_root, cpu_reader=None, mem_total=lambda: 8 * 1024**3
        )
        previous, router.plugin_manager = router.plugin_manager, manager
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    "/api/plugins/install",
                    json={
                        "kind": "bundled",
                        "plugin_id": plugin_id,
                        "granted_permissions": GRANTS[plugin_id],
                        "config": {},
                    },
                )
                assert response.status_code == 201, response.text
                instance_id = response.json()["instance_id"]
                instance = manager.get(instance_id)
                async with asyncio.timeout(20):
                    while instance.status == "stopped" or instance.status == "starting":
                        await asyncio.sleep(0.02)
                assert instance.status == "running", instance.last_error
                answered = await client.post(
                    f"/api/plugins/{instance_id}/test", json={"text": "!ping"}
                )
                assert answered.status_code == 200, answered.text
        finally:
            await manager.stop_all(5.0)
            router.plugin_manager = previous


@pytest.mark.parametrize("new_loop", _loops())
@pytest.mark.parametrize("plugin_id", sorted(GRANTS))
def test_a_bundled_plugin_starts_on_both_loops_with_the_setuid_arguments(
    new_loop: Callable[[], asyncio.AbstractEventLoop],
    plugin_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(selection, "_current", selection.Selection(_DroppingBackend()))
    with asyncio.Runner(loop_factory=new_loop) as runner:
        runner.run(_install_and_run(tmp_path / "plugins", plugin_id))


@pytest.mark.parametrize("new_loop", _loops())
@pytest.mark.parametrize("plugin_id", sorted(GRANTS))
def test_a_bundled_plugin_starts_at_the_real_setuid_level(
    new_loop: Callable[[], asyncio.AbstractEventLoop],
    plugin_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The image's own level: root or CAP_SETUID, with the plugin account (CI's root job)."""
    backend = available_backends().get("setuid")
    if backend is None:
        pytest.skip("the setuid level needs root or CAP_SETUID and the plugin account")
    data_dir = shared_tmp_dir("meshloom-any-loop-")
    # Its own uid leases and data dir: `available_backends()` is cached, and leases left in
    # /tmp by a root run would refuse the non-root run of the same CI job.
    monkeypatch.setattr(setuid, "lease_domains", lambda: [data_dir])
    backend = copy.copy(backend)
    # `prepare_data_dir` sets the server's process-wide umask 077: it must not outlive the
    # test, or the plugin uid cannot traverse the directories later tests create.
    umask = os.umask(0o077)
    try:
        backend.prepare_data_dir(data_dir)
        monkeypatch.setattr(selection, "_current", selection.Selection(backend))
        with asyncio.Runner(loop_factory=new_loop) as runner:
            runner.run(_install_and_run(data_dir / "plugins", plugin_id))
    finally:
        os.umask(umask)


def test_every_bundled_plugin_is_covered() -> None:
    assert set(installer.list_bundled()) == set(GRANTS)


@pytest.mark.parametrize("new_loop", _loops())
def test_a_closed_input_pipe_ends_the_write_cleanly(
    new_loop: Callable[[], asyncio.AbstractEventLoop], tmp_path: Path
) -> None:
    """uvloop raises RuntimeError on a closed transport: uncaught, it left each event of a
    plugin that closed its input waiting for its budget instead of being dropped."""
    plugin_dir = Path(meshloom_plugin.__file__).parent / "official" / "echo"

    async def scenario() -> None:
        process = host_module.PluginProcess(
            plugin_dir, host_module.LogRing(1024, 128), None, backend=NoneBackend()
        )
        await process.start(lambda work: launch.runner_params(plugin_dir, work, "i", "none"))
        try:
            assert process._proc is not None
            process._proc.close_pipes(0)
            await asyncio.sleep(0.05)
            with pytest.raises(host_module.ProcessEnded):
                await process.notify("plugin.ping")
        finally:
            await process.close()

    with asyncio.Runner(loop_factory=new_loop) as runner:
        runner.run(scenario())


@pytest.mark.parametrize("new_loop", _loops())
def test_a_failed_spawn_leaves_no_child(
    new_loop: Callable[[], asyncio.AbstractEventLoop], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Killed through the given callable, the only way under another uid."""

    async def refused(_self: ChildProcess) -> None:
        raise OSError("no pipe")

    monkeypatch.setattr(ChildProcess, "_connect_pipes", refused)
    killed: list[int] = []

    async def scenario() -> None:
        with pytest.raises(OSError, match="no pipe"):
            await ChildProcess.spawn(
                [sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"],
                kill=lambda pid: (killed.append(pid), os.killpg(pid, 9)),
                stdin=host_module.subprocess.PIPE,
                stdout=host_module.subprocess.PIPE,
                start_new_session=True,
            )

    with asyncio.Runner(loop_factory=new_loop) as runner:
        runner.run(scenario())
    assert len(killed) == 1


@pytest.mark.parametrize("new_loop", _loops())
def test_the_exit_is_reported_while_a_grandchild_holds_the_pipes(
    new_loop: Callable[[], asyncio.AbstractEventLoop],
) -> None:
    script = "import os, time\nif os.fork() == 0:\n    time.sleep(30)\nos._exit(7)\n"

    async def scenario() -> None:
        child = await ChildProcess.spawn(
            [sys.executable, "-I", "-S", "-c", script],
            kill=lambda pid: os.killpg(pid, 9),
            stdin=host_module.subprocess.PIPE,
            stdout=host_module.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(10):
                assert await child.wait() == 7
        finally:
            os.killpg(child.pid, 9)
            child.close_pipes()

    with asyncio.Runner(loop_factory=new_loop) as runner:
        runner.run(scenario())
