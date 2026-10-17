"""Pin the meshcore log calls that ``app/log_redaction.py`` was written against.

The redaction rules key on exact meshcore message prefixes (``Sending raw
data``, ``sending pkt``, ``Received data``, ``Dispatching event``, the parse
and event-handler errors). A meshcore bump that adds, moves or rewords a log
call with a non-constant argument fails this test, so somebody reviews the
rules before a frame dump slips through.

Each call is fingerprinted as module + level + its whitespace-normalised source
text (``ast.get_source_segment``, raw source: no line numbers, no
``ast.dump``/``ast.unparse``, whose output differs between the Python versions
CI runs). The test also pins every ``getLogger(...)`` argument: a module moved
to a child logger (``"meshcore.reader"``) would bypass the logger-level filter.

After reviewing ``app/log_redaction.py`` against a new meshcore, refresh the
fixture with::

    uv run python tests/test_meshcore_log_sites.py --write
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import meshcore

FIXTURE = Path(__file__).parent / "fixtures" / "meshcore_log_sites.json"
LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "fatal", "log"}
LOGGER_NAMES = {"logger", "logging", "log", "_logger", "LOGGER"}


def _package_root() -> Path:
    return Path(meshcore.__file__).parent


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _modules() -> list[tuple[str, str, ast.Module]]:
    root = _package_root()
    modules = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        modules.append((path.relative_to(root).as_posix(), source, ast.parse(source)))
    return modules


def _is_constant(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant)


def collect_log_sites() -> list[dict[str, str]]:
    sites: list[dict[str, str]] = []
    for module, source, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            func = node.func
            if func.attr not in LEVELS or not isinstance(func.value, ast.Name):
                continue
            if func.value.id not in LOGGER_NAMES:
                continue
            arguments = [*node.args, *(keyword.value for keyword in node.keywords)]
            if all(_is_constant(argument) for argument in arguments):
                continue
            segment = ast.get_source_segment(source, node) or ""
            sites.append({"module": module, "level": func.attr, "call": _normalise(segment)})
    sites.sort(key=lambda site: (site["module"], site["level"], site["call"]))
    return sites


def collect_get_logger_arguments() -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for module, source, tree in _modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name != "getLogger":
                continue
            argument = (
                _normalise(ast.get_source_segment(source, node.args[0]) or "")
                if node.args
                else "<root>"
            )
            found.append({"module": module, "argument": argument})
    return found


def test_meshcore_log_sites_match_the_reviewed_set():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = collect_log_sites()
    as_key = lambda site: (site["module"], site["level"], site["call"])  # noqa: E731
    added = sorted(set(map(as_key, actual)) - set(map(as_key, expected["log_sites"])))
    removed = sorted(set(map(as_key, expected["log_sites"])) - set(map(as_key, actual)))
    assert not added and not removed, (
        "meshcore log calls changed: review app/log_redaction.py, then refresh "
        f"{FIXTURE.name}.\nadded: {added}\nremoved: {removed}"
    )
    assert expected["meshcore_version"]


def test_every_meshcore_logger_is_the_meshcore_logger():
    for entry in collect_get_logger_arguments():
        if entry["argument"] == "__name__":
            # Only the package itself, whose __name__ is "meshcore".
            assert entry["module"] == "__init__.py", entry
        else:
            assert entry["argument"] in ('"meshcore"', "'meshcore'"), entry


def test_the_redaction_prefixes_still_exist_in_meshcore():
    calls = " ".join(site["call"] for site in collect_log_sites())
    for prefix in (
        "Sending cli command: ",
        "Sending raw data: ",
        "sending pkt : ",
        "Received data: ",
        "Received private key response: ",
        "Unhandled data received ",
        "Dispatching event: ",
        "handle_rx parse error: %s: %s | raw=%s\\n%s",
        "Error in event handler for ",
    ):
        assert prefix in calls, prefix


def _write_fixture() -> None:
    from importlib.metadata import version

    payload = {
        "meshcore_version": version("meshcore"),
        "log_sites": collect_log_sites(),
    }
    FIXTURE.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {len(payload['log_sites'])} sites to {FIXTURE}")


if __name__ == "__main__":
    if "--write" in sys.argv:
        _write_fixture()
    else:
        print(__doc__)
