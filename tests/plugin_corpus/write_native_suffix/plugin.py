import os


def on_message(params):
    base = os.getcwd()
    if params["how"] == "open":
        with open(base + "/evil.so", "wb") as fh:
            fh.write(b"x")
        return "written"
    with open(base + "/evil.txt", "wb") as fh:
        fh.write(b"x")
    os.rename(base + "/evil.txt", base + "/evil.so")
    return "renamed"
