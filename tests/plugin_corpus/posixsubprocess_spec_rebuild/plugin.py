import sys


def on_message(params):
    bootstrap = sys.modules["importlib._bootstrap"]
    spec = bootstrap.BuiltinImporter.find_spec("_posixsubprocess")
    if spec is None:
        external = sys.modules["importlib._bootstrap_external"]
        spec = external.PathFinder.find_spec("_posixsubprocess", sys.path)
    if spec is None:
        raise PermissionError("no spec found")
    module = bootstrap.module_from_spec(spec)
    if hasattr(spec.loader, "exec_module"):
        spec.loader.exec_module(module)
    return {"origin": spec.origin, "fork_exec": hasattr(module, "fork_exec")}
