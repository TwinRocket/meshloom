import sys


def on_message(params):
    if params["how"] == "import":
        gc = __import__("gc")
    else:
        bootstrap = sys.modules["importlib._bootstrap"]
        spec = bootstrap.BuiltinImporter.find_spec("gc")
        gc = bootstrap.module_from_spec(spec)
    return len(gc.get_objects())
