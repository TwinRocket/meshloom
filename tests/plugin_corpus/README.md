# Hostile plugin corpus (seed)

Each directory is a minimal plugin (`manifest.json` with only `entry`, which is
all the runner reads, and `plugin.py`) plus `case.json`: the events to send and
the expected outcome. `tests/test_plugin_runner.py` runs every case through a
real runner child with the "none" isolation level (hooks only) and checks that
the child keeps serving afterwards.

`expect` is one of `{"error_type": "<exception class>"}` (the handler's JSON-RPC
error), `{"result": <value>}` or `{"check": "<name>"}` (a check written in the
test, for outcomes that depend on the test's own state).

Seeded by MSHL-176 with the three demonstrated audit-hook bypasses (critique-1
§1.2), a `print()` flood, `os.environ` and inherited descriptors. MSHL-191
consolidates the corpus and runs it under every isolation level; the expected
outcome may then differ per level. See docs/dev/plugins-security.md.
