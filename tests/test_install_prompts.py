"""Installer prompts: end of input, step numbering and the Docker packages it offers."""

from __future__ import annotations

import re
import subprocess
import textwrap
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "setup" / "install.sh"
TEXT = INSTALL_SH.read_text(encoding="utf-8")


def _extract_fn(name: str) -> str:
    marker = f"\n{name}() {{"
    start = TEXT.index(marker) + 1
    brace_at = start + len(marker) - 2
    depth = 0
    for index, char in enumerate(TEXT[brace_at:], start=brace_at):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return TEXT[start : index + 1]
    raise AssertionError(f"unclosed function {name}")


def _run(
    names: tuple[str, ...], body: str, stdin: str = "", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    source = "\n\n".join(_extract_fn(name) for name in names)
    script = f"""
set -euo pipefail
t() {{ printf '%s' "$1"; }}
ui_err() {{ printf '%s\\n' "$1" >&2; }}
ui_warn() {{ printf '%s\\n' "$1"; }}
ui_dim() {{ printf '%s\\n' "$1"; }}
ui_b() {{ printf '%s' "$1"; }}
ui_wrap() {{ :; }}
{source}
{textwrap.dedent(body)}
"""
    return subprocess.run(
        ["bash", "-c", script],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=env,
    )


# ── end of input ─────────────────────────────────────────────────────────────


def test_menu_cancels_on_end_of_input_instead_of_looping() -> None:
    # "y" is not a menu answer; then stdin ends. Before the fix this looped forever.
    result = _run(
        ("ui_norm", "ui_ask", "ui_menu"),
        """
        choice="$(ui_menu 3 1)" || exit 130
        echo "chose $choice"
        """,
        stdin="y\n",
    )
    assert result.returncode == 130
    assert "chose" not in result.stdout
    assert "cancelled" in result.stderr


def test_menu_still_accepts_a_valid_answer() -> None:
    result = _run(
        ("ui_norm", "ui_ask", "ui_menu"),
        """
        choice="$(ui_menu 3 1)" || exit 130
        echo "chose $choice"
        """,
        stdin="x\n2\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("chose 2")


def test_every_captured_prompt_stops_on_end_of_input() -> None:
    # ui_ask exits 130 on EOF, but inside $(...) only its subshell exits: each
    # caller must pass it on, or the installer loops on a closed stdin.
    captures = re.findall(r"^.*\$\(ui_(?:ask|menu)\b.*$", TEXT, flags=re.MULTILINE)
    assert len(captures) >= 8
    for line in captures:
        assert line.rstrip().endswith("|| exit 130"), line


# ── step numbering ───────────────────────────────────────────────────────────


def test_steps_have_no_total_that_could_change_on_the_way() -> None:
    assert "STEP_TOTAL" not in TEXT
    result = _run(
        ("ui_screen",),
        """
        UI_CLEAR=0
        ui_screen "Installation method" 1
        ui_screen "Upgrade" 2
        """,
    )
    assert result.returncode == 0, result.stderr
    assert "[1] Installation method" in result.stdout
    assert "[2] Upgrade" in result.stdout
    assert "/" not in result.stdout


# ── Docker packages ──────────────────────────────────────────────────────────


def _docker_host(tmp_path: Path, apt_knows_v2: bool, install_works: bool) -> dict[str, str]:
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "calls.log"
    log.touch()
    known = "docker-compose-v2" if apt_knows_v2 else "no-such-package"
    apt_cache = stubs / "apt-cache"
    apt_cache.write_text(
        "#!/bin/sh\n"
        f'case "$*" in *{known}*) exit 0 ;; esac\n'
        'echo "E: No packages found" >&2\nexit 100\n',
        encoding="utf-8",
    )
    apt_cache.chmod(0o755)
    return {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "STUB_LOG": str(log),
        "STUB_BIN": str(stubs),
        "STUB_INSTALL_WORKS": "1" if install_works else "0",
    }


_DOCKER_STUBS = """
PKG_MGR=apt
priv() { printf 'sudo %s' "$*"; }
ui_ask() { printf 'y'; }
apt_get() {
    echo "apt-get $*" >>"$STUB_LOG"
    case "$1" in install) ;; *) return 0 ;; esac
    if [ "$STUB_INSTALL_WORKS" = 1 ]; then
        printf '#!/bin/sh\\nexit 0\\n' >"$STUB_BIN/docker"
        chmod 0755 "$STUB_BIN/docker"
        return 0
    fi
    echo "E: Unable to locate package" >&2
    return 100
}
"""


def test_docker_on_debian_installs_the_debian_compose_package(tmp_path: Path) -> None:
    env = _docker_host(tmp_path, apt_knows_v2=False, install_works=True)
    result = _run(
        ("docker_packages", "offer_cmd_install", "ensure_docker"),
        _DOCKER_STUBS + "ensure_docker\necho READY\n",
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "sudo apt-get install -y docker.io docker-compose\n" in result.stdout
    calls = Path(env["STUB_LOG"]).read_text(encoding="utf-8").splitlines()
    assert calls == ["apt-get update", "apt-get install -y docker.io docker-compose"]
    assert result.stdout.strip().endswith("READY")


def test_docker_on_ubuntu_installs_compose_v2(tmp_path: Path) -> None:
    env = _docker_host(tmp_path, apt_knows_v2=True, install_works=True)
    result = _run(
        ("docker_packages", "offer_cmd_install", "ensure_docker"),
        _DOCKER_STUBS + "ensure_docker\n",
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    calls = Path(env["STUB_LOG"]).read_text(encoding="utf-8").splitlines()
    assert calls[-1] == "apt-get install -y docker.io docker-compose-v2"


def test_docker_install_failure_points_to_the_docker_docs(tmp_path: Path) -> None:
    env = _docker_host(tmp_path, apt_knows_v2=False, install_works=False)
    result = _run(
        ("docker_packages", "offer_cmd_install", "ensure_docker"),
        _DOCKER_STUBS + "ensure_docker\necho READY\n",
        env=env,
    )
    assert result.returncode == 1
    assert "READY" not in result.stdout
    assert "See https://docs.docker.com/engine/install/" in result.stdout
    assert "missing: docker" in result.stderr


def test_ensure_cmd_still_stops_when_the_command_stays_missing(tmp_path: Path) -> None:
    env = _docker_host(tmp_path, apt_knows_v2=False, install_works=False)
    result = _run(
        ("offer_cmd_install", "ensure_cmd"),
        _DOCKER_STUBS + 'ensure_cmd no-such-tool-here "no-such-tool-here"\necho READY\n',
        env=env,
    )
    assert result.returncode == 1
    assert "READY" not in result.stdout
    assert "missing: no-such-tool-here" in result.stderr
