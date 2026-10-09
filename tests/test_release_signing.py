"""Release signing chain: trust-anchor check, version consistency, rpm signature
detection and the workflow wiring that makes release jobs fail closed."""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build"
WORKFLOWS = ROOT / ".github" / "workflows"
needs_gpg = pytest.mark.skipif(shutil.which("gpg") is None, reason="gpg not installed")


def _run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        check=False,
    )


def _keys(env_dir: Path) -> dict[str, str]:
    return {"MESHLOOM_KEYS_DIR": str(env_dir)}


@pytest.fixture
def real_keys(tmp_path: Path) -> tuple[Path, Path, str]:
    """A throwaway key laid out like pkg/keys, plus a GNUPGHOME holding its secret subkey."""
    home = tmp_path / "gen"
    home.mkdir(mode=0o700)
    env = {"GNUPGHOME": str(home)}
    gen = _run(
        [
            "gpg",
            "--batch",
            "--passphrase",
            "",
            "--quick-gen-key",
            "t <t@example.invalid>",
            "ed25519",
            "cert",
            "never",
        ],
        env,
    )
    assert gen.returncode == 0, gen.stderr
    listing = _run(["gpg", "--list-keys", "--with-colons"], env).stdout
    fpr = next(ln.split(":")[9] for ln in listing.splitlines() if ln.startswith("fpr:"))
    add = _run(
        ["gpg", "--batch", "--passphrase", "", "--quick-add-key", fpr, "ed25519", "sign", "2y"], env
    )
    assert add.returncode == 0, add.stderr
    keys = tmp_path / "keys"
    keys.mkdir()
    (keys / "FINGERPRINT").write_text(fpr + "\n")
    binary = subprocess.run(
        ["gpg", "--export", fpr], capture_output=True, env={**os.environ, **env}, check=True
    )
    (keys / "meshloom-archive-keyring.gpg").write_bytes(binary.stdout)
    armored = _run(["gpg", "--armor", "--export", fpr], env)
    (keys / "meshloom.asc").write_text(armored.stdout)
    secret = subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--armor",
            "--export-secret-subkeys",
            fpr,
        ],
        capture_output=True,
        env={**os.environ, **env},
        check=True,
    )
    sec_home = tmp_path / "sec"
    sec_home.mkdir(mode=0o700)
    imp = subprocess.run(
        ["gpg", "--batch", "--import"],
        input=secret.stdout,
        capture_output=True,
        env={**os.environ, "GNUPGHOME": str(sec_home)},
        check=False,
    )
    assert imp.returncode == 0, imp.stderr
    return keys, sec_home, fpr


def test_repo_keys_are_placeholders_until_the_owner_creates_the_key() -> None:
    """Strict mode (release jobs) refuses placeholders; PR mode tolerates them."""
    fpr = (ROOT / "pkg" / "keys" / "FINGERPRINT").read_text().strip()
    if len(fpr) == 40 and all(c in "0123456789ABCDEF" for c in fpr):
        pytest.skip("a real key has been committed")
    strict = _run([str(BUILD / "check_signing_keys.sh")])
    assert strict.returncode != 0
    assert "placeholder" in (strict.stdout + strict.stderr).lower()
    assert _run([str(BUILD / "check_signing_keys.sh"), "--allow-placeholder"]).returncode == 0


def test_placeholder_dir_fails_strict(tmp_path: Path) -> None:
    for name in ("FINGERPRINT", "meshloom-archive-keyring.gpg", "meshloom.asc"):
        shutil.copy(ROOT / "pkg" / "keys" / name, tmp_path / name)
    (tmp_path / "FINGERPRINT").write_text(
        "A" * 40 + "\n"
    )  # well-formed but files still placeholders
    out = _run([str(BUILD / "check_signing_keys.sh")], _keys(tmp_path))
    assert out.returncode != 0


@needs_gpg
def test_real_key_passes_and_secret_must_match(
    real_keys: tuple[Path, Path, str], tmp_path: Path
) -> None:
    keys, sec_home, _fpr = real_keys
    script = str(BUILD / "check_signing_keys.sh")
    assert _run([script], _keys(keys)).returncode == 0
    assert _run([script, "--secret-gnupghome", str(sec_home)], _keys(keys)).returncode == 0

    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    gen = _run(
        [
            "gpg",
            "--batch",
            "--passphrase",
            "",
            "--quick-gen-key",
            "o <o@example.invalid>",
            "ed25519",
        ],
        {"GNUPGHOME": str(other)},
    )
    assert gen.returncode == 0, gen.stderr
    mismatch = _run([script, "--secret-gnupghome", str(other)], _keys(keys))
    assert mismatch.returncode != 0
    assert "does not match" in mismatch.stderr


@needs_gpg
def test_fingerprint_not_in_keyring_fails(real_keys: tuple[Path, Path, str]) -> None:
    keys, _sec, fpr = real_keys
    (keys / "FINGERPRINT").write_text(("0" if fpr[0] != "0" else "1") + fpr[1:] + "\n")
    out = _run([str(BUILD / "check_signing_keys.sh")], _keys(keys))
    assert out.returncode != 0


