"""Which release channel this machine follows, and what the testing channel serves.

The channel is the system's choice, made by the installer in places only root can write.
This module only reads them (docs/dev/releasing.md, "Channels"):

- package: the apt or dnf source itself, so what is shown cannot drift from what gets
  installed;
- compose with the update helper: ``status.json``, which the helper writes in a directory
  mounted read-only, from its root-owned configuration;
- anything else (a hand-written compose, a source checkout): the version that runs, which
  the operator picked and the app cannot change. The add-on has no pre-release path.

What the testing channel serves comes from ``channels/testing.txt`` on the repository site,
checked with ``gpgv`` against the release key (the check the compose helper makes on
``OCI-DIGESTS``). Nothing here gets a machine moved: on a package install the file only
feeds the badge, apt and dnf install what the signed suite serves.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx
from packaging.version import InvalidVersion, Version

from app.services.update_apply import read_helper_channel, secure_compose_helper

logger = logging.getLogger(__name__)

Channel = Literal["stable", "testing"]

CHANNEL_FILE_URL = "https://twinrocket.github.io/meshloom/channels/testing.txt"
CHANNEL_FILE_TIMEOUT_SECONDS = 10.0
CHANNEL_FILE_MAX_BYTES = 4096
GPGV_TIMEOUT_SECONDS = 15.0

APT_SOURCES_DIR = Path("/etc/apt/sources.list.d")
DNF_REPO_FILE = Path("/etc/yum.repos.d/meshloom.repo")
SYSTEM_KEYRING = Path("/usr/share/keyrings/meshloom-archive-keyring.gpg")
CHECKOUT_KEYRING = (
    Path(__file__).resolve().parents[2] / "pkg" / "keys" / ("meshloom-archive-keyring.gpg")
)
_OFFICIAL_APT = re.compile(r"(?:^|\s)https://twinrocket\.github\.io/meshloom/apt/?\s+(\S+)")
_DEB822_URI = re.compile(
    r"^URIs:(?:[ \t]+\S+)*?[ \t]+https://twinrocket\.github\.io/meshloom/apt/?(?:[ \t]|$)",
    re.MULTILINE | re.IGNORECASE,
)
_DEB822_TESTING = re.compile(r"^Suites:.*\btesting\b", re.MULTILINE | re.IGNORECASE)
_DNF_TESTING = re.compile(
    r"^\s*baseurl\s*=\s*https://twinrocket\.github\.io/meshloom/rpm/testing/", re.MULTILINE
)
_CHANNEL_FILE_RE = re.compile(
    r"schema=1\n"
    r"channel=testing\n"
    r"version=(?P<version>[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}(?:-rc\.[1-9][0-9]{0,5})?)\n"
    r"stable=(?P<stable>[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6})\n"
    r"issued_at=(?P<issued_at>[0-9]{1,12})\n"
)

# The newest copy accepted since this process started: an older one is a replay. The
# worst a replay can do is hide a newer release: only something newer than what runs is
# ever offered, apt and dnf have their own signed metadata, and the compose helper keeps
# the same check on disk because it acts on the file.
_newest_issued_at = 0


@dataclass(frozen=True)
class ChannelRelease:
    version: str
    stable: str
    issued_at: int


def _is_root_only(path: Path) -> bool:
    """Regular file owned by root, written by nobody else. Tests patch this."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == 0
        and not info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    )


def _read_system_file(path: Path) -> str:
    if not _is_root_only(path):
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _apt_source_is_testing() -> bool:
    try:
        entries = sorted(APT_SOURCES_DIR.glob("*.list"))
    except OSError:
        return False
    for entry in entries:
        for line in _read_system_file(entry).splitlines():
            text = line.split("#", 1)[0].strip()
            if not text.startswith("deb"):
                continue
            match = _OFFICIAL_APT.search(text)
            if match and match.group(1) == "testing":
                return True
    # The deb822 form, which `apt modernize-sources` turns the list file into.
    try:
        stanzas = sorted(APT_SOURCES_DIR.glob("*.sources"))
    except OSError:
        return False
    for entry in stanzas:
        for stanza in re.split(r"\n\s*\n", _read_system_file(entry)):
            if _DEB822_URI.search(stanza) and _DEB822_TESTING.search(stanza):
                return True
    return False


