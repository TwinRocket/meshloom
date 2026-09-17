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


def _package_helper_present() -> bool:
    return APPLY_UPDATE_BIN.exists() or UPDATE_SERVICE.exists()


def _compose_helper_present() -> bool:
    helper = (os.environ.get("MESHLOOM_UPDATE_HELPER") or "").strip().lower()
    return helper == "compose" or COMPOSE_HELPER_SENTINEL.exists()


def detect_install_kind() -> tuple[InstallKind, bool]:
    """Return ``(kind, apply_supported)``.

    Addon is only ``MESHLOOM_INSTALL_KIND=addon`` — never inferred from
    Supervisor tokens or other Home Assistant signals.
    """
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
