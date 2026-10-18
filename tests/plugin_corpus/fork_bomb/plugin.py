import os


def on_message(params):
    children = 0
    while children < 10000:
        if os.fork() == 0:
            os._exit(0)
        children += 1
    return children
