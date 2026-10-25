"""Print the failed and errored tests of a pytest JUnit XML as Markdown.

CI appends the output to $GITHUB_STEP_SUMMARY so a failure stays visible even
when the raw job log is truncated or a pytest-xdist worker dies mid-run.

Usage: junit_summary.py REPORT.xml
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

MAX_DETAIL_CHARS = 1500


def main(path: str) -> int:
    report = Path(path)
    if not report.is_file():
        print(
            f"**No JUnit report at `{path}`**: pytest did not finish "
            "(crash, timeout or kill). See the job log."
        )
        return 0
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for suite in suites:
        for key in totals:
            totals[key] += int(suite.get(key, 0))
    print(
        f"**pytest**: {totals['tests']} tests, {totals['failures']} failed, "
        f"{totals['errors']} errors, {totals['skipped']} skipped\n"
    )
    bad = []
    for case in root.iter("testcase"):
        for kind in ("failure", "error"):
            node = case.find(kind)
            if node is not None:
                name = f"{case.get('classname', '')}::{case.get('name', '')}"
                bad.append((kind, name, (node.get("message") or "")[:200], node.text or ""))
    for kind, name, message, text in bad:
        print(f"<details><summary><code>{name}</code> ({kind}): {message}</summary>\n")
        print("```text")
        print(text[-MAX_DETAIL_CHARS:].replace("```", "'''"))
        print("```\n</details>\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
