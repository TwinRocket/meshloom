"""The plugin child's seccomp filter and capability drop (`meshloom_plugin.seccomp`).

The filter is the guard that refuses task creation and exec to a child running as
root, which `RLIMIT_NPROC` does not bind (MSHL-323). The probes below install it
exactly as the runner does (`_libc_prctl`, `PR_SET_NO_NEW_PRIVS`, then `apply`) in
a fresh interpreter and try every way out; the root CI job runs them as root.
Stdlib and pytest only (`--noconftest` works).
"""

import json
import subprocess
import sys
import textwrap

import pytest

from meshloom_plugin import seccomp

LINUX = sys.platform.startswith("linux")
APP_ROOT = str(__import__("pathlib").Path(__file__).resolve().parent.parent)

PROBE = textwrap.dedent(
    """
    import errno, json, os, sys, threading
    sys.path.insert(0, {app_root!r})
    from meshloom_plugin import runner, seccomp
    prctl = runner._libc_prctl()
    assert prctl(runner.PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0
    abi = seccomp.apply(prctl, required=True)
    out = {{"abi": abi, "euid": os.geteuid()}}

    def attempt(name, fn):
        try:
            fn()
        except OSError as exc:
            out[name] = errno.errorcode.get(exc.errno, exc.errno)
        except RuntimeError as exc:
            out[name] = "RuntimeError: " + str(exc)
        else:
            out[name] = "allowed"

    def fork():
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)

    def thread():
        t = threading.Thread(target=lambda: None)
        t.start()
        t.join()

    attempt("fork", fork)
    attempt("execv", lambda: os.execv(sys.executable, [sys.executable, "-c", "pass"]))
    attempt("posix_spawn", lambda: os.waitpid(os.posix_spawn(sys.executable, [sys.executable, "-c", "pass"], {{}}), 0))
    attempt("thread", thread)
    import subprocess
    attempt("subprocess", lambda: subprocess.run([sys.executable, "-c", "pass"]))
    out["caps"] = seccomp.capability_sets()
    out["getpid"] = os.getpid() > 0
    print(json.dumps(out))
    """
)


def test_native_abi_follows_the_interpreter_not_the_kernel() -> None:
    assert seccomp.native_abi("x86_64", 8) == "x86_64"
    assert seccomp.native_abi("x86_64", 4) == "i386"
    assert seccomp.native_abi("i686", 4) == "i386"
    assert seccomp.native_abi("aarch64", 8) == "aarch64"
    # armhf userland on an arm64 kernel (Raspberry Pi OS 32-bit, 64-bit kernel)
    assert seccomp.native_abi("aarch64", 4) == "arm"
    assert seccomp.native_abi("armv7l", 4) == "arm"
    assert seccomp.native_abi("riscv64", 8) == "riscv64"
    assert seccomp.native_abi("s390x", 8) is None
    assert seccomp.native_abi("mips", 4) is None


@pytest.mark.parametrize("abi", sorted(seccomp.TABLES))
def test_filter_denies_exactly_the_listed_syscalls(abi: str) -> None:
    """Interpret the BPF program: denied numbers and foreign arches get EPERM."""
    arch, numbers = seccomp.TABLES[abi]
    prog = seccomp.build_filter(abi)
    assert len(prog) < 64

    def run(arch_value: int, nr: int) -> int:
        acc = 0
        pc = 0
        while True:
            code, jt, jf, k = prog[pc]
            if code == 0x20:
                acc = {0: nr, 4: arch_value}[k]
                pc += 1
            elif code == 0x15:
                pc += 1 + (jt if acc == k else jf)
            elif code == 0x35:
                pc += 1 + (jt if acc >= k else jf)
            elif code == 0x06:
                return k
            else:
                raise AssertionError(f"unexpected opcode {code:#x}")

    deny = seccomp.SECCOMP_RET_ERRNO | seccomp.EPERM
    for name, nr in numbers.items():
        assert run(arch, nr) == deny, name
    for nr in range(0, 600):
        if nr not in numbers.values():
            assert run(arch, nr) == seccomp.SECCOMP_RET_ALLOW, nr
    assert run(arch ^ 0x1, 0) == deny
    if abi == "x86_64":
        assert run(arch, 0x40000000 | 1) == deny  # x32 write
    assert {"clone", "clone3", "execve", "execveat"} <= set(numbers)


@pytest.mark.skipif(not LINUX, reason="seccomp is Linux only")
def test_child_cannot_fork_exec_or_thread_once_filtered() -> None:
    if seccomp.native_abi() is None:
        pytest.skip("no seccomp table for this architecture")
    proc = subprocess.run(
        [sys.executable, "-I", "-S", "-c", PROBE.format(app_root=APP_ROOT)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["abi"] == seccomp.native_abi()
    for name in ("fork", "execv", "posix_spawn", "subprocess"):
        assert out[name] == "EPERM", (name, out)
    assert out["thread"].startswith("RuntimeError"), out
    assert out["getpid"] is True
    if out["euid"] != 0:
        # A non-root child keeps no capability (ambient ones included).
        assert out["caps"] == dict.fromkeys(out["caps"], 0), out["caps"]


def test_unknown_architecture_is_fatal_only_when_required(monkeypatch: pytest.MonkeyPatch) -> None:
    if not LINUX:
        assert seccomp.apply(lambda *a: 0, required=True) is None
        return
    monkeypatch.setattr(seccomp, "native_abi", lambda: None)
    monkeypatch.setattr(seccomp, "drop_capabilities", lambda prctl: None)
    assert seccomp.apply(lambda *a: 0, required=False) is None
    with pytest.raises(seccomp.SeccompError):
        seccomp.apply(lambda *a: 0, required=True)
