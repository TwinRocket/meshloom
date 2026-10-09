#!/usr/bin/env python3
"""Fail unless every given .rpm carries an OpenPGP signature in its signature header.

This is a structural check (does a signature tag exist), usable on a runner whose
rpm is too old to verify EdDSA. Cryptographic verification is done by the clients
(`gpgcheck=1`) and by `rpm -K` where the local rpm supports the algorithm.
"""

import struct
import sys

# RPMSIGTAG_DSA, RSA (header-only) and PGP, GPG (header+payload), plus the
# EdDSA / newer OpenPGP tags used by recent rpm (rpm >= 4.19).
SIGNATURE_TAGS = {267, 268, 1002, 1005, 278, 279}
RPM_MAGIC = bytes.fromhex("edabeedb")
HEADER_MAGIC = bytes.fromhex("8eade801")


def signature_tags(data: bytes) -> set[int]:
    if data[:4] != RPM_MAGIC:
        raise ValueError("not an rpm file")
    off = 96  # lead
    magic, _reserved, nindex, _hsize = struct.unpack(">4sIII", data[off : off + 16])
    if magic != HEADER_MAGIC:
        raise ValueError("bad signature header magic")
    tags = set()
    for i in range(nindex):
        start = off + 16 + 16 * i
        tags.add(struct.unpack(">I", data[start : start + 4])[0])
    return tags


def main(paths: list[str]) -> int:
    if not paths:
        print("usage: check_rpm_signed.py FILE.rpm...", file=sys.stderr)
        return 2
    rc = 0
    for path in paths:
        with open(path, "rb") as fh:
            head = fh.read(96 + 16 + 16 * 64)
        try:
            found = signature_tags(head) & SIGNATURE_TAGS
        except (ValueError, struct.error) as exc:
            print(f"::error::{path}: {exc}")
            rc = 1
            continue
        if found:
            print(f"signed    {path}")
        else:
            print(f"::error::{path}: no OpenPGP signature in the rpm header")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
