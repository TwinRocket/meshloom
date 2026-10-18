"""Static attention scan of local plugin sources (app/plugins/attention.py)."""

import re
import textwrap
import time
from pathlib import Path

import pytest

from app.plugins import attention
from app.plugins.attention import scan_source

ROOT = Path(__file__).resolve().parents[1]

WEATHER_BOT = textwrap.dedent(
    """\
    import json
    import urllib.request

    URL = "https://api.open-meteo.com/v1/forecast?latitude=48.85&longitude=2.35"


    def bot(**kwargs):
        if kwargs.get("message_text") != "!meteo":
            return None
        with urllib.request.urlopen(URL) as response:
            data = json.load(response)
        return f"{data['current']['temperature_2m']} C"
    """
)


def _codes(report):
    return [(r.code, r.line, r.detail) for r in report.reasons]


def _default_bot_template() -> str:
    tsx = (ROOT / "frontend/src/components/settings/SettingsFanoutSection.tsx").read_text()
    match = re.search(r"const DEFAULT_BOT_CODE = `(.*?)`;", tsx, re.S)
    assert match, "DEFAULT_BOT_CODE not found: update this test with the template's new home"
    return match.group(1)


def test_open_meteo_bot_names_the_module_and_line_and_prefills_the_host():
    report = scan_source(WEATHER_BOT)
    assert _codes(report) == [("import_not_allowed", 2, "urllib.request")]
    assert report.http_hosts == ["api.open-meteo.com"]
    assert report.needs_attention
    assert report.to_json() == {
        "reasons": [
            {
                "code": "import_not_allowed",
                "line": 2,
                "detail": "urllib.request",
                "informative": False,
            }
        ],
        "http_hosts": ["api.open-meteo.com"],
        "truncated": False,
    }


def test_from_import_of_a_denied_submodule_is_flagged():
    report = scan_source("from urllib import parse, request\nfrom urllib.parse import quote\n")
    assert _codes(report) == [("import_not_allowed", 1, "urllib.request")]


def test_default_bot_template_gets_no_reason():
    report = scan_source(_default_bot_template())
    assert report.reasons == []
    assert report.http_hosts == []


def test_every_pattern_with_its_line():
    source = textwrap.dedent(
        """\
        import requests
        import threading
        from concurrent.futures import ThreadPoolExecutor
        import os, json
        from . import helpers
        seen = set()
        CACHE: dict = {}
        LIMIT = 3


        def bot(**kwargs):
            global LIMIT
            print("hi")
            open("/etc/passwd")
            exec("1")
            eval("1")
            __import__("socket")
            state = globals()["_bot_globals"]
            return "x" * 2 + "%s"
        """
    )
    source += f'LONG = "{"é" * 79}"\nSHORT = "{"é" * 78}"\n'
    report = scan_source(source)
    assert _codes(report) == [
        ("import_not_allowed", 1, "requests"),
        ("threading", 2, "threading"),
        ("threading", 3, "concurrent.futures"),
        ("module_state", 6, "seen"),
        ("module_state", 7, "CACHE"),
        ("global_statement", 12, "LIMIT"),
        ("print_call", 13, None),
        ("open_call", 14, None),
        ("exec_call", 15, None),
        ("eval_call", 16, None),
        ("import_call", 17, None),
        ("module_state", 18, "_bot_globals"),
        ("long_literal", 20, None),
    ]
    informative = {r.code for r in report.reasons if r.informative}
    assert informative == {"print_call", "module_state", "global_statement"}


def test_long_docstring_is_not_a_long_literal():
    doc = "x" * 400
    assert scan_source(f'"""{doc}"""\ndef bot(**kwargs):\n    """{doc}"""\n').reasons == []


