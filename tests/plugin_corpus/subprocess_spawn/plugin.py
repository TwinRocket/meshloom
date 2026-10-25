import os


def on_message(params):
    how = params["how"]
    if how == "subprocess":
        import subprocess

        return subprocess.run(["/bin/true"]).returncode
    if how == "system":
        return os.system("true")
    if how == "posix_spawn":
        return os.posix_spawn("/bin/true", ["/bin/true"], {})
    os.execv("/bin/true", ["/bin/true"])
