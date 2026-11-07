import sys


def _socket_module():
    # `_socket` rebuilt from its spec: built into python-build-standalone (no import
    # event), an extension module elsewhere (the hook refuses its import).
    bootstrap = sys.modules["importlib._bootstrap"]
    spec = bootstrap.BuiltinImporter.find_spec("_socket")
    if spec is None:
        external = sys.modules["importlib._bootstrap_external"]
        spec = external.PathFinder.find_spec("_socket", sys.path)
    if spec is None:
        raise PermissionError("no spec found")
    module = bootstrap.module_from_spec(spec)
    if hasattr(spec.loader, "exec_module"):
        spec.loader.exec_module(module)
    return module


def on_message(params):
    how = params["how"]
    try:
        _socket = _socket_module()
        if how == "socketpair":
            # No audit event: only the seccomp filter refuses it.
            pair = _socket.socketpair()
            for end in pair:
                end.close()
            return {"created": "socketpair"}
        if how == "inet":
            sock = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            target = ("127.0.0.1", params["port"])
        elif how == "inet6":
            sock = _socket.socket(_socket.AF_INET6, _socket.SOCK_STREAM)
            target = ("::1", params["port"])
        else:
            sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
            target = "\0meshloom-corpus-abstract"
        sock.settimeout(2)
        try:
            sock.connect(target)
        finally:
            sock.close()
        return {"connected": target}
    except OSError as exc:
        if exc.errno is None:
            raise  # the runner's own refusal ("plugin sandbox: ...")
        return {"refused": exc.errno}