def test_hosts_are_deduplicated_lowercased_and_only_those_the_permission_accepts():
    source = textwrap.dedent(
        """\
        A = "https://API.Example.com:8443/x?y=1"
        B = "see https://user:pw@api.example.com/z and https://other.example.org"
        C = "https://192.168.1.10/ https://localhost/ https://printer.local/"
        D = "https://store.meshloom.app/ https://8.8.8.8/dns http://plain.example.net/"
        """
    )
    assert scan_source(source).http_hosts == ["api.example.com", "other.example.org", "8.8.8.8"]


def test_syntax_error_is_one_reason():
    report = scan_source("def bot(:\n")
    assert _codes(report) == [("syntax_error", 1, None)]


def test_lists_are_the_runner_policy():
    from meshloom_plugin import policy

    assert attention.ALLOWED_IMPORTS is policy.ALLOWED_IMPORTS
    assert attention.DENIED_MODULES is policy.DENIED_MODULES


def test_process_and_compile_calls_are_flagged():
    source = textwrap.dedent(
        """\
        import os
        os.system("ls")
        os.popen("ls")
        os.kill(1, 9)
        os.path.join("a", "b")
        code = compile("1", "<x>", "eval")
        """
    )
    assert _codes(scan_source(source)) == [
        ("process_call", 2, "os.system"),
        ("process_call", 3, "os.popen"),
        ("process_call", 4, "os.kill"),
        ("compile_call", 6, None),
    ]
    flagged = _codes(scan_source("import subprocess\nsubprocess.run(['ls'])\n"))
    assert flagged == [
        ("import_not_allowed", 1, "subprocess"),
        ("process_call", 2, "subprocess.run"),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "x = " + "a+" * 500 + "a\n",
        "x = " + "a+" * 200_000 + "a\n",
        "x = a" + ".b" * 100_000 + "\n",
        "x = " + "(" * 10_000 + "1" + ")" * 10_000 + "\n",
        "x = " + "-" * 200_000 + "1\n",
        "x = " + "[" * 5_000 + "]" * 5_000 + "\n",
    ],
    ids=["chain-500", "chain-200k", "attr-100k", "parens-10k", "unary-200k", "lists-5k"],
)
def test_deep_but_valid_source_never_raises(source):
    report = scan_source(source)
    codes = {r.code for r in report.reasons}
    assert codes <= {"too_complex", "syntax_error"}


def test_deep_nesting_is_scanned_without_recursion():
    depth = 150
    source = "".join(" " * i + "if x:\n" for i in range(depth)) + " " * depth + "eval('1')\n"
    report = scan_source(source)
    assert [r.code for r in report.reasons] in (["eval_call"], ["too_complex"], ["syntax_error"])


def test_huge_and_node_heavy_sources_are_coded():
    assert _codes(scan_source("x = 1\n" * 300_000)) == [("source_too_large", 0, None)]
    assert _codes(scan_source("#" * (attention.MAX_SOURCE_BYTES + 1))) == [
        ("source_too_large", 0, None)
    ]
    many = "x = [" + "1," * 250_000 + "]\n"
    assert _codes(scan_source(many)) == [("too_complex", 0, None)]


def test_host_extraction_is_bounded_and_fast():
    # 200k URLs: too large for a source, so the extractor is driven directly.
    urls = " ".join(f"https://h{i}.example.com/p" for i in range(200_000))
    scanner = attention._Scanner(set())
    start = time.monotonic()
    scanner._hosts_from(urls)
    assert time.monotonic() - start < 1.0
    assert len(scanner.hosts) == attention.MAX_PREFILL_HOSTS
    assert scanner.truncated

    start = time.monotonic()
    assert _codes(scan_source(f"X = {urls!r}\n")) == [("source_too_large", 0, None)]
    assert time.monotonic() - start < 1.0

    # One host repeated: the URL count bound stops the scan.
    same = " ".join(["https://h.example.com/"] * 40_000)
    start = time.monotonic()
    report = scan_source(f"X = {same!r}\n")
    assert time.monotonic() - start < 1.0
    assert report.http_hosts == ["h.example.com"]
    assert report.truncated
    assert report.to_json()["truncated"] is True
