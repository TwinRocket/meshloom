import sys


def on_message(params):
    seen = []
    sys.addaudithook(lambda event, args: seen.append(event))
    compile("1", "<corpus>", "eval")
    return bool(seen)
