def on_message(params):
    with open(params["path"], "w") as fh:
        fh.write("planted")
    return "written"
