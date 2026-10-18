# Hostile plugin corpus (seed)

Each directory is a minimal plugin (`manifest.json` with only `entry`, which is
all the runner reads, and `plugin.py`) plus `case.json`: the events to send and
the expected outcome. `tests/test_plugin_runner.py` runs every case through a
real runner child under each isolation level this machine offers (`none`, the
hooks only, always; `bwrap` where its self-test passes; `setuid` as root with
the `meshloom-plugin` account) and checks that the child keeps serving
afterwards.

`expect` is one of `{"error_type": "<exception class>"}` (the handler's JSON-RPC
error), `{"result": <value>}` or `{"check": "<name>"}` (a check written in the
test, for outcomes that depend on the test's own state). An event may add
`"open_path": {"note": "...", "python_below": "3.12"}` (the version is optional):
a documented path the runner does not close. When the expectation fails there,
the test reports `xfail` instead of failing; when the path gets closed, it passes.
`"closed_by": ["bwrap", "setuid"]` with `"os_check": "<name>"` names the levels
whose OS isolation closes it: there the check (written in the test) runs instead
of the `xfail`, and must pass.

Seeded by MSHL-176 with the three demonstrated audit-hook bypasses (critique-1
§1.2), a `print()` flood, `os.environ`, inherited descriptors, thread start and
the open paths (`posixsubprocess_spec_rebuild`, `thread_start` on 3.11). MSHL-180 runs
it under every isolation level and closes `posixsubprocess_spec_rebuild` under
`bwrap` and `setuid` (`exec_escape`) and `thread_start` on 3.11 under `setuid`
(`nproc`). MSHL-191 consolidates the corpus. See docs/dev/plugins-security.md.