def _version_tree(root: Path, version: str, docker: str | None = None) -> None:
    (root / "frontend").mkdir()
    (root / "meshloom").mkdir()
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n')
    (root / "frontend" / "package.json").write_text(f'{{"name": "x", "version": "{version}"}}')
    (root / "meshloom" / "config.yaml").write_text(f'name: x\nversion: "{version}"\n')
    (root / "meshloom" / "Dockerfile").write_text(
        f"FROM ghcr.io/twinrocket/meshloom:{docker or version}\nCOPY a b\n"
    )


def test_version_consistency_accepts_matching_sources(tmp_path: Path) -> None:
    _version_tree(tmp_path, "4.18.0")
    out = _run(
        [str(BUILD / "check_version_consistency.sh"), "4.18.0"],
        {"MESHLOOM_VERSION_ROOT": str(tmp_path)},
    )
    assert out.returncode == 0, out.stdout


def test_version_consistency_rejects_stale_dockerfile_tag(tmp_path: Path) -> None:
    _version_tree(tmp_path, "4.18.0", docker="4.17.0")
    out = _run(
        [str(BUILD / "check_version_consistency.sh"), "4.18.0"],
        {"MESHLOOM_VERSION_ROOT": str(tmp_path)},
    )
    assert out.returncode != 0
    assert "Dockerfile" in out.stdout


def test_version_consistency_rejects_non_semver_argument() -> None:
    assert _run([str(BUILD / "check_version_consistency.sh"), "v4.18"]).returncode == 2


def test_repo_versions_are_self_consistent() -> None:
    version = next(
        ln.split('"')[1]
        for ln in (ROOT / "pyproject.toml").read_text().splitlines()
        if ln.startswith("version = ")
    )
    out = _run([str(BUILD / "check_version_consistency.sh"), version])
    assert out.returncode == 0, out.stdout


def _fake_rpm(path: Path, tags: list[int]) -> None:
    index = b"".join(struct.pack(">IIII", t, 7, 0, 1) for t in tags)
    header = struct.pack(">4sIII", bytes.fromhex("8eade801"), 0, len(tags), 8) + index + b"\0" * 8
    path.write_bytes(bytes.fromhex("edabeedb") + b"\0" * 92 + header)


def test_rpm_signature_detection(tmp_path: Path) -> None:
    signed, unsigned = tmp_path / "s.rpm", tmp_path / "u.rpm"
    _fake_rpm(signed, [62, 268, 273, 1000, 1002])
    _fake_rpm(unsigned, [62, 273, 1000])
    script = [sys.executable, "-I", str(BUILD / "check_rpm_signed.py")]
    assert _run([*script, str(signed)]).returncode == 0
    assert _run([*script, str(unsigned)]).returncode == 1
    assert _run([*script, str(signed), str(unsigned)]).returncode == 1


def test_nfpm_template_ships_keyring_and_drops_signature_only_when_unsigned() -> None:
    tmpl = (ROOT / "pkg" / "nfpm" / "nfpm.yaml.tmpl").read_text()
    assert "dst: /usr/share/keyrings/meshloom-archive-keyring.gpg" in tmpl
    assert tmpl.count("# SIGN-BEGIN") == 2
    assert tmpl.count("# SIGN-END") == 2
    script = (BUILD / "build_nfpm_packages.sh").read_text()
    assert "MESHLOOM_REQUIRE_SIGNED" in script
    assert "check_signing_keys.sh" in script


def test_release_jobs_fail_closed_and_use_least_privilege() -> None:
    release = (WORKFLOWS / "release.yml").read_text()
    assert "check_version_consistency.sh" in release
    assert "import-signing-key" in release
    assert "sign-manifest.yml" in release
    assert 'MESHLOOM_REQUIRE_SIGNED: "1"' in release
    assert "\npermissions: {}\n" in release

    sign = (WORKFLOWS / "sign-manifest.yml").read_text()
    for needle in (
        "SHA256SUMS.asc",
        "oci ",
        "gpgv --keyring",
        "attest-build-provenance",
        "require_armhf",
    ):
        assert needle in sign

    armhf = (WORKFLOWS / "nfpm-armhf.yml").read_text()
    assert "sign-manifest.yml" in armhf
    assert "require_armhf: true" in armhf

    repo = (WORKFLOWS / "publish-linux-repo.yml").read_text()
    assert "|| true" not in repo
    assert "unsigned repo" not in repo
    for needle in (
        "InRelease",
        "Release.gpg",
        "meshloom.gpg",
        "meshloom.asc",
        "repomd.xml.asc",
        "check_rpm_signed.py",
    ):
        assert needle in repo
    action = (ROOT / ".github" / "actions" / "import-signing-key" / "action.yml").read_text()
    assert "exit 1" in action and "MESHLOOM_REPO_GPG_PRIVATE_KEY" in action

    for wf in ("release", "docker", "nfpm-armhf", "rpi-image", "publish-linux-repo"):
        text = (WORKFLOWS / f"{wf}.yml").read_text()
        assert "\npermissions: {}\n" in text or "\npermissions:\n  contents: read\n" in text, wf
