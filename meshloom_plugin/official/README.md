# Bundled plugins

Packages shipped with Meshloom, installable offline from Settings → Extensions →
Installed → "Add a bundled extension" (`app/plugins/installer.py`). They are ordinary
plugins: same package format, same sandbox, same quotas and host guards as a third-party
plugin, validated with `origin = "bundled"` (ids in the reserved `meshloom.*` namespace).
Each directory is one package; a directory starting with `_` is ignored.

| Directory | Id | What it is |
|---|---|---|
| `echo/` | `meshloom.echo` | `!pling` → `Plong!`, the reference plugin (replaces the bots' example) |
| `auto_responder/` | `meshloom.auto-responder` | no-code rules: a trigger, a reply |

Check one from a source checkout:

```bash
uv run python -m meshloom_plugin.dev check meshloom_plugin/official/echo --origin bundled
```

A package's `version` changes only when its files change; `tests/test_official_plugins.py`
checks both packages.
