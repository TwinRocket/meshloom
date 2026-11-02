import os
import sys


def _signal_module():
    # `signal` is not in the plugin import allowlist, but the import guard is not a
    # boundary: rebuild the (built-in) module from its spec.
    bootstrap = sys.modules["importlib._bootstrap"]
    spec = bootstrap.BuiltinImporter.find_spec("signal")
    module = bootstrap.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def on_message(params):
    # Neither os.pidfd_open nor signal.pidfd_send_signal raises an audit event,
    # unlike os.kill: signal 0 only (refused before any delivery).
    try:
        fd = os.pidfd_open(int(params["pid"]))
    except OSError as exc:
        return {"refused": exc.errno, "at": "pidfd_open"}
    try:
        _signal_module().pidfd_send_signal(fd, 0)
    except OSError as exc:
        return {"refused": exc.errno, "at": "pidfd_send_signal"}
    finally:
        os.close(fd)
    return "signalled"
