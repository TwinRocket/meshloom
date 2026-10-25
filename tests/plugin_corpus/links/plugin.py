import os


def on_message(params):
    target = os.getcwd() + "/link"
    if params["how"] == "symlink":
        os.symlink("/etc/passwd", target)
    else:
        os.link("/etc/passwd", target)
    return "linked"
