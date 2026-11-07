import os


def on_message(params):
    seen = []
    for fd in range(256):
        try:
            st = os.fstat(fd)
        except OSError:
            continue
        seen.append([fd, st.st_dev, st.st_ino])
    return seen
