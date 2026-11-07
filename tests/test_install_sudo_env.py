"""A sudo user runs the installer on a machine where /etc/meshloom is 0750 (MSHL-473).

Runs the functions of scripts/setup/install.sh for real. A `sudo` on PATH opens the
directory for the length of one command, so the script sees what an unprivileged
user sees: a file in there looks absent unless the check itself goes through sudo.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "setup" / "install.sh"

pytestmark = pytest.mark.skipif(os.geteuid() == 0, reason="root reads everything")


def _fn(name: str) -> str:
    text = INSTALL_SH.read_text(encoding="utf-8")
    start = re.search(rf"^{name}\(\) \{{", text, re.MULTILINE)
    assert start, name
    end = text.index("\n}\n", start.start())
    return text[start.start() : end + 3]


def _bash(names: tuple[str, ...], script: str, path_dir: Path) -> subprocess.CompletedProcess[str]:
    source = "\n".join(_fn(n) for n in names)
    env = {**os.environ, "PATH": f"{path_dir}:{os.environ['PATH']}"}
    return subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{source}\n{script}"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def closed_etc(tmp_path: Path):
    """A root-only directory, and a sudo that opens it for one command."""
    etc = tmp_path / "etc"
    etc.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(
        bin_dir / "sudo",
        f"""
[ "${{1:-}}" = -n ] && shift
chmod 700 '{etc}'
rc=0
"$@" || rc=$?
chmod 000 '{etc}'
exit "$rc"
""",
    )
    yield etc, bin_dir
    etc.chmod(0o700)


_ENV_FNS = (
    "is_root",
    "as_root",
    "install_etc_file",
    "_env_ensure_key",
    "write_meshloom_env",
)


def _write_env(etc: Path, bin_dir: Path, existing: str | None) -> str:
    dest = etc / "meshloom.env"
    if existing is not None:
        dest.write_text(existing, encoding="utf-8")
    etc.chmod(0)
    result = _bash(
        _ENV_FNS,
        # _env_drop_key is `sed -i`, which BSD sed reads differently: no key to drop here.
        f"secure_etc_meshloom() {{ :; }}\n_env_drop_key() {{ :; }}\nwrite_meshloom_env '{dest}'",
        bin_dir,
    )
    assert result.returncode == 0, result.stderr
    etc.chmod(0o700)
    return dest.read_text(encoding="utf-8")


def test_a_sudo_user_keeps_the_existing_env_file(closed_etc) -> None:
    etc, bin_dir = closed_etc
    packaged = (
        "# Meshloom configuration\n"
        "MESHCORE_DATABASE_PATH=/var/lib/meshloom/meshcore.db\n"
        "MESHLOOM_INSTALL_KIND=package\n"
        "MY_OWN_LINE=kept\n"
    )
    assert _write_env(etc, bin_dir, packaged) == packaged


def test_a_sudo_user_only_gets_the_missing_keys_added(closed_etc) -> None:
    etc, bin_dir = closed_etc
    out = _write_env(etc, bin_dir, "MY_OWN_LINE=kept\n")
    assert out == (
        "MY_OWN_LINE=kept\n"
        "MESHCORE_DATABASE_PATH=/var/lib/meshloom/meshcore.db\n"
        "MESHLOOM_INSTALL_KIND=package\n"
    )


def test_the_env_file_is_still_written_when_it_is_really_absent(closed_etc) -> None:
    etc, bin_dir = closed_etc
    out = _write_env(etc, bin_dir, None)
    assert "MESHCORE_DATABASE_PATH=/var/lib/meshloom/meshcore.db\n" in out
    assert "MESHLOOM_INSTALL_KIND=package\n" in out
    assert stat.S_IMODE((etc / "meshloom.env").stat().st_mode) == 0o640


def test_a_sudo_user_reads_the_saved_state_of_the_root_only_directory(
    closed_etc, tmp_path: Path
) -> None:
    etc, bin_dir = closed_etc
    (etc / "installer.conf").write_text(
        "lang=fr\nversion=4.18.2\ncompose_dir=/srv/stack\n", encoding="utf-8"
    )
    (etc / "compose-update.env").write_text("MESHLOOM_COMPOSE_DIR=/srv/other\n", encoding="utf-8")
    etc.chmod(0)
    result = _bash(
        ("is_root", "ui_norm", "read_root_file", "system_conf_get"),
        f"""
system_installer_conf() {{ printf '%s' '{etc}/installer.conf'; }}
system_conf_get version; echo
system_conf_get compose_dir; echo
read_root_file '{etc}/compose-update.env'
""",
        bin_dir,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["4.18.2", "/srv/stack", "MESHLOOM_COMPOSE_DIR=/srv/other"]


def test_saving_the_state_as_a_sudo_user_keeps_the_saved_stack(closed_etc) -> None:
    etc, bin_dir = closed_etc
    conf = etc / "installer.conf"
    conf.write_text("lang=en\nversion=4.18.2\ncompose_dir=/srv/stack\n", encoding="utf-8")
    etc.chmod(0)
    result = _bash(
        (
            "is_root",
            "as_root",
            "ui_norm",
            "read_root_file",
            "system_conf_get",
            "write_installer_conf",
            "install_etc_file",
            "save_system_installer_conf",
        ),
        f"""
