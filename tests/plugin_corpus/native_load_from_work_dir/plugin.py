import os
import sys


def on_message(params):
    path = os.getcwd() + "/libevil.txt"
    with open(path, "wb") as fh:
        fh.write(b"\x7fELF not a real library")
    external = sys.modules["importlib._bootstrap_external"]
    bootstrap = sys.modules["importlib._bootstrap"]
    spec = bootstrap.spec_from_loader("evil", external.ExtensionFileLoader("evil", path))
    try:
        bootstrap.module_from_spec(spec)
    except ImportError:
        return "dlopen attempted"
    return "loaded"
