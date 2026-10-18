import os


def on_message(params):
    pid = params.get("pid") or os.getppid()
    with open(f"/proc/{pid}/mem", "r+b") as fh:
        return fh.read(1).hex()
