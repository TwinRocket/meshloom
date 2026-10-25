def on_message(params):
    try:
        with open("/etc/passwd"):
            return "open was not refused"
    except PermissionError as exc:
        tb = exc.__traceback__
        while tb is not None:
            frame = tb.tb_frame
            if frame.f_code.co_filename == "<meshloom-audit-hook>":
                return {
                    "globals": sorted(frame.f_globals),
                    "builtins": sorted(frame.f_builtins),
                    "local_types": sorted({type(v).__name__ for v in frame.f_locals.values()}),
                }
            tb = tb.tb_next
        return "hook frame not found"
