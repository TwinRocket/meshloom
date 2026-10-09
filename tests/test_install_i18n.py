"""FR/EN message keys in scripts/setup/install.sh must stay in sync."""

from __future__ import annotations

import re
from pathlib import Path

INSTALL_SH = Path(__file__).resolve().parents[1] / "scripts" / "setup" / "install.sh"


def _keys(lang: str) -> set[str]:
    text = INSTALL_SH.read_text(encoding="utf-8")
    return set(re.findall(rf"{lang}:([A-Za-z0-9_]+)\)", text))


def test_install_sh_en_fr_keys_match() -> None:
    en = _keys("en")
    fr = _keys("fr")
    assert en, "expected English message keys in install.sh"
    assert en == fr, f"i18n key mismatch en-fr={en - fr!r} fr-en={fr - en!r}"


def test_install_sh_is_real_utf8() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "Français" in text
    assert "recommandé" in text
    assert "FranÃ§ais" not in text
    assert "recommandÃ©" not in text


def test_release_asset_tempfile_uses_package_suffix() -> None:
    """apt rejects local files that do not end in .deb / .ddeb / .changes."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'tmp="${tmpdir}/meshloom${pkg_ext}"' in text
    assert 'pkg_ext=".deb"' in text
    assert 'pkg_ext=".rpm"' in text


def test_release_asset_downloads_and_validates_before_install() -> None:
    """4.1.1 shipped a mktemp immediately followed by apt-get install.

    The empty tempfile made apt fail with "could not locate member control.tar".
    Order is load-bearing: signed manifest -> curl -> magic/size + SHA-256 -> apt/dnf.
    """
    text = INSTALL_SH.read_text(encoding="utf-8")
    body = text.split("install_from_release_asset()", 1)[1].split("\nensure_clone()", 1)[0]

    manifest_at = body.index('fetch_signed_manifest "$tag" "$tmpdir"')
    curl_at = body.index('curl -fL --proto \'=https\' --max-time 180 "$url" -o "$tmp"')
    valid_at = body.index('pkg_file_is_valid "$tmp"')
    sha_at = body.index('[ "$(sha256_of "$tmp")" != "$expected" ]')
    apt_at = body.index('apt-get install -y "$tmp"')
    dnf_at = body.index('dnf install -y "$tmp"')
    repo_at = body.index("add_signed_repo")

    assert manifest_at < curl_at < valid_at < sha_at < apt_at < dnf_at < repo_at


def test_package_validation_checks_real_magic_and_size() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    # "!<arch>\n" (deb ar header) and 0xEDABEEDB (rpm lead).
    assert "213c617263683e0a" in text
    assert "edabeedb" in text
    assert '[ "$size" -ge 4096 ]' in text


def test_release_asset_url_is_anchored_to_suffix() -> None:
    """A .deb.asc / .rpm.sha256 sidecar must not be mistaken for the package."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'grep -E "${pattern}\\$"' in text
    assert "grep -F" not in text.split("release_asset_url()", 1)[1].split("\n}", 1)[0]


def test_release_asset_failure_is_logged_with_url_and_size() -> None:
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert "release asset rejected: url=${url} bytes=" in text
