import sys


def on_message(params):
    started = []
    sys.modules["_thread"].start_new_thread(started.append, (True,))
    return "thread started"
