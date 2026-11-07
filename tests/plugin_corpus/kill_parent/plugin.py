import os


def on_message(params):
    if params["how"] == "kill":
        os.kill(os.getppid(), 0)
    else:
        os.killpg(os.getpgid(0), 0)
    return "signalled"
