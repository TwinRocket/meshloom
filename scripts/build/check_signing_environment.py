#!/usr/bin/env python3
"""Fail unless the `release-signing` environment limits its secrets to release refs.

Key-holding jobs declare this environment, and GitHub creates a missing one with no
protection rule at all. So a release checks it first, in a job that runs before any
job naming the environment. Settings like these are readable with no privilege on a
public repository; the token, when there is one, only spares the anonymous rate limit.

Usage: check_signing_environment.py OWNER/REPO [--api-url URL]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

ENVIRONMENT = "release-signing"
CATCH_ALL = {"*", "**", "*/**", "**/*"}
ATTEMPTS = 3


class Refused(Exception):
    pass


def _get(url: str, token: str) -> tuple[int, dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "meshloom-release"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as exc:
        return exc.code, {}


def fetch(url: str, token: str) -> tuple[int, dict]:
    """The token first, then none. A 404 is an answer, anything else is retried."""
    status = 0
    for attempt in range(ATTEMPTS):
        for credential in (token, "") if token else ("",):
            try:
                status, body = _get(url, credential)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                continue
            if status in (200, 404):
                return status, body
        if attempt + 1 < ATTEMPTS:
            time.sleep(float(os.environ.get("MESHLOOM_API_RETRY_SECONDS", "5")))
    raise Refused(f"could not read {url} (last HTTP status {status})")


def check(api: str, repo: str, token: str) -> list[str]:
    base = f"{api}/repos/{repo}/environments/{ENVIRONMENT}"
    how = "see docs/dev/releasing.md, 'Who can read the key'"
    status, environment = fetch(base, token)
    if status == 404:
        raise Refused(f"the environment {ENVIRONMENT} does not exist: {how}")
    policy = environment.get("deployment_branch_policy") or {}
    if not policy.get("custom_branch_policies"):
        raise Refused(f"{ENVIRONMENT} is not limited to selected branches and tags: {how}")
    status, listing = fetch(f"{base}/deployment-branch-policies", token)
    names = [p.get("name", "") for p in listing.get("branch_policies", [])] if status == 200 else []
    if not names:
        raise Refused(f"{ENVIRONMENT} lists no allowed branch or tag: {how}")
    if broad := CATCH_ALL.intersection(names):
        raise Refused(f"{ENVIRONMENT} allows every ref ({', '.join(sorted(broad))}): {how}")
    return names


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo")
    parser.add_argument("--api-url", default="https://api.github.com")
    args = parser.parse_args()
    try:
        names = check(args.api_url, args.repo, os.environ.get("GH_TOKEN", ""))
    except Refused as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    print(f"ok    {ENVIRONMENT} is limited to: {', '.join(names)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
