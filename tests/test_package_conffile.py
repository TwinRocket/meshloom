"""The packaged /etc/meshloom/meshloom.env is a conffile (``config|noreplace``).

When the shipped copy changes, dpkg asks about every edited copy on a plain
``apt upgrade`` (and a run with no terminal stops at the configure step), and rpm
writes a ``.rpmnew``. 4.18 installers made by a sudo user rewrote the file, so most
4.18 hosts have an edited copy. Keep the shipped file byte for byte as 4.18.3 shipped
it. Its ``MESHCORE_DISABLE_BOTS=true`` line also carries the 4.18 bot lock to
migration 122 (``app/migrations/_122_bots_to_plugins.py``) on an unedited copy.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV = REPO / "pkg" / "nfpm" / "meshloom.env"

# sha256 of pkg/nfpm/meshloom.env at tag 4.18.3.
SHA256_4_18_3 = "7f144d1c5c0089ff8dc47cafc1236eb61453ad4455292167ab1ac156db137d01"


def test_the_conffile_is_the_one_4_18_3_shipped() -> None:
    assert hashlib.sha256(ENV.read_bytes()).hexdigest() == SHA256_4_18_3


def test_the_conffile_keeps_the_4_18_bot_lock_and_the_package_kind() -> None:
    lines = ENV.read_text(encoding="utf-8").splitlines()
    assert "MESHCORE_DISABLE_BOTS=true" in lines
    assert "MESHLOOM_INSTALL_KIND=package" in lines


def test_the_package_ships_it_as_a_kept_conffile() -> None:
    spec = (REPO / "pkg" / "nfpm" / "nfpm.yaml.tmpl").read_text(encoding="utf-8")
    block = spec[spec.index("dst: /etc/meshloom/meshloom.env") :][:120]
    assert "type: config|noreplace" in block