def _dnf_source_is_testing() -> bool:
    return bool(_DNF_TESTING.search(_read_system_file(DNF_REPO_FILE)))


def _version_is_prerelease(version: str) -> bool:
    try:
        return Version(version.strip().lstrip("vV")).is_prerelease
    except InvalidVersion:
        return False


def detect_update_channel(kind: str, current: str) -> Channel:
    """The channel this machine follows. Stable unless the system says otherwise."""
    if kind == "addon":
        return "stable"
    if kind == "package":
        return "testing" if _apt_source_is_testing() or _dnf_source_is_testing() else "stable"
    if kind == "compose" and secure_compose_helper():
        return "testing" if read_helper_channel() == "testing" else "stable"
    return "testing" if _version_is_prerelease(current) else "stable"


def parse_channel_file(text: str) -> ChannelRelease | None:
    match = _CHANNEL_FILE_RE.fullmatch(text)
    if match is None:
        return None
    return ChannelRelease(
        version=match["version"], stable=match["stable"], issued_at=int(match["issued_at"])
    )


def _keyring() -> Path | None:
    for path in (SYSTEM_KEYRING, CHECKOUT_KEYRING):
        if path.is_file():
            return path
    return None


async def _signature_is_good(data: bytes, signature: bytes) -> bool:
    """gpgv against the release key. Anything but a clean success is a refusal."""
    keyring = _keyring()
    gpgv = shutil.which("gpgv")
    if keyring is None or gpgv is None:
        logger.warning("Cannot check the channel file: gpgv or the release key is missing")
        return False
    with tempfile.TemporaryDirectory(prefix="meshloom-channel-") as tmp:
        data_path = os.path.join(tmp, "testing.txt")
        signature_path = data_path + ".asc"
        Path(data_path).write_bytes(data)
        Path(signature_path).write_bytes(signature)
        proc = await asyncio.create_subprocess_exec(
            gpgv,
            "--keyring",
            str(keyring),
            signature_path,
            data_path,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(proc.wait(), timeout=GPGV_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return False
    return proc.returncode == 0


async def _download(client: httpx.AsyncClient, url: str) -> bytes | None:
    response = await client.get(url)
    if response.status_code != 200 or len(response.content) > CHANNEL_FILE_MAX_BYTES:
        logger.info("Channel file %s: HTTP %s", url.rsplit("/", 1)[-1], response.status_code)
        return None
    return response.content


async def fetch_channel_release() -> ChannelRelease | None:
    """What the testing channel serves, or None when it cannot be trusted right now."""
    global _newest_issued_at
    try:
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=CHANNEL_FILE_TIMEOUT_SECONDS
        ) as client:
            data = await _download(client, CHANNEL_FILE_URL)
            signature = await _download(client, f"{CHANNEL_FILE_URL}.asc")
    except httpx.RequestError as exc:
        logger.info("Channel file lookup failed: %s", exc)
        return None
    if data is None or signature is None:
        return None
    if not await _signature_is_good(data, signature):
        logger.warning("Channel file refused: its signature does not check out")
        return None
    release = parse_channel_file(data.decode("utf-8", errors="replace"))
    if release is None:
        logger.warning("Channel file refused: unexpected content")
        return None
    if release.issued_at < _newest_issued_at:
        logger.warning("Channel file refused: older than the copy already accepted")
        return None
    _newest_issued_at = release.issued_at
    return release


def reset_channel_state() -> None:
    """Forget the newest copy seen (tests)."""
    global _newest_issued_at
    _newest_issued_at = 0
