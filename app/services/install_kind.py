"""How this process was installed — declared first, then helper, then container."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

InstallKind = Literal["package", "compose", "addon", "container", "source"]

_DECLARED = frozenset({"package", "compose", "addon", "container", "source"})

# pkg/nfpm ships meshloom-update.service and /usr/lib/meshloom/apply-update.
APPLY_UPDATE_BIN = Path("/usr/lib/meshloom/apply-update")
UPDATE_SERVICE = Path("/usr/lib/systemd/system/meshloom-update.service")
COMPOSE_HELPER_SENTINEL = Path("/var/lib/meshloom/update-helper-compose")
DOCKERENV_PATH = Path("/.dockerenv")
CONTAINERENV_PATH = Path("/run/.containerenv")


def _env_kind() -> InstallKind | None:
    raw = (os.environ.get("MESHLOOM_INSTALL_KIND") or "").strip().lower()
    if raw in _DECLARED:
        return raw  # type: ignore[return-value]
    return None


def _in_container() -> bool:
    return DOCKERENV_PATH.exists() or CONTAINERENV_PATH.exists()


def in_container() -> bool:
    """Whether this process runs in a container, whatever the update helper says."""
    return _in_container() or detect_install_kind()[0] in {"compose", "container", "addon"}


def _package_helper_present() -> bool:
    return APPLY_UPDATE_BIN.exists() or UPDATE_SERVICE.exists()


def _compose_helper_present() -> bool:
    helper = (os.environ.get("MESHLOOM_UPDATE_HELPER") or "").strip().lower()
    return helper == "compose" or COMPOSE_HELPER_SENTINEL.exists()


def _helper_disabled() -> bool:
    """``MESHLOOM_UPDATE_HELPER=none``: the installer found no signed repo for this arch."""
    return (os.environ.get("MESHLOOM_UPDATE_HELPER") or "").strip().lower() == "none"


def detect_install_kind() -> tuple[InstallKind, bool]:
    """Return ``(kind, apply_supported)``.

    Addon is only ``MESHLOOM_INSTALL_KIND=addon`` — never inferred from
    Supervisor tokens or other Home Assistant signals.
    """
    kind, supported = _detect()
    if _helper_disabled():
        return kind, False
    return kind, supported


def _detect() -> tuple[InstallKind, bool]:
    declared = _env_kind()
    if declared == "addon":
        return "addon", False
    if declared == "compose":
        return "compose", _compose_helper_present()
    if declared == "package":
        return "package", _package_helper_present()
    if declared in {"container", "source"}:
        return declared, False

    if _package_helper_present():
        return "package", True
    if _compose_helper_present():
        return "compose", True
    if _in_container():
        return "container", False
    return "source", False


PackageChannel = Literal["stable", "testing"]

# Written by scripts/setup/install.sh (add_signed_repo); both are world-readable.
APT_SOURCE = Path("/etc/apt/sources.list.d/meshloom.list")
YUM_REPO = Path("/etc/yum.repos.d/meshloom.repo")
_APT_REPO_SUFFIX = "/meshloom/apt"


def _read_small(path: Path) -> str | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(8192)
    except OSError:
        return None


def _apt_channel(text: str) -> PackageChannel | None:
    for raw in text.splitlines():
        tokens = raw.split("#", 1)[0].split()
        if not tokens or tokens[0] != "deb":
            continue
        for i, token in enumerate(tokens[:-1]):
            if token.rstrip("/").endswith(_APT_REPO_SUFFIX):
                suite = tokens[i + 1]
                return "testing" if suite == "testing" else "stable"
    return None


def _yum_channel(text: str) -> PackageChannel | None:
    for raw in text.splitlines():
        key, sep, value = raw.partition("=")
        if not sep or key.strip().lower() != "baseurl":
            continue
        url = value.strip()
        if "/meshloom/rpm/" not in url:
            continue
        return "testing" if "/meshloom/rpm/testing/" in url else "stable"
    return None


def package_channel() -> PackageChannel | None:
    """Which suite of the signed repository this package install follows.

    ``testing`` is what the pre-release installer writes: it serves the release
    candidates (and the stable release when no candidate is newer), so the in-app
    update installs whatever that suite holds. ``None`` when neither source is found.
    """
    apt = _read_small(APT_SOURCE)
    if apt is not None:
        channel = _apt_channel(apt)
        if channel is not None:
            return channel
    yum = _read_small(YUM_REPO)
    if yum is not None:
        return _yum_channel(yum)
    return None
