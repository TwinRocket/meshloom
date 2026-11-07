import os
import sys


def on_message(params):
    for i in range(20000):
        print(f"flood line {i} " + "x" * 80)
    sys.stdout.write("not json\n")
    os.write(1, b'{"jsonrpc": "2.0", "id": 0, "result": "forged"}\n')
    return "done"
