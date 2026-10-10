"""Execute the privileged update helpers against hostile inputs.

The helpers (pkg/nfpm/apply-update, scripts/setup/helpers/compose-update,
docker-entrypoint.sh) run for real, unprivileged, against a fake root in
tmp_path. External commands (apt-get, dnf, docker, curl, systemctl, setpriv…)
are PATH stubs; gpgv is the real one with an ephemeral key. The fake-root
override is only honoured with MESHLOOM_HELPER_TESTING=1.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APPLY_UPDATE = REPO / "pkg" / "nfpm" / "apply-update"
COMPOSE_UPDATE = REPO / "scripts" / "setup" / "helpers" / "compose-update"
ENTRYPOINT = REPO / "docker-entrypoint.sh"
PIN = REPO / "pkg" / "nfpm" / "meshloom.pref"
OFFICIAL = "https://twinrocket.github.io/meshloom"
KEYRING = "usr/share/keyrings/meshloom-archive-keyring.gpg"
REPO_URL = "https://github.com/TwinRocket/meshloom"
DIGEST = "sha256:" + "ab" * 32
OLD_DIGEST = "sha256:" + "cd" * 32
STATUS_KEYS = {
    "schema",
    "state",
    "phase",
    "percent",
    "error",
    "started_at",
    "updated_at",
    "version",
}

# Real tools the helpers may call; everything else is a stub or absent.
_HOST_TOOLS = [
    "sh",
    "awk",
    "sed",
    "grep",
    "mktemp",
    "mv",
    "chmod",
    "mkdir",
    "date",
    "stat",
    "id",
    "cat",
    "head",
    "tr",
    "rm",
    "basename",
    "dirname",
    "wc",
    "sleep",
    "mkfifo",
    "env",
    "find",
    "chown",
    "printf",
    "test",
    "cp",
    "tail",
    "sort",
    "gpgv",
]

pytestmark = pytest.mark.skipif(
    os.name == "nt" or shutil.which("gpg") is None or shutil.which("gpgv") is None,
    reason="needs a POSIX shell, gpg and gpgv",
)

if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:  # pragma: no cover
    pytestmark = pytest.mark.skip(reason="must run unprivileged")


# ── fixtures ────────────────────────────────────────────────────────────────


@dataclass
class Signer:
    home: Path
    keyring: Path

    def sign(self, path: Path) -> Path:
        out = path.with_name(path.name + ".asc")
        subprocess.run(
            ["gpg", "--batch", "--yes", "--armor", "--detach-sign", "-o", str(out), str(path)],
            env={**os.environ, "GNUPGHOME": str(self.home)},
            check=True,
            capture_output=True,
        )
        return out


def _make_signer(base: Path, name: str) -> Signer:
    home = base / f"gnupg-{name}"
    home.mkdir(mode=0o700)
    env = {**os.environ, "GNUPGHOME": str(home)}
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--passphrase",
            "",
            "--quick-gen-key",
            f"{name} <{name}@test>",
            "ed25519",
            "sign",
            "never",
        ],
        env=env,
        check=True,
        capture_output=True,
    )
    keyring = base / f"{name}.gpg"
    with keyring.open("wb") as handle:
        subprocess.run(["gpg", "--batch", "--export"], env=env, check=True, stdout=handle)
    return Signer(home=home, keyring=keyring)


@pytest.fixture(scope="module")
def signers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Signer]:
    base = tmp_path_factory.mktemp("keys")
    return {"release": _make_signer(base, "release"), "attacker": _make_signer(base, "attacker")}


def _write_exec(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + textwrap.dedent(body).lstrip("\n"), encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def env_root(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    """(fake root, stub log, env) with PATH = stubs + a curated host-tools dir."""
    root = tmp_path / "root"
    root.mkdir()
    bin_dir = tmp_path / "bin"
    stubs = tmp_path / "stubs"
    bin_dir.mkdir()
    stubs.mkdir()
    for tool in _HOST_TOOLS:
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    log = tmp_path / "calls.log"
    log.touch()
    # Every stub logs "<name> <args>" and a snapshot of the published status.
    # The installed meshloom version lives in $STUB_PKG (empty = not installed);
    # a successful install/upgrade moves it to $STUB_NEW_VERSION.
    common = """
        echo "$(basename "$0") $*" >>"$STUB_LOG"
        [ -f "$STUB_STATUS" ] && sed 's/^/  status /' "$STUB_STATUS" >>"$STUB_LOG"
        installed=$(cat "$STUB_PKG" 2>/dev/null || true)
        """
    _write_exec(stubs / "systemctl", common + "exit 0\n")
    _write_exec(
        stubs / "dpkg-query",
        common
        + """
        [ -n "$installed" ] || exit 1
        case "$*" in *Status*) echo "install ok installed" ;; *) printf '%s' "$installed" ;; esac
        """,
    )
    _write_exec(
        stubs / "rpm",
        common
        + """
        [ -n "$installed" ] || exit 1
        printf '%s' "$installed"
        """,
    )
    _write_exec(
        stubs / "dnf",
        common
        + """
        [ "${STUB_RC_DNF:-0}" = 0 ] || exit "$STUB_RC_DNF"
        [ -n "${STUB_NEW_VERSION:-}" ] && printf '%s' "$STUB_NEW_VERSION" >"$STUB_PKG"
        exit 0
        """,
    )
    pkg = tmp_path / "installed-version"
    pkg.write_text("4.17.0-1")
    env = {
        "PATH": f"{stubs}:{bin_dir}",
        "MESHLOOM_HELPER_TESTING": "1",
        "MESHLOOM_HELPER_ROOT": str(root),
        "RUNTIME_DIRECTORY": str(root / "run" / "helper"),
        "STUB_LOG": str(log),
        "STUB_PKG": str(pkg),
        "STUB_NEW_VERSION": "4.18.0-1",
        "HOME": str(tmp_path),
        "LC_ALL": "C",
    }
    return root, log, env


def _run(script: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(script), *args], env=env, capture_output=True, text=True, timeout=60, check=False
    )


def _status(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == STATUS_KEYS
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    return data


def _plant_hostile_app_files(app_dir: Path, outside: Path) -> dict[Path, str]:
    """What a compromised app/container could leave for root to trip on."""
    app_dir.mkdir(parents=True, exist_ok=True)
    outside.write_text("precious\n", encoding="utf-8")
    (app_dir / "update-job.json").write_text(
        json.dumps({"state": "applying", "target": "1.0\nimage: evil", "last_attempt": 1}),
        encoding="utf-8",
    )
    (app_dir / "request-update").symlink_to(outside)
    (app_dir / "update-attempt.json").symlink_to(outside)
    return {
        app_dir / "update-job.json": (app_dir / "update-job.json").read_text(encoding="utf-8"),
        outside: "precious\n",
    }


# ── package helper (apply-update) ─────────────────────────────────────────────


def _apt_stub(stubs: Path) -> None:
    _write_exec(
        stubs / "apt-get",
        """
        echo "apt-get $*" >>"$STUB_LOG"
        [ -f "$STUB_STATUS" ] && sed 's/^/  status /' "$STUB_STATUS" >>"$STUB_LOG"
        [ "${STUB_RC_APT:-0}" = 0 ] || exit "$STUB_RC_APT"
        case "$*" in
            *APT::Status-Fd=3*install*)
                echo "dlstatus:1:40:Downloading" >&3
                echo "pmstatus:meshloom:70:Installing" >&3
                [ -n "${STUB_NEW_VERSION:-}" ] && printf '%s' "$STUB_NEW_VERSION" >"$STUB_PKG"
                ;;
        esac
        exit 0
        """,
    )


@pytest.fixture
def apt_host(env_root: tuple[Path, Path, dict[str, str]], signers: dict[str, Signer]):
    root, log, env = env_root
    _apt_stub(Path(env["PATH"].split(":")[0]))
    (root / "etc/apt/sources.list.d").mkdir(parents=True)
    (root / "etc/apt/preferences.d").mkdir(parents=True)
    (root / "etc/apt/sources.list").write_text("deb http://deb.debian.org/debian trixie main\n")
    keyring = root / KEYRING
    keyring.parent.mkdir(parents=True)
    shutil.copy(signers["release"].keyring, keyring)
    (root / "etc/apt/sources.list.d/meshloom.list").write_text(
        f"deb [signed-by=/{KEYRING}] {OFFICIAL}/apt stable main\n"
    )
    shutil.copy(PIN, root / "etc/apt/preferences.d/meshloom.pref")
    (root / "run/systemd/system").mkdir(parents=True)
    env["STUB_STATUS"] = str(root / "var/lib/meshloom-update/status.json")
    return root, log, env


def _apt_calls(log: Path) -> list[str]:
    return [line for line in log.read_text().splitlines() if line.startswith("apt-get ")]


def test_apply_update_happy_path_phases_and_status(apt_host) -> None:
    root, log, env = apt_host
    hostile = _plant_hostile_app_files(root / "var/lib/meshloom", root / "outside")
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 0, result.stderr

    status = _status(root / "var/lib/meshloom-update/status.json")
    assert status["state"] == "succeeded"
    assert status["phase"] == "done"
    assert status["percent"] == 100
    assert status["error"] is None
    assert status["version"] == "4.18.0-1"

    lines = log.read_text().splitlines()
    calls = _apt_calls(log)
    assert "update" in calls[0] and "AllowUnauthenticated=false" in calls[0]
    assert "install -y --only-upgrade meshloom" in calls[1]
    assert "APT::Status-Fd=3" in calls[1]
    phases = [
        json.loads(line[len("  status ") :])["phase"]
        for line in lines
        if line.startswith("  status ")
    ]
    # preparing (update) -> downloading/installing (install) -> restarting (systemctl).
    assert phases[0] == "preparing"
    assert "downloading" in phases
    assert phases.index("downloading") < phases.index("restarting")
    assert "systemctl start meshloom" in log.read_text() or "systemctl is-active" in log.read_text()

    # Nothing the app can write was read, followed, deleted or changed.
    for path, content in hostile.items():
        assert path.read_text(encoding="utf-8") == content
    assert (root / "var/lib/meshloom/request-update").is_symlink()
    assert "evil" not in log.read_text()
    leftovers = [p.name for p in (root / "var/lib/meshloom-update").iterdir()]
    assert sorted(leftovers) == ["last-start", "status.json"]
    assert stat.S_IMODE((root / "var/lib/meshloom-update").stat().st_mode) == 0o755


@pytest.mark.parametrize(
    ("relpath", "content", "reason"),
    [
        (
            "etc/apt/sources.list.d/meshloom.list",
            f"deb [trusted=yes] {OFFICIAL}/apt stable main\n",
            "insecure option",
        ),
        ("etc/apt/sources.list.d/meshloom.list", f"deb {OFFICIAL}/apt stable main\n", "signed-by"),
        (
            "etc/apt/sources.list.d/meshloom.list",
            f"deb [signed-by=/{KEYRING} trusted=yes] {OFFICIAL}/apt stable main\n",
            "insecure option",
        ),
        (
            "etc/apt/sources.list.d/zz.list",
            f"deb [allow-insecure=yes signed-by=/{KEYRING}] {OFFICIAL}/apt stable main\n",
            "insecure option",
        ),
        (
            "etc/apt/sources.list.d/meshloom.sources",
            f"Types: deb\nURIs: {OFFICIAL}/apt\nSuites: stable\nComponents: main\n",
            "Signed-By",
        ),
    ],
)
def test_apply_update_fails_closed_on_unsigned_apt_source(
    apt_host, relpath, content, reason
) -> None:
    root, log, env = apt_host
    if relpath.endswith("meshloom.sources"):
        (root / "etc/apt/sources.list.d/meshloom.list").unlink()
    (root / relpath).write_text(content)
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 1
    assert reason in result.stderr
    assert _apt_calls(log) == []
    status = _status(root / "var/lib/meshloom-update/status.json")
    assert status["state"] == "failed"
    assert "not signature-checked" in str(status["error"])


@pytest.mark.parametrize(
    ("relpath", "content"),
    [
        ("etc/apt/sources.list.d/other.list", "deb [trusted=yes] http://example.invalid/ x main\n"),
        (
            "etc/apt/sources.list.d/other.sources",
            "Types: deb\nURIs: http://example.invalid/\nSuites: x\nTrusted: yes\n",
        ),
    ],
)
def test_apply_update_ignores_unrelated_sources(apt_host, relpath, content) -> None:
    """The origin pin keeps third-party sources away from the meshloom package."""
    root, log, env = apt_host
    (root / relpath).write_text(content)
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 0, result.stderr


def test_apt_pin_ignores_a_spoofed_origin(tmp_path: Path) -> None:
    """A trusted third-party repo claiming "Origin: Meshloom" with meshloom 9.9.9
    must not become the candidate (real apt, private Dir tree)."""
    if shutil.which("apt-cache") is None or shutil.which("apt-get") is None:
        pytest.skip("apt not available")
    repo = tmp_path / "evil"
    (repo / "dists/stable/main/binary-amd64").mkdir(parents=True)
    (repo / "dists/stable/main/binary-amd64/Packages").write_text(
        "Package: meshloom\nVersion: 9.9.9-1\nArchitecture: amd64\n"
        "Maintainer: x <x@x>\nFilename: pool/meshloom.deb\nSize: 1\n"
        "SHA256: " + "0" * 64 + "\nDescription: evil\n"
    )
    (repo / "dists/stable/Release").write_text(
        "Origin: Meshloom\nLabel: Meshloom\nSuite: stable\nCodename: stable\n"
        "Architectures: amd64\nComponents: main\n"
    )
    etc = tmp_path / "etc"
    (etc / "preferences.d").mkdir(parents=True)
    (etc / "sources.list.d").mkdir()
    shutil.copy(PIN, etc / "preferences.d/meshloom.pref")
    (etc / "sources.list").write_text(f"deb [trusted=yes arch=amd64] file:{repo} stable main\n")
    state = tmp_path / "state"
    (state / "lists/partial").mkdir(parents=True)
    cache = tmp_path / "cache"
    (cache / "archives/partial").mkdir(parents=True)
    (tmp_path / "status").write_text("")
    opts = [
        f"-oDir::Etc={etc}",
        f"-oDir::State={state}",
        f"-oDir::State::status={tmp_path / 'status'}",
        f"-oDir::Cache={cache}",
        "-oAPT::Architecture=amd64",
        "-oDebug::NoLocking=1",
    ]
    update = subprocess.run(["apt-get", *opts, "update"], capture_output=True, text=True)
    assert update.returncode == 0, update.stderr
    policy = subprocess.run(
        ["apt-cache", *opts, "policy", "meshloom"], capture_output=True, text=True, check=True
    ).stdout
    assert "Candidate: (none)" in policy, policy
    assert "9.9.9-1 -1" in policy, policy


def test_apply_update_requires_the_origin_pin(apt_host) -> None:
    root, log, env = apt_host
    (root / "etc/apt/preferences.d/meshloom.pref").unlink()
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 1
    assert "pin" in result.stderr
    assert _apt_calls(log) == []


def test_apply_update_cooldown(apt_host) -> None:
    root, log, env = apt_host
    assert _run(APPLY_UPDATE, env).returncode == 0
    first = len(_apt_calls(log))
    again = _run(APPLY_UPDATE, env)
    assert again.returncode == 0
    assert len(_apt_calls(log)) == first
    status = _status(root / "var/lib/meshloom-update/status.json")
    assert status["state"] == "cooldown"
    assert status["version"] == "4.18.0-1"
    assert "too soon" in str(status["error"])


def test_apply_update_reports_apt_failure(apt_host) -> None:
    root, _log, env = apt_host
    env["STUB_RC_APT"] = "100"
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 1
    status = _status(root / "var/lib/meshloom-update/status.json")
    assert status["state"] == "failed"
    assert status["error"] == "apt-get update failed"


def test_apply_update_refuses_symlinked_state_dir(apt_host) -> None:
    root, log, env = apt_host
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (root / "var/lib").mkdir(parents=True, exist_ok=True)
    (root / "var/lib/meshloom-update").symlink_to(elsewhere)
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 1
    assert list(elsewhere.iterdir()) == []
    assert _apt_calls(log) == []


def test_apply_update_ignores_root_override_without_testing_flag(apt_host) -> None:
    root, log, env = apt_host
    del env["MESHLOOM_HELPER_TESTING"]
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 1
    assert "must run as root" in result.stderr
    assert not (root / "var/lib/meshloom-update").exists()
    assert _apt_calls(log) == []


@pytest.fixture
def dnf_host(env_root):
    root, log, env = env_root
    (root / "etc/yum.repos.d").mkdir(parents=True)
    env["STUB_STATUS"] = str(root / "var/lib/meshloom-update/status.json")
    return root, log, env


_GOOD_REPO = (
    f"[meshloom]\nname=Meshloom\nbaseurl={OFFICIAL}/rpm/$basearch\nenabled=1\n"
    "gpgcheck=1\nrepo_gpgcheck=1\ngpgkey=file:///usr/share/keyrings/meshloom-archive-keyring.asc\n"
)


def test_apply_update_dnf_uses_only_the_signed_repo(dnf_host) -> None:
    root, log, env = dnf_host
    (root / "etc/yum.repos.d/meshloom.repo").write_text(_GOOD_REPO)
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 0, result.stderr
    dnf = [line for line in log.read_text().splitlines() if line.startswith("dnf ")]
    # dnf5 "install" is a no-op on an installed package: upgrade, with --refresh.
    assert dnf == [
        "dnf -y --refresh --disablerepo=* --enablerepo=meshloom --setopt=meshloom.gpgcheck=1 "
        "--setopt=meshloom.repo_gpgcheck=1 upgrade meshloom"
    ]
    status = _status(root / "var/lib/meshloom-update/status.json")
    assert status["state"] == "succeeded"
    assert status["version"] == "4.18.0-1"


def test_apply_update_dnf_installs_when_absent(dnf_host) -> None:
    root, log, env = dnf_host
    (root / "etc/yum.repos.d/meshloom.repo").write_text(_GOOD_REPO)
    Path(env["STUB_PKG"]).write_text("")
    assert _run(APPLY_UPDATE, env).returncode == 0
    assert (
        "install meshloom"
        in [ln for ln in log.read_text().splitlines() if ln.startswith("dnf ")][0]
    )


@pytest.mark.parametrize("manager", ["apt", "dnf"])
def test_apply_update_reports_no_newer_version_as_failure(apt_host, dnf_host, manager) -> None:
    root, log, env = apt_host if manager == "apt" else dnf_host
    if manager == "dnf":
        (root / "etc/yum.repos.d/meshloom.repo").write_text(_GOOD_REPO)
    env["STUB_NEW_VERSION"] = ""
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 1
    status = _status(root / "var/lib/meshloom-update/status.json")
    assert status["state"] == "failed"
    assert "no newer meshloom" in str(status["error"])
    assert status["version"] == "4.17.0-1"


@pytest.mark.parametrize(
    "repo",
    [
        _GOOD_REPO.replace("gpgcheck=1\nrepo", "gpgcheck=0\nrepo"),
        _GOOD_REPO.replace("repo_gpgcheck=1\n", ""),
        _GOOD_REPO.replace("[meshloom]", "[other]"),
    ],
)
def test_apply_update_dnf_fails_closed(dnf_host, repo: str) -> None:
    root, log, env = dnf_host
    (root / "etc/yum.repos.d/meshloom.repo").write_text(repo)
    result = _run(APPLY_UPDATE, env)
    assert result.returncode == 1
    assert not [line for line in log.read_text().splitlines() if line.startswith("dnf ")]


# ── compose helper (compose-update) ───────────────────────────────────────────


@pytest.fixture
def compose_host(env_root, signers: dict[str, Signer], tmp_path: Path):
    root, log, env = env_root
    stubs = Path(env["PATH"].split(":")[0])
    files = tmp_path / "release-files"
    files.mkdir()
    _write_exec(
        stubs / "curl",
        """
        echo "curl $*" >>"$STUB_LOG"
        out=; url=; w=
        while [ $# -gt 0 ]; do
            case "$1" in
                -o) out=$2; shift ;;
                -w) w=$2; shift ;;
                --proto | --max-time) shift ;;
                https://*) url=$1 ;;
            esac
            shift
        done
        case "$url" in
            */releases/latest) [ -n "$w" ] && printf '%s' "$(cat "$STUB_FILES/location")"; exit 0 ;;
        esac
        rel=${url#https://github.com/TwinRocket/meshloom/releases/download/}
        [ -f "$STUB_FILES/$rel" ] || exit 22
        cp "$STUB_FILES/$rel" "$out"
        """,
    )
    _write_exec(
        stubs / "docker",
        """
        echo "docker $* (cwd=$(pwd))" >>"$STUB_LOG"
        [ -f .env ] && sed 's/^/  env /' .env >>"$STUB_LOG"
        exit "${STUB_RC_DOCKER:-0}"
        """,
    )
    keyring = root / KEYRING
    keyring.parent.mkdir(parents=True)
    shutil.copy(signers["release"].keyring, keyring)
    compose_dir = root / "srv/meshloom"
    (compose_dir / "data").mkdir(parents=True)
    (compose_dir / "docker-compose.yml").write_text("services: {}\n")
    (compose_dir / ".env").write_text(
        f"MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.17.0@{OLD_DIGEST}\nKEEP_ME=1\n"
    )
    env.update({"STUB_FILES": str(files), "MESHLOOM_COMPOSE_DIR": "/srv/meshloom"})
    return root, log, env, files, compose_dir


def _publish(
    files: Path, signer: Signer, tag: str, body: str | None = None, *, location: str | None = None
) -> None:
    (files / "location").write_text(
        location if location is not None else f"{REPO_URL}/releases/tag/{tag}"
    )
    rel = files / tag
    rel.mkdir(exist_ok=True)
    digests = rel / "OCI-DIGESTS"
    digests.write_text(
        body if body is not None else f"ghcr.io/twinrocket/meshloom:{tag} {DIGEST}\n"
    )
    signer.sign(digests)


def _docker_calls(log: Path) -> list[str]:
    return [line for line in log.read_text().splitlines() if line.startswith("docker ")]


def test_compose_update_happy_path(compose_host, signers) -> None:
    root, log, env, files, compose_dir = compose_host
    _publish(files, signers["release"], "4.18.0")
    hostile = _plant_hostile_app_files(compose_dir / "data", root / "outside")
    yaml_before = (compose_dir / "docker-compose.yml").read_bytes()

    result = _run(COMPOSE_UPDATE, env)
    assert result.returncode == 0, result.stderr

    env_lines = (compose_dir / ".env").read_text().splitlines()
    assert f"MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.18.0@{DIGEST}" in env_lines
    assert "KEEP_ME=1" in env_lines
    assert stat.S_IMODE((compose_dir / ".env").stat().st_mode) == 0o644
    assert (compose_dir / "docker-compose.yml").read_bytes() == yaml_before
    calls = _docker_calls(log)
    assert [c.split(" (cwd=")[0] for c in calls] == ["docker compose pull", "docker compose up -d"]
    assert all(f"cwd={compose_dir}" in c for c in calls)
    status = _status(compose_dir / "update-status/status.json")
    assert status["state"] == "succeeded" and status["version"] == "4.18.0"
    for path, content in hostile.items():
        assert path.read_text(encoding="utf-8") == content
    assert "evil" not in log.read_text()
    assert "1.0" not in (compose_dir / ".env").read_text()


@pytest.mark.parametrize(
    "location",
    [
        f"{REPO_URL}/releases/tag/1.2.3;rm",
        f"{REPO_URL}/releases/tag/v1.2.3-rc",
        f"{REPO_URL}/releases/tag/v4.18.0",
        f"{REPO_URL}/releases/tag/4.18.0/../../evil",
        "https://evil.example/TwinRocket/meshloom/releases/tag/4.18.0",
        f"{REPO_URL}/releases/tag/4.18.0\nimage: evil",
        "",
    ],
)
def test_compose_update_rejects_hostile_location(compose_host, signers, location: str) -> None:
    root, log, env, files, compose_dir = compose_host
    _publish(files, signers["release"], "4.18.0", location=location)
    before = (compose_dir / ".env").read_text()
    result = _run(COMPOSE_UPDATE, env)
    assert result.returncode == 1
    assert (compose_dir / ".env").read_text() == before
    assert _docker_calls(log) == []
    assert "could not resolve" in str(_status(compose_dir / "update-status/status.json")["error"])


def test_compose_update_rejects_bad_signature(compose_host, signers) -> None:
    root, log, env, files, compose_dir = compose_host
    _publish(files, signers["attacker"], "4.18.0")
    before = (compose_dir / ".env").read_text()
    result = _run(COMPOSE_UPDATE, env)
    assert result.returncode == 1
    assert "signature check failed" in result.stderr
    assert (compose_dir / ".env").read_text() == before
    assert _docker_calls(log) == []


def test_compose_update_rejects_tampered_digest_file(compose_host, signers) -> None:
    root, log, env, files, compose_dir = compose_host
    _publish(files, signers["release"], "4.18.0")
    (files / "4.18.0" / "OCI-DIGESTS").write_text(
        f"ghcr.io/twinrocket/meshloom:4.18.0 sha256:{'ee' * 32}\n"
    )
    assert _run(COMPOSE_UPDATE, env).returncode == 1
    assert _docker_calls(log) == []


@pytest.mark.parametrize(
    "body",
    [
        f"ghcr.io/twinrocket/meshloom:4.18.0 {DIGEST}\nghcr.io/twinrocket/meshloom:4.18.0 {OLD_DIGEST}\n",
        f"ghcr.io/twinrocket/meshloom:4.17.0 {DIGEST}\n",
        f"ghcr.io/evil/meshloom:4.18.0 {DIGEST}\n",
        f"ghcr.io/twinrocket/meshloom:4.18.0 {DIGEST.upper()}\n",
        f"ghcr.io/twinrocket/meshloom:4.18.0 {DIGEST} extra\n",
        "",
    ],
)
def test_compose_update_rejects_bad_digest_line(compose_host, signers, body: str) -> None:
    root, log, env, files, compose_dir = compose_host
    _publish(files, signers["release"], "4.18.0", body)
    before = (compose_dir / ".env").read_text()
    result = _run(COMPOSE_UPDATE, env)
    assert result.returncode == 1
    assert (compose_dir / ".env").read_text() == before
    assert _docker_calls(log) == []


def test_compose_update_refuses_downgrade(compose_host, signers) -> None:
    root, log, env, files, compose_dir = compose_host
    (compose_dir / ".env").write_text(
        f"MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.19.0@{OLD_DIGEST}\n"
    )
    _publish(files, signers["release"], "4.18.0")
    result = _run(COMPOSE_UPDATE, env)
    assert result.returncode == 1
    assert "refusing downgrade" in result.stderr
    assert _docker_calls(log) == []


def test_compose_update_cooldown(compose_host, signers) -> None:
    root, log, env, files, compose_dir = compose_host
    _publish(files, signers["release"], "4.18.0")
    assert _run(COMPOSE_UPDATE, env).returncode == 0
    assert len(_docker_calls(log)) == 2
    assert _run(COMPOSE_UPDATE, env).returncode == 0
    assert len(_docker_calls(log)) == 2
    assert "too soon" in str(_status(compose_dir / "update-status/status.json")["error"])


def test_compose_update_refuses_symlinked_status_dir(compose_host, signers) -> None:
    root, log, env, files, compose_dir = compose_host
    _publish(files, signers["release"], "4.18.0")
    elsewhere = root / "elsewhere"
    elsewhere.mkdir()
    (compose_dir / "update-status").symlink_to(elsewhere)
    assert _run(COMPOSE_UPDATE, env).returncode == 1
    assert list(elsewhere.iterdir()) == []
    assert _docker_calls(log) == []


@pytest.mark.parametrize(
    "bad", ["srv/meshloom", "/srv/../etc", "/srv/meshloom\n/etc", "/srv/mesh loom", ""]
)
def test_compose_update_validates_compose_dir(compose_host, bad: str) -> None:
    root, log, env, files, compose_dir = compose_host
    env["MESHLOOM_COMPOSE_DIR"] = bad
    result = _run(COMPOSE_UPDATE, env)
    assert result.returncode == 1
    assert _docker_calls(log) == []


def test_compose_update_bootstrap_pins_without_pull(compose_host, signers) -> None:
    root, log, env, files, compose_dir = compose_host
    (compose_dir / ".env").unlink()
    _publish(files, signers["release"], "4.18.0")
    result = _run(COMPOSE_UPDATE, env, "--bootstrap")
    assert result.returncode == 0, result.stderr
    assert (compose_dir / ".env").read_text().splitlines()[1] == (
        f"MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:4.18.0@{DIGEST}"
    )
    assert _docker_calls(log) == []


def test_compose_update_apply_needs_a_valid_pin(compose_host, signers) -> None:
    root, log, env, files, compose_dir = compose_host
    (compose_dir / ".env").write_text("MESHLOOM_IMAGE=ghcr.io/twinrocket/meshloom:latest\n")
    _publish(files, signers["release"], "4.18.0")
    result = _run(COMPOSE_UPDATE, env)
    assert result.returncode == 1
    assert "no valid MESHLOOM_IMAGE pin" in result.stderr
    assert _docker_calls(log) == []


def test_installer_embeds_the_current_helpers() -> None:
    result = subprocess.run(
        ["python3", str(REPO / "scripts/setup/sync_installer_embeds.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# ── container entrypoint ─────────────────────────────────────────────────────


@pytest.fixture
def entry(tmp_path: Path):
    if shutil.which("unshare") is None:
        pytest.skip("unshare not available")
    probe = subprocess.run(["unshare", "-rm", "true"], capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("unprivileged user namespaces are disabled")
    root = tmp_path / "root"
    (root / "app/data").mkdir(parents=True)
    (root / "dev").mkdir()
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    log = tmp_path / "setpriv.log"
    _write_exec(
        stubs / "setpriv",
        """
        echo "setpriv $*" >>"$STUB_LOG"
        while [ "$1" != "--" ]; do shift; done
        shift
        if [ "$1" = test ] && [ -n "${STUB_DENY:-}" ]; then exit 1; fi
        exec "$@"
        """,
    )
    env = {
        "PATH": f"{stubs}:/usr/bin:/bin",
        "MESHLOOM_HELPER_TESTING": "1",
        "MESHLOOM_HELPER_ROOT": str(root),
        "STUB_LOG": str(log),
    }
    return root, log, env


def _entry(
    env: dict[str, str], root: Path, *, device: bool = False
) -> subprocess.CompletedProcess[str]:
    # Fake root via a user namespace; /dev/null bind-mounted as the radio.
    prep = ""
    if device:
        prep = f'touch "{root}/dev/ttyUSB0" && mount --bind /dev/null "{root}/dev/ttyUSB0" && '
    script = (
        prep + f'exec sh "{ENTRYPOINT}" sh -c \'echo "RAN uid=$(id -u) nosync=${{UV_NO_SYNC:-}}"\''
    )
    return subprocess.run(
        ["unshare", "-rm", "sh", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_entrypoint_is_a_passthrough_by_default(entry) -> None:
    root, log, env = entry
    result = _entry(env, root)
    assert result.returncode == 0, result.stderr
    assert "RAN uid=0 nosync=" in result.stdout
    assert not log.exists()


def test_entrypoint_drops_to_requested_uid(entry) -> None:
    root, log, env = entry
    env["MESHLOOM_RUN_AS_USER"] = "10001"
    result = _entry(env, root)
    assert result.returncode == 0, result.stderr
    assert "nosync=1" in result.stdout
    calls = log.read_text().splitlines()
    assert calls[-1].startswith(
        "setpriv --reuid=10001 --regid=10001 --clear-groups --inh-caps=-all -- "
    )


def test_entrypoint_adds_serial_device_group(entry) -> None:
    root, log, env = entry
    env["MESHLOOM_RUN_AS_USER"] = "10001"
    result = _entry(env, root, device=True)
    if "mount" in result.stderr:
        pytest.skip("bind mounts unavailable in this namespace")
    assert result.returncode == 0, result.stderr
    calls = log.read_text().splitlines()
    assert any(" test -r " in c for c in calls)
    assert "--groups=" in calls[-1] and "--reuid=10001" in calls[-1]


def test_entrypoint_falls_back_to_root_when_radio_unreachable(entry) -> None:
    root, log, env = entry
    env["MESHLOOM_RUN_AS_USER"] = "10001"
    env["STUB_DENY"] = "1"
    result = _entry(env, root, device=True)
    if "mount" in result.stderr and "RAN" not in result.stdout:
        pytest.skip("bind mounts unavailable in this namespace")
    assert result.returncode == 0, result.stderr
    assert "running as root instead" in result.stderr
    assert "RAN uid=0" in result.stdout


def test_entrypoint_rejects_non_numeric_uid(entry) -> None:
    root, log, env = entry
    env["MESHLOOM_RUN_AS_USER"] = "10001;id"
    result = _entry(env, root)
    assert "RAN uid=0" in result.stdout
    assert "numeric uid" in result.stderr
    assert not log.exists()


# ── systemd units ────────────────────────────────────────────────────────────


def test_units_pass_systemd_analyze_verify(tmp_path: Path) -> None:
    if shutil.which("systemd-analyze") is None:
        pytest.skip("systemd-analyze not available")
    fake_exec = tmp_path / "helper"
    _write_exec(fake_exec, "exit 0\n")
    units = {
        "meshloom-update.service": (REPO / "pkg/nfpm/meshloom-update.service").read_text(),
        "meshloom-update.path": (REPO / "pkg/nfpm/meshloom-update.path").read_text(),
        "meshloom-compose-update.service": (
            REPO / "scripts/setup/helpers/meshloom-compose-update.service.in"
        ).read_text(),
        "meshloom-compose-update.path": (
            REPO / "scripts/setup/helpers/meshloom-compose-update.path.in"
        ).read_text(),
    }
    paths = []
    for name, text in units.items():
        text = text.replace("@COMPOSE_DIR@", "/srv/meshloom")
        text = text.replace("/usr/lib/meshloom/apply-update", str(fake_exec))
        text = text.replace("/usr/lib/meshloom/compose-update", str(fake_exec))
        text = text.replace("Requires=docker.service\n", "")
        path = tmp_path / name
        path.write_text(text)
        paths.append(str(path))
    result = subprocess.run(
        ["systemd-analyze", "verify", *paths], capture_output=True, text=True, check=False
    )
    problems = [
        line
        for line in (result.stdout + result.stderr).splitlines()
        if line.strip() and "docker.service" not in line and "network-online" not in line
    ]
    assert problems == [], problems
