"""Mutation check of the plugin sandbox tests (docs/dev/plugins-security.md, "Tests").

Removes one guard at a time in the working tree (each file is restored right
after its run), runs the cases that pin it and expects them to fail. Run it
from the repository root, ideally as root on Linux with bubblewrap and the
`meshloom-plugin` account so every level runs:

    uv run python scripts/test/plugin_mutations.py

Exit 0 when every mutant is caught. The host pipe mutant is only caught on
Python 3.11 with bwrap (the hang it guards against needs both), so on other
setups it reports SURVIVED; CI's 3.11 backend job is the reference for it.
"""

import subprocess
import sys
from pathlib import Path

CORPUS = "tests/test_plugin_corpus.py"
FUZZ = "tests/test_plugin_rpc_fuzz.py"
RUNNER = "meshloom_plugin/runner.py"
RUNNER_TESTS = "tests/test_plugin_runner.py"

# (name, file, text to replace, replacement, test file, -k expression); several
# edits of one file: a list of (text, replacement) pairs and None.
MUTANTS = [
    ("os.fork refused", RUNNER, '        "os.fork",\n', "", CORPUS, "fork_bomb"),
    (
        "open read roots",
        RUNNER,
        "            roots = {read_roots!r}\n",
        '            roots = ("/",)\n',
        CORPUS,
        "read_etc_passwd or read_database or proc_server",
    ),
    # The hook's refusal sits behind the stub (`sys.addaudithook` replaced): each
    # one is pinned on its own.
    (
        "addaudithook refused",
        RUNNER,
        '        "sys.addaudithook",\n',
        "",
        RUNNER_TESTS,
        "hook_source",
    ),
    (
        "addaudithook stub",
        RUNNER,
        "    sys.addaudithook = _add_audit_hook_stub()\n",
        "",
        CORPUS,
        "add_audit_hook",
    ),
    (
        "RLIMIT_NPROC 0",
        RUNNER,
        "        wanted.append((resource.RLIMIT_NPROC, 0))\n",
        "",
        RUNNER_TESTS,
        "serves_under_each_isolation_level",
    ),
    ("gc events refused", RUNNER, '        "gc.get_objects",\n', "", CORPUS, "gc_objects"),
    ("setrlimit refused", RUNNER, '        "resource.setrlimit",\n', "", CORPUS, "rlimit_raise"),
    (
        "native suffix write",
        RUNNER,
        "            if path.endswith({native_suffixes!r}):\n",
        "            if False:\n",
        CORPUS,
        "write_native_suffix",
    ),
    (
        "native load roots",
        RUNNER,
        "        rest = args[1:2]\n",
        "        return\n        rest = args[1:2]\n",
        CORPUS,
        "native_load_from_work_dir",
    ),
    (
        "environ scrub",
        RUNNER,
        "        if key not in policy.ENV_KEYS:\n",
        "        if False:\n",
        CORPUS,
        "environ",
    ),
    (
        "RLIMIT_AS",
        RUNNER,
        '        wanted.append((resource.RLIMIT_AS, limits["address_space"]))\n',
        "        pass\n",
        CORPUS,
        "memory_bomb",
    ),
    (
        # Both 3.11 pipe guards: either one alone keeps the instance restarting.
        "host stdout drain and pipe close",
        "app/plugins/host.py",
        [
            ("                await self._discard_stdout()\n", ""),
            ("PIPE_CLOSE_SECONDS = 5.0\n", "PIPE_CLOSE_SECONDS = 1e9\n"),
        ],
        None,
        CORPUS,
        "huge_stdout_line and bwrap",
    ),
    (
        "cancel then kill",
        "app/plugins/host.py",
        '                    process.kill(\n                        f"event_cancel_ignored:',
        '                    (lambda *a: None)(\n                        f"event_cancel_ignored:',
        CORPUS,
        "infinite_loop",
    ),
    (
        "timer minimum",
        "app/plugins/capabilities/timer.py",
        "TIMER_MIN_SECONDS = 10\n",
        "TIMER_MIN_SECONDS = 0\n",
        CORPUS,
        "timer_too_short",
    ),
    (
        "archive symlink check",
        "app/plugins/package.py",
        "    if stat.S_ISLNK(mode):\n",
        "    if False:\n",
        CORPUS,
        "zip_symlink",
    ),
    (
        "rpc nesting cap",
        "meshloom_plugin/rpc.py",
        "    if _nesting_depth(text) > MAX_NESTING_DEPTH:\n",
        "    if False:\n",
        FUZZ,
        "nesting",
    ),
    (
        "rpc partial line bound",
        "meshloom_plugin/rpc.py",
        "        if len(self._buf) > self._bound():\n",
        "        if False:\n",
        FUZZ,
        "chunking or edges or real_child",
    ),
    (
        "rpc NaN refusal",
        "meshloom_plugin/rpc.py",
        "        obj = json.loads(text, parse_constant=_refuse_constant)\n",
        "        obj = json.loads(text)\n",
        FUZZ,
        "decode_returns",
    ),
]


def main() -> int:
    results = []
    for name, path, old, new, test, expr in MUTANTS:
        target = Path(path)
        source = mutated = target.read_text()
        for text, replacement in old if new is None else [(old, new)]:
            if mutated.count(text) != 1:
                print(f"{name}: the text to mutate is not found once in {path}", file=sys.stderr)
                return 2
            mutated = mutated.replace(text, replacement)
        target.write_text(mutated)
        try:
            run = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    test,
                    "-q",
                    "-n",
                    "4",
                    "-k",
                    f"({expr}) and not documented",
                ],
                capture_output=True,
                text=True,
                timeout=900,
            )
        finally:
            target.write_text(source)
        tail = [line for line in run.stdout.splitlines() if " passed" in line or " failed" in line]
        verdict = "KILLED" if run.returncode != 0 else "SURVIVED"
        results.append((name, verdict, tail[-1] if tail else ""))
    for row in results:
        print(" | ".join(row))
    return 0 if all(verdict == "KILLED" for _, verdict, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
