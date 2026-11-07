import os


def on_message(params):
    if params["how"] == "relative":
        path = "../../escape.txt"
    else:
        path = os.getcwd() + "/../escape.txt"
    with open(path, "w") as fh:
        fh.write("x")
    return path
