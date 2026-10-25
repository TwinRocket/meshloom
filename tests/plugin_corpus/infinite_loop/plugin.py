def _ping(params):
    return "pong" if params.get("ping") else None


def on_message(params):
    if _ping(params):
        return "pong"
    while True:
        pass
