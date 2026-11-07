def on_message(params):
    with open(params["path"], "rb") as fh:
        return fh.read(16).hex()
