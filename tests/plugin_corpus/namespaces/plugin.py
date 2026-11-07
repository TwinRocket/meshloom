import os


def on_message(params):
    if params["how"] == "unshare":
        os.unshare(os.CLONE_NEWUSER)
    else:
        os.setns(0, 0)
    return "namespace changed"
