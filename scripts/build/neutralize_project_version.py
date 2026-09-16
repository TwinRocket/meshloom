#!/usr/bin/env python3
"""Rewrite this project's own version to a constant in the dependency manifests.

A release bumps the version in pyproject.toml and uv.lock and changes nothing
else in them. Those two files are what the Docker dependency layer is keyed on,
so the release — the one build that can least afford recompiling eight C
extensions under emulation for armv7 — was the build guaranteed to miss the
layer cache.

Only this project's own entry is touched. uv.lock carries a version line for
every package it pins, and rewriting those would describe a lockfile that does
not exist.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLACEHOLDER = "0.0.0"

_PROJECT_VERSION = re.compile(
    r"(?P<head>^\[project\](?:(?!^\[).)*?^version = \")[^\"]*(?P<tail>\")",
    re.MULTILINE | re.DOTALL,
)


def neutralize_pyproject(text: str) -> str:
    """The version under ``[project]``, and no other table's."""
    replaced, count = _PROJECT_VERSION.subn(rf"\g<head>{PLACEHOLDER}\g<tail>", text, count=1)
    if count != 1:
        raise ValueError("no [project] version found in pyproject.toml")
    return replaced


def neutralize_lock(text: str, project_name: str) -> str:
    """The version of the package named ``project_name``, and no other."""
    pattern = re.compile(
        rf"(?P<head>^name = \"{re.escape(project_name)}\"\nversion = \")[^\"]*(?P<tail>\")",
        re.MULTILINE,
    )
    replaced, count = pattern.subn(rf"\g<head>{PLACEHOLDER}\g<tail>", text, count=1)
    if count != 1:
        raise ValueError(f"no locked entry for {project_name!r} in uv.lock")
    return replaced


def project_name(pyproject_text: str) -> str:
    match = re.search(
        r"^\[project\].*?^name = \"([^\"]+)\"", pyproject_text, re.MULTILINE | re.DOTALL
    )
    if match is None:
        raise ValueError("no [project] name found in pyproject.toml")
    return match.group(1)


def main(directory: str = ".") -> None:
    root = Path(directory)
    pyproject = root / "pyproject.toml"
    lock = root / "uv.lock"

    pyproject_text = pyproject.read_text(encoding="utf-8")
    name = project_name(pyproject_text)

    pyproject.write_text(neutralize_pyproject(pyproject_text), encoding="utf-8")
    lock.write_text(neutralize_lock(lock.read_text(encoding="utf-8"), name), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:])
