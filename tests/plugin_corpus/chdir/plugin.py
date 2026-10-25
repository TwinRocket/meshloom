import os


def on_message(params):
    os.chdir("/")
    return os.getcwd()
