#!/usr/bin/env python3
"""Fail unless every given .deb carries a valid origin signature from the keyring.

nFPM signs a .deb with `type: origin`: a `_gpgorigin` member holding a detached
signature over the other members, concatenated in archive order. dpkg never checks
it, and apt only checks the repository metadata, so a package without it would be
served under a valid `InRelease`. The members are read here rather than through
`ar`, which is not on every runner.

Usage: check_deb_signed.py --keyring FILE.gpg FILE.deb...
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AR_MAGIC = b"!<arch>\n"
AR_HEADER = 60
SIGNATURE_MEMBER = "_gpgorigin"
CHUNK = 1024 * 1024


class BadPackage(Exception):
    pass


def members(path: Path) -> list[tuple[str, int, int]]:
    """(name, offset, size) of each archive member, in order."""
    found = []
    with path.open("rb") as fh:
        if fh.read(len(AR_MAGIC)) != AR_MAGIC:
            raise BadPackage("not a .deb archive")
        while header := fh.read(AR_HEADER):
            if len(header) != AR_HEADER or header[58:60] != b"`\n":
                raise BadPackage("corrupt archive header")
            name = header[:16].decode("ascii", "replace").strip().rstrip("/")
            try:
                size = int(header[48:58])
            except ValueError as exc:
                raise BadPackage("corrupt archive header") from exc
            found.append((name, fh.tell(), size))
            fh.seek(size + size % 2, 1)
    return found


def verify(path: Path, keyring: Path) -> None:
    parts = members(path)
    signatures = [part for part in parts if part[0] == SIGNATURE_MEMBER]
    signed = [part for part in parts if part[0] != SIGNATURE_MEMBER]
    if len(signatures) != 1:
        raise BadPackage(f"{len(signatures)} {SIGNATURE_MEMBER} members, expected 1")
    gpgv = shutil.which("gpgv")
    if gpgv is None:
        raise BadPackage("gpgv is not installed")
    _, sig_offset, sig_size = signatures[0]
    with tempfile.NamedTemporaryFile(suffix=".sig") as sig, path.open("rb") as fh:
        fh.seek(sig_offset)
        sig.write(fh.read(sig_size))
        sig.flush()
        proc = subprocess.Popen(
            [gpgv, "--keyring", str(keyring.resolve()), sig.name, "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdin is not None
        try:
            for _, offset, size in signed:
                fh.seek(offset)
                left = size
                while left > 0:
                    chunk = fh.read(min(CHUNK, left))
                    if not chunk:
                        raise BadPackage("truncated member")
                    proc.stdin.write(chunk)
                    left -= len(chunk)
            proc.stdin.close()
        except BrokenPipeError:
            pass
        if proc.wait() != 0:
            raise BadPackage("the signature does not verify against the keyring")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keyring", type=Path, required=True)
    parser.add_argument("packages", type=Path, nargs="+")
    args = parser.parse_args()
    status = 0
    for path in args.packages:
        try:
            verify(path, args.keyring)
        except (BadPackage, OSError) as exc:
            print(f"::error::{path}: {exc}")
            status = 1
        else:
            print(f"signed    {path}")
    return status


if __name__ == "__main__":
    sys.exit(main())
