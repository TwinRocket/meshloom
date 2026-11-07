import sys


def on_message(params):
    if params["how"] == "import":
        import zipimport

        return repr(zipimport)
    return "zipimport" in sys.modules
