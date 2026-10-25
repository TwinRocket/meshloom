import os


def on_message(params):
    return os.listdir(params["path"])
