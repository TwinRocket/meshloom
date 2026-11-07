"""`Channel.call_sync`: a blocking host call for synchronous plugin code (legacy bots).

Same harness as tests/test_plugin_runner.py: a real child, the test plays the server.
"""

import time
from pathlib import Path
from typing import Any

import pytest
from test_plugin_runner import Runner, make_plugin

from meshloom_plugin import policy, rpc


@pytest.fixture
def work(tmp_path: Path) -> Path:
    return tmp_path.resolve()  # macOS temp dirs go through the /var -> /private/var link


PLUGIN = """
    import meshloom_plugin
    from meshloom_plugin.errors import HostError

    def on_message(params):
        host = meshloom_plugin.host()
        case = params["case"]
        if case == "ok":
            return host.call_sync("x.get", {"k": 1})
        if case == "timeout":
            try:
                return host.call_sync("x.slow", None, timeout=0.3)
            except HostError as exc:
                return exc.error
        if case == "error":
            try:
                return host.call_sync("x.bad")
            except HostError as exc:
                return exc.error
        if case == "closed":
            try:
                return host.call_sync("x.get")
            except HostError as exc:
                return exc.error

    def on_health(params):
        return "health"

    async def on_timer(params):
        import asyncio
        await asyncio.sleep(30)
"""


@pytest.fixture
def child(work: Path):
    runner = Runner(make_plugin(work, PLUGIN), work)
    yield runner
    runner.close()


def read_request(child: Runner) -> dict[str, Any]:
    message = child.read_message()
    assert "method" in message, message
    return message


def test_answer_comes_back_as_the_result(child: Runner) -> None:
    msg_id = child.start("event.message", {"case": "ok"})
    request = read_request(child)
    assert (request["method"], request["params"]) == ("x.get", {"k": 1})
    child.send(rpc.result(request["id"], {"v": 7}))
    assert child.wait_for(msg_id)["result"] == {"v": 7}


def test_events_that_arrive_meanwhile_wait_for_the_call_to_end(child: Runner) -> None:
    msg_id = child.start("event.message", {"case": "ok"})
    request = read_request(child)
    health_id = child.start("event.health", {})
    time.sleep(0.2)  # the loop is blocked: the event is read and held back, not answered
    child.send(rpc.result(request["id"], "done"))
    first = child.read_message()
    second = child.read_message()
    by_id = {first["id"]: first, second["id"]: second}
    assert by_id[msg_id]["result"] == "done"
    assert by_id[health_id]["result"] == "health"
    assert child.event({"case": "ok"}, on_request=lambda m: rpc.result(m["id"], 1))["result"] == 1


def test_host_error_is_raised_to_the_caller(child: Runner) -> None:
    msg_id = child.start("event.message", {"case": "error"})
    request = read_request(child)
    child.send(rpc.error(request["id"], -32010, "no", {"code": "permission_denied"}))
    result = child.wait_for(msg_id)["result"]
    assert result["data"] == {"code": "permission_denied"}


def test_timeout_then_a_late_answer_is_ignored(child: Runner) -> None:
    started = time.monotonic()
    msg_id = child.start("event.message", {"case": "timeout"})
    request = read_request(child)
    result = child.wait_for(msg_id)["result"]
    assert result["code"] == "timeout"
    assert time.monotonic() - started < 5
    child.send(rpc.result(request["id"], "late"))
    assert child.event({"case": "ok"}, on_request=lambda m: rpc.result(m["id"], 2))["result"] == 2


def test_cancel_during_the_call_cancels_the_handler(child: Runner) -> None:
    msg_id = child.start("event.message", {"case": "ok"})
    read_request(child)
    cancel_id = child.start("event.cancel", {"id": msg_id})
    replies = {m["id"]: m for m in (child.read_message(), child.read_message())}
    assert replies[cancel_id]["result"] is True
    assert replies[msg_id]["error"]["code"] == rpc.CANCELLED
    assert child.event({"case": "ok"}, on_request=lambda m: rpc.result(m["id"], 3))["result"] == 3


def test_held_back_events_are_bounded_and_the_overflow_is_refused(child: Runner) -> None:
    msg_id = child.start("event.message", {"case": "ok"})
    request = read_request(child)
    ids = [child.start("event.health", {}) for _ in range(200)]
    time.sleep(0.5)
    child.send(rpc.result(request["id"], "done"))
    seen: dict[Any, dict[str, Any]] = {}
    while len(seen) < 201:
        message = child.read_message(timeout=30)
        assert message["id"] not in seen
        seen[message["id"]] = message
    assert seen[msg_id]["result"] == "done"
    refused = [i for i in ids if seen[i].get("error", {}).get("code") == rpc.OVERLOADED]
    assert len(refused) >= 200 - policy.MAX_CONCURRENT_HANDLERS - 128
    assert all(seen[i]["result"] == "health" for i in ids if i not in refused)


def test_oversized_line_while_blocked_ends_the_child(child: Runner) -> None:
    child.start("event.message", {"case": "closed"})
    read_request(child)
    child.write(b"x" * (rpc.MAX_MESSAGE_BYTES + 10) + b"\n")
    assert child.proc.wait(timeout=10) == 2


def test_the_stream_closing_while_blocked_ends_the_child(child: Runner) -> None:
    child.start("event.message", {"case": "closed"})
    read_request(child)
    child._stdin.close()  # stdin EOF: the server is gone
    assert child.proc.wait(timeout=10) == 0


def test_cancel_of_another_task_is_applied_at_once_even_past_the_cap(child: Runner) -> None:
    slow = child.start("event.timer", {})
    time.sleep(0.3)
    msg_id = child.start("event.message", {"case": "ok"})
    request = read_request(child)
    health = [child.start("event.health", {}) for _ in range(150)]  # fill the held-back buffer
    cancel_id = child.start("event.cancel", {"id": slow})
    seen: dict[Any, dict[str, Any]] = {}
    while cancel_id not in seen:  # answered at once, though the loop is blocked
        message = child.read_message(timeout=10)
        seen[message["id"]] = message
    assert seen[cancel_id]["result"] is True
    child.send(rpc.result(request["id"], "done"))
    while not {slow, msg_id, *health} <= seen.keys():
        message = child.read_message(timeout=30)
        seen[message["id"]] = message
    assert seen[slow]["error"]["code"] == rpc.CANCELLED  # delivered, not dropped
    assert seen[msg_id]["result"] == "done"
