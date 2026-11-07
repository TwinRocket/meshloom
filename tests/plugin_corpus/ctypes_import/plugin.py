def on_message(params):
    module = __import__(params["name"])
    return repr(module)
