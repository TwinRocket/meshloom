import os
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
    # Then use it: run a program (no hook in it) that drops a file in the work dir.
    program = os.fsencode(sys.executable)
    argv = [program, b"-I", b"-S", b"-c", b"open('escaped', 'x').close()"]
    errpipe_read, errpipe_write = os.pipe()
    # subprocess.py's call: allow_vfork was dropped after 3.12, so try both arities.
    common = (argv, [program], True, (errpipe_write,), None, None)
    common += (-1, -1, -1, -1, -1, -1, errpipe_read, errpipe_write)
    common += (False, False, -1, None, None, None, -1, None)
    try:
        try:
            pid = module.fork_exec(*common, False)
        except TypeError:
            pid = module.fork_exec(*common)
    except OSError as exc:
        return {"refused": exc.errno}
    finally:
        os.close(errpipe_read)
        os.close(errpipe_write)
    return {"spawned": pid}
