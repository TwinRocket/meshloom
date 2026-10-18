import os


def on_message(params):
    os.environ["MESHLOOM_CORPUS"] = "1"
    return "set"
