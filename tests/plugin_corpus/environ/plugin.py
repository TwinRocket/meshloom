import os


def on_message(params):
    return dict(os.environ)
