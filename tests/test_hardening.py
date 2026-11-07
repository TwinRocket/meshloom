import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services import hardening

linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")


def _fake_libc(result: int) -> MagicMock:
    libc = MagicMock()
    libc.prctl.return_value = result
    return libc


def test_calls_prctl_on_linux(monkeypatch):
    monkeypatch.setattr(hardening.sys, "platform", "linux")
    libc = _fake_libc(0)
    monkeypatch.setattr(hardening.ctypes, "CDLL", lambda *a, **k: libc)
    assert hardening.set_not_dumpable() is True
    libc.prctl.assert_called_once_with(hardening.PR_SET_DUMPABLE, 0, 0, 0, 0)
    assert libc.prctl.argtypes == hardening.PRCTL_ARGTYPES
    assert libc.prctl.restype is hardening.ctypes.c_int


def test_noop_off_linux(monkeypatch):
    monkeypatch.setattr(hardening.sys, "platform", "darwin")
    cdll = MagicMock()
    monkeypatch.setattr(hardening.ctypes, "CDLL", cdll)
    assert hardening.set_not_dumpable() is False
    cdll.assert_not_called()


def test_prctl_failure_warns_and_does_not_raise(monkeypatch, caplog):
    monkeypatch.setattr(hardening.sys, "platform", "linux")
    monkeypatch.setattr(hardening.ctypes, "CDLL", lambda *a, **k: _fake_libc(-1))
    with caplog.at_level("WARNING"):
        assert hardening.set_not_dumpable() is False
    assert "PR_SET_DUMPABLE" in caplog.text


def test_ctypes_failure_warns_and_does_not_raise(monkeypatch, caplog):
    monkeypatch.setattr(hardening.sys, "platform", "linux")

    def boom(*a, **k):
        raise OSError("no libc")

    monkeypatch.setattr(hardening.ctypes, "CDLL", boom)
    with caplog.at_level("WARNING"):
        assert hardening.set_not_dumpable() is False
    assert "no libc" in caplog.text


@linux_only
def test_process_becomes_not_dumpable():
    """prctl(PR_GET_DUMPABLE) reads 0 afterwards, and /proc/self/status agrees when it has the field."""
    code = (
        "import ctypes\n"
        "from app.services.hardening import set_not_dumpable\n"
        "libc = ctypes.CDLL(None)\n"
        "assert libc.prctl(3, 0, 0, 0, 0) == 1\n"
        "assert set_not_dumpable()\n"
        "print(libc.prctl(3, 0, 0, 0, 0))\n"
        "for line in open('/proc/self/status'):\n"
        "    if line.startswith('Dumpable'):\n"
        "        print(line.split()[1])\n"
    )
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert set(out.stdout.split()) == {"0"}


@linux_only
def test_same_uid_child_cannot_read_server_environ(tmp_path):
    """The server's /proc/<pid>/environ is unreadable by a same-uid process (not root)."""
    if os.geteuid() == 0:
        pytest.skip("root can read anything")
    reader = tmp_path / "reader.py"
    reader.write_text(
        "import sys\n"
        "try:\n"
        "    open(f'/proc/{sys.argv[1]}/environ').read()\n"
        "    print('READ')\n"
        "except OSError:\n"
        "    print('DENIED')\n"
    )
    server = tmp_path / "server.py"
    server.write_text(
        "import os, subprocess, sys\n"
        "from app.services.hardening import set_not_dumpable\n"
        "set_not_dumpable()\n"
        f"r = subprocess.run([sys.executable, {str(reader)!r}, str(os.getpid())],\n"
        "                   capture_output=True, text=True)\n"
        "print(r.stdout.strip())\n"
    )
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(
        [sys.executable, str(server)],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(root)},
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "DENIED"
