import os


def on_message(params):
    pid = params.get("pid") or os.getppid()
    with open(f"/proc/{pid}/environ", "rb") as fh:
        return fh.read(64).hex()
