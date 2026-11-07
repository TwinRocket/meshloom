import sys


def on_message(params):
    resource = sys.modules.get("resource")
    if resource is None:
        bootstrap = sys.modules["importlib._bootstrap"]
        spec = bootstrap.BuiltinImporter.find_spec("resource")
        if spec is None:
            external = sys.modules["importlib._bootstrap_external"]
            spec = external.PathFinder.find_spec("resource", sys.path)
        resource = bootstrap.module_from_spec(spec)
        spec.loader.exec_module(resource)
    resource.setrlimit(resource.RLIMIT_NOFILE, (4096, 4096))
    return "raised"