ML_LANG=en TARGET_VERSION=5.0.0-rc.1 COMPOSE_DIR_SAVED=""
system_installer_conf() {{ printf '%s' '{conf}'; }}
secure_etc_meshloom() {{ :; }}
save_system_installer_conf
""",
        bin_dir,
    )
    assert result.returncode == 0, result.stderr
    etc.chmod(0o700)
    assert "compose_dir=/srv/stack\n" in conf.read_text(encoding="utf-8")


def _record_apt(tmp_path: Path) -> tuple[Path, Path]:
    """An apt-get that notes its arguments, environment and whether stdin can answer."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    log = tmp_path / "apt.log"
    _exe(
        bin_dir / "apt-get",
        f"""
answer=""
IFS= read -r answer || answer=EOF
printf '%s|%s|%s\\n' "$*" "${{DEBIAN_FRONTEND:-}}" "$answer" >>'{log}'
""",
    )
    return bin_dir, log


def _assert_never_asks(log: Path, expected_calls: int) -> None:
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == expected_calls
    for line in lines:
        args, frontend, answer = line.split("|")
        assert "Dpkg::Options::=--force-confdef" in args
        assert "Dpkg::Options::=--force-confold" in args
        assert frontend == "noninteractive"
        assert answer == "EOF"


_STUBS = """
PKG_MGR=apt
REPO_HAS_ARCH=1
as_root() { "$@"; }
phase() { :; }
phase_ok() { :; }
t() { :; }
add_signed_repo() { :; }
write_meshloom_env() { :; }
start_meshloom_unit() { :; }
persist_installer_state() { :; }
ensure_update_helper() { :; }
print_done_native() { :; }
print_prerelease_notes() { :; }
INSTALL_LOG=/dev/null
"""


@pytest.mark.parametrize("path", ["install_from_pages", "install_prerelease_package"])
def test_the_package_paths_cannot_stop_on_a_question(tmp_path: Path, path: str) -> None:
    bin_dir, log = _record_apt(tmp_path)
    (bin_dir / "dpkg-query").write_text(
        "#!/bin/bash\nprintf 'install ok installed'\n", encoding="utf-8"
    )
    (bin_dir / "dpkg-query").chmod(0o755)
    result = _bash(
        ("ensure_log", "run_quiet", "apt_get", "finish_interrupted_package", path),
        # A terminal-like stdin that would answer: the installer must not hand it over.
        _STUBS + f"{path} <<<'answer-from-the-terminal'",
        bin_dir,
    )
    assert result.returncode == 0, result.stderr
    _assert_never_asks(log, 2)


def test_restoring_the_helper_cannot_stop_on_a_question(tmp_path: Path) -> None:
    bin_dir, log = _record_apt(tmp_path)
    result = _bash(
        ("apt_get", "_restore_package_update_helper"),
        _STUBS
        + "_package_update_helper_present() { false; }\n_restore_package_update_helper <<<answer",
        bin_dir,
    )
    assert result.returncode == 0, result.stderr
    _assert_never_asks(log, 3)


def test_no_apt_get_escapes_the_wrapper() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    calls = [line for line in text.splitlines() if re.search(r"\bas_root apt-get\b", line)]
    assert calls == []


def test_every_dnf_call_is_non_interactive() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    calls = [line for line in text.splitlines() if re.search(r"\bas_root dnf\b", line)]
    assert calls
    assert all(" -y" in line for line in calls), calls


@pytest.mark.parametrize(
    ("status", "finishes"),
    [
        ("install ok unpacked", True),
        ("install ok half-configured", True),
        ("install ok installed", False),
        ("", False),
    ],
)
def test_an_interrupted_package_is_finished_without_a_question(
    tmp_path: Path, status: str, finishes: bool
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "dpkg.log"
    _exe(bin_dir / "dpkg-query", f"printf '%s' '{status}'\n")
    _exe(bin_dir / "dpkg", f"""printf '%s|%s\\n' "$*" "$DEBIAN_FRONTEND" >>'{log}'\n""")
    result = _bash(
        ("ensure_log", "run_soft", "finish_interrupted_package"),
        'as_root() { "$@"; }\nINSTALL_LOG=/dev/null\nfinish_interrupted_package',
        bin_dir,
    )
    assert result.returncode == 0, result.stderr
    if finishes:
        assert (
            log.read_text(encoding="utf-8")
            == "--force-confdef --force-confold --configure -a|noninteractive\n"
        )
    else:
        assert not log.exists()


def test_a_package_that_cannot_be_finished_does_not_stop_the_install(tmp_path: Path) -> None:
    """Its own script fails: the install that follows replaces it, as it always did."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "dpkg-query", "printf '%s' 'install ok half-configured'\n")
    _exe(bin_dir / "dpkg", "exit 1\n")
    result = _bash(
        ("ensure_log", "run_soft", "finish_interrupted_package"),
        'as_root() { "$@"; }\nINSTALL_LOG=/dev/null\nfinish_interrupted_package; echo "went on"',
        bin_dir,
    )
    assert result.returncode == 0, result.stderr
    assert "went on" in result.stdout
