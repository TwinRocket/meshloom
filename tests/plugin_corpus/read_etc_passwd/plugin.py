def on_message(params):
    with open("/etc/passwd") as fh:
        return fh.read(1)
