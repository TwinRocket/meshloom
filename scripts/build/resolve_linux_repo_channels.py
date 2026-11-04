#!/usr/bin/env python3
"""Decide which release each suite of the apt/dnf repository serves.

GitHub Pages replaces the whole site on every deployment, so every publication
rebuilds both suites. This derives them from the published releases, whichever
release started the run, so any run leaves the same repository:

  stable   the release asked for when it is X.Y.Z, otherwise GitHub's latest
           release. Only ever a strict X.Y.Z release not flagged as a pre-release.
  testing  the highest complete X.Y.Z-rc.N newer than stable, otherwise stable.
           A tester therefore moves on to the final release once it is out.

An rc that cannot be served is skipped, with a warning, unless it was asked for
by name: a broken candidate must not stop a stable publication. What can only be
seen in its packages (signature, version, size) gets the same treatment in
fetch_linux_repo_packages.sh. Everything else fails closed, before anything is built.

Input: one JSON object per line ({tag_name, draft, prerelease, assets: [names]}).
Output, for $GITHUB_OUTPUT: stable=<tag> and testing=<tag>.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

STABLE_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)$")
RC_RE = re.compile(r"^([0-9]+)\.([0-9]+)\.([0-9]+)-rc\.([1-9][0-9]*)$")
RC_PACKAGES = ("_amd64.deb", "_arm64.deb", ".x86_64.rpm", ".aarch64.rpm")


class Refused(Exception):
    pass


def _sort_key(tag: str) -> tuple[int, ...]:
    """An rc sorts before its final release, and rc.10 after rc.9."""
    if match := STABLE_RE.match(tag):
        return (*map(int, match.groups()), 1, 0)
    if match := RC_RE.match(tag):
        major, minor, patch, number = map(int, match.groups())
        return (major, minor, patch, 0, number)
    raise Refused(f"'{tag}' is not X.Y.Z or X.Y.Z-rc.N")


def _missing_packages(release: dict) -> list[str]:
    names = release.get("assets") or []
    return [s for s in RC_PACKAGES if not any(name.endswith(s) for name in names)]


def _stable_tag(version: str, latest: str, published: dict[str, dict]) -> str:
    tag = version if STABLE_RE.match(version) else latest
    if not STABLE_RE.match(tag):
        raise Refused(f"stable must be served from a strict release: '{tag}' is not X.Y.Z")
    release = published.get(tag)
    if release is None:
        raise Refused(f"no published release {tag}")
    if release.get("prerelease"):
        raise Refused(f"{tag} is flagged pre-release: it cannot be served from stable")
    return tag


def _check_requested_rc(version: str, published: dict[str, dict]) -> None:
    release = published.get(version)
    if release is None:
        raise Refused(f"no published release {version}")
    if not release.get("prerelease"):
        raise Refused(f"{version} is not flagged as a pre-release on GitHub")
    if missing := _missing_packages(release):
        raise Refused(f"{version} lacks packages: {', '.join(missing)}")


def _testing_tag(stable: str, published: dict[str, dict]) -> str:
    candidates = []
    for tag, release in published.items():
        if not RC_RE.match(tag):
            continue
        if not release.get("prerelease"):
            print(f"::warning::{tag} is not flagged as a pre-release: not served", file=sys.stderr)
        elif missing := _missing_packages(release):
            print(f"::warning::{tag} lacks {', '.join(missing)}: not served", file=sys.stderr)
        else:
            candidates.append(tag)
    newest = max(candidates, key=_sort_key, default=None)
    if newest is not None and _sort_key(newest) > _sort_key(stable):
        return newest
    return stable


def resolve(
    releases: list[dict], version: str, latest: str, *, require_armhf: bool = False
) -> tuple[str, str]:
    is_rc = bool(RC_RE.match(version))
    if version and not is_rc and not STABLE_RE.match(version):
        raise Refused(f"'{version}' is not X.Y.Z or X.Y.Z-rc.N")
    if is_rc and require_armhf:
        raise Refused(f"{version}: a release candidate has no armhf package")
    published = {r["tag_name"]: r for r in releases if not r.get("draft")}
    stable = _stable_tag(version, latest, published)
    if is_rc:
        _check_requested_rc(version, published)
    return stable, _testing_tag(stable, published)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--releases", type=Path, required=True)
    parser.add_argument("--latest", default="", help="tag of GitHub's latest release")
    parser.add_argument("--version", default="", help="release this run publishes, if any")
    parser.add_argument("--require-armhf", action="store_true")
    args = parser.parse_args()

    lines = args.releases.read_text(encoding="utf-8").splitlines()
    releases = [json.loads(line) for line in lines if line.strip()]
    try:
        stable, testing = resolve(
            releases, args.version, args.latest, require_armhf=args.require_armhf
        )
    except Refused as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(f"stable={stable}")
    print(f"testing={testing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
