"""The dependency layer must see the same manifests from one release to the next."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "build" / "neutralize_project_version.py"

_spec = importlib.util.spec_from_file_location("neutralize_project_version", SCRIPT)
assert _spec and _spec.loader
neutralize = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(neutralize)


def _real_manifests() -> tuple[str, str]:
    return (
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        (REPO_ROOT / "uv.lock").read_text(encoding="utf-8"),
    )


def test_two_releases_hand_the_layer_the_same_bytes() -> None:
    """The whole point: a version bump must not reach the dependency layer."""
    pyproject, lock = _real_manifests()
    name = neutralize.project_name(pyproject)

    def release(version: str) -> tuple[str, str]:
        bumped_py = re.sub(
            r'^version = "[^"]*"', f'version = "{version}"', pyproject, count=1, flags=re.MULTILINE
        )
        bumped_lock = lock.replace(
            f'name = "{name}"\nversion = ', f'name = "{name}"\nversion = ', 1
        )
        bumped_lock = re.sub(
            rf'(^name = "{re.escape(name)}"\nversion = ")[^"]*"',
            rf'\g<1>{version}"',
            bumped_lock,
            count=1,
            flags=re.MULTILINE,
        )
        return (
            neutralize.neutralize_pyproject(bumped_py),
            neutralize.neutralize_lock(bumped_lock, name),
        )

    assert release("4.10.0") == release("9.99.0")


def test_only_the_project_version_moves() -> None:
    """uv.lock pins a version per package; rewriting those describes a lockfile
    that does not exist."""
    pyproject, lock = _real_manifests()
    name = neutralize.project_name(pyproject)

    changed = [
        (before, after)
        for before, after in zip(
            lock.splitlines(), neutralize.neutralize_lock(lock, name).splitlines(), strict=True
        )
        if before != after
    ]
    assert len(changed) == 1
    assert changed[0][1] == f'version = "{neutralize.PLACEHOLDER}"'


def test_the_running_app_still_reads_a_real_version() -> None:
    """The image copies the real manifests back after the dependency layer, so
    the placeholder must never be what the repo itself carries."""
    pyproject, _ = _real_manifests()
    assert neutralize.PLACEHOLDER not in pyproject


def test_a_manifest_without_the_project_is_an_error_not_a_silent_pass() -> None:
    with pytest.raises(ValueError):
        neutralize.neutralize_pyproject('[tool.ruff]\nversion = "1.0.0"\n')
    with pytest.raises(ValueError):
        neutralize.neutralize_lock('[[package]]\nname = "other"\nversion = "1.0.0"\n', "meshloom")
