"""scripts/ci/junit_summary.py never fails the CI step, even on a broken report."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ci" / "junit_summary.py"
spec = importlib.util.spec_from_file_location("junit_summary", SCRIPT)
assert spec and spec.loader
junit_summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(junit_summary)


def test_missing_report(tmp_path, capsys):
    assert junit_summary.main(str(tmp_path / "none.xml")) == 0
    assert "No JUnit report" in capsys.readouterr().out


def test_empty_or_truncated_report_prints_the_missing_message(tmp_path, capsys):
    for content in ("", '<testsuites><testsuite tests="3"><testcase name="a"'):
        report = tmp_path / "r.xml"
        report.write_text(content)
        assert junit_summary.main(str(report)) == 0
        out = capsys.readouterr().out
        assert "No JUnit report" in out
        assert "probably crashed" in out


def test_valid_report(tmp_path, capsys):
    report = tmp_path / "r.xml"
    report.write_text(
        '<testsuites><testsuite tests="1" failures="1"><testcase classname="t" name="a">'
        '<failure message="boom">trace</failure></testcase></testsuite></testsuites>'
    )
    assert junit_summary.main(str(report)) == 0
    assert "1 failed" in capsys.readouterr().out
