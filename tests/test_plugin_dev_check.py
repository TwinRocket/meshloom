"""`python -m meshloom_plugin.dev check`: the server's validators plus a sandbox dry run."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.plugins.errors import PluginValidationError
from app.plugins.manifest import parse_manifest
from meshloom_plugin import dev

ROOT = Path(__file__).resolve().parent.parent
ECHO = ROOT / "tests/plugin_dev/echo"
VECTORS = json.loads((ROOT / "docs/dev/plugins/vectors/manifest.json").read_text("utf-8"))
INVALID = [v for v in VECTORS["vectors"] if not v["valid"]]


@pytest.fixture
def plugin(tmp_path):
    dest = tmp_path / "plugin"
    shutil.copytree(ECHO, dest)
    return dest


def rewrite_manifest(plugin: Path, **changes):
    path = plugin / "manifest.json"
    manifest = json.loads(path.read_text("utf-8"))
    manifest.update(changes)
    path.write_text(json.dumps(manifest), "utf-8")


def codes(report, stage=None):
    return [f.code for f in report.errors if stage in (None, f.stage)]


def test_echo_passes_and_prints_the_captured_sends(plugin, capsys):
    report = dev.check_directory(plugin)
    assert report.ok, report.errors
    assert report.ran
    assert [s["params"] for s in report.sends] == [{"to": "reply", "text": "!test hello"}]
    assert dev.main(["check", str(plugin)]) == 0
    out = capsys.readouterr().out
    assert 'messages.send {"to": "reply", "text": "!test hello"}' in out
    assert out.strip().endswith("OK")


def test_json_output(plugin, capsys):
    assert dev.main(["check", str(plugin), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["plugin"] == {"id": "acme.echo", "version": "1.0.0"}
    assert data["errors"] == []
    assert data["dry_run"]["calls"][0]["method"] == "messages.send"


@pytest.mark.parametrize("vector", INVALID, ids=[v["name"] for v in INVALID])
def test_every_manifest_rule_fails_with_the_servers_messages(plugin, vector):
    (plugin / "manifest.json").write_text(json.dumps(vector["manifest"]), "utf-8")
    origin = vector.get("origin", "third_party")
    report = dev.check_directory(plugin, origin=origin)
    with pytest.raises(PluginValidationError) as server:
        parse_manifest(vector["manifest"], origin=origin)
    assert not report.ok
    assert not report.ran
    assert [(f.code, f.message) for f in report.errors] == [
        (i.code, i.message) for i in server.value.issues
    ]


def test_package_rules_fail_with_the_servers_messages(plugin):
    (plugin / "LICENSE").unlink()
    report = dev.check_directory(plugin)
    assert not report.ok and not report.ran
    assert codes(report, "package") == ["package_missing_file"]
    assert report.errors[0].message == "LICENSE is required"
    (plugin / "LICENSE").write_text("MIT\n", "utf-8")
    (plugin / "extra.so").write_bytes(b"\x7fELF")
    assert codes(dev.check_directory(plugin)) == ["package_native_extension"]


def test_entry_missing_and_config_schema_rules(plugin):
    rewrite_manifest(plugin, entry="other.py", config_schema="config.schema.json")
    report = dev.check_directory(plugin)
    assert sorted(f.path for f in report.errors) == ["config.schema.json", "other.py"]


def test_symlink_is_refused(plugin):
    (plugin / "link.py").symlink_to(plugin / "plugin.py")
    report = dev.check_directory(plugin)
    assert codes(report) == ["package_unreadable"]


def test_not_a_directory(tmp_path):
    assert codes(dev.check_directory(tmp_path / "nope")) == ["not_a_directory"]


def test_plugin_raising_in_on_message_fails(plugin):
    (plugin / "plugin.py").write_text(
        "def on_message(event):\n    raise RuntimeError('boom')\n", "utf-8"
    )
    report = dev.check_directory(plugin)
    assert codes(report) == ["on_message_failed"]
    assert "RuntimeError: boom" in report.errors[0].message
    assert "boom" in report.stderr


def test_plugin_raising_in_init_fails(plugin):
    (plugin / "plugin.py").write_text(
        "def on_init(params):\n    raise ValueError('no init')\n", "utf-8"
    )
    assert codes(dev.check_directory(plugin)) == ["init_failed"]


def test_plugin_failing_to_load_reports_the_runner_error(plugin):
    (plugin / "plugin.py").write_text("import socket\n", "utf-8")
    report = dev.check_directory(plugin)
    assert codes(report) == ["entry_load_failed"]
    assert "socket" in report.errors[0].message


def test_plugin_that_never_answers_times_out(plugin, monkeypatch):
    monkeypatch.setattr(dev, "EVENT_TIMEOUT", 0.5)
    (plugin / "plugin.py").write_text(
        "import asyncio\n\nasync def on_message(event):\n    await asyncio.sleep(60)\n", "utf-8"
    )
    report = dev.check_directory(plugin)
    assert codes(report) == ["run_failed"]
    assert "no answer" in report.errors[0].message


def test_static_findings_are_warnings_unless_strict(plugin):
    (plugin / "plugin.py").write_text(
        "def on_message(event):\n    print('hi')\n    exec('1')\n", "utf-8"
    )
    report = dev.check_directory(plugin)
    assert report.ok
    assert {w.code for w in report.warnings} == {"print_call", "exec_call"}
    assert next(w for w in report.warnings if w.code == "exec_call").line == 3
    strict = dev.check_directory(plugin, strict=True)
    assert codes(strict) == ["exec_call"]
    assert [w.code for w in strict.warnings] == ["print_call"]


def test_vendor_is_not_scanned(plugin):
    (plugin / "vendor").mkdir()
    (plugin / "vendor/lib.py").write_text("exec('1')\n", "utf-8")
    assert dev.check_directory(plugin).warnings == []


def test_exit_status_one_on_failure(plugin, capsys):
    (plugin / "README.md").unlink()
    assert dev.main(["check", str(plugin)]) == 1
    assert "FAILED" in capsys.readouterr().out


def test_cli_module_runs_and_exits_nonzero(plugin):
    run = subprocess.run(
        [sys.executable, "-m", "meshloom_plugin.dev", "check", str(plugin), "--json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["ok"] is True


def test_importing_dev_pulls_in_neither_fastapi_nor_the_database():
    code = (
        "import sys, meshloom_plugin.dev\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('fastapi', 'starlette', 'aiosqlite')"
        " or m in ('app.database', 'app.main', 'app.config')]\n"
        "assert not bad, bad\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60
    )
    assert run.returncode == 0, run.stderr
