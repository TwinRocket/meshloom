"""The shipped fail2ban filter and jail stay tied to the AUTH_FAIL contract (MSHL-423).

``fail2ban-regex`` itself runs in CI (``scripts/test/fail2ban_filter.sh``, workflow
``nfpm.yml``, on the unpacked .deb). This test needs no fail2ban: it turns the
filter's ``failregex`` into a Python regex and holds it to the contract page.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
F2B = ROOT / "pkg" / "nfpm" / "fail2ban"
FILTER = (F2B / "meshloom-filter.conf").read_text(encoding="utf-8")
JAIL = (F2B / "meshloom-jail.conf").read_text(encoding="utf-8")
NFPM = (ROOT / "pkg" / "nfpm" / "nfpm.yaml.tmpl").read_text(encoding="utf-8")
CONTRACT = (ROOT / "docs" / "dev" / "auth-fail-log.md").read_text(encoding="utf-8")

SYSTEMD_PREFIX = "host uvicorn[812]: "


def _failregex() -> str:
    m = re.search(r"^failregex = (.+)$", FILTER, re.MULTILINE)
    assert m, "no failregex"
    return m.group(1)


def _contract_regex() -> str:
    m = re.search(r"^(\^\\d\{4\}.*AUTH_FAIL .*\$)$", CONTRACT, re.MULTILINE)
    assert m, "the contract regex is not on its page"
    return m.group(1)


def _sample_lines() -> list[str]:
    return re.findall(
        r"^\d{4}-\d{2}-\d{2} [\d:]{8} - meshloom\.auth - WARNING - AUTH_FAIL .*$",
        CONTRACT,
        re.MULTILINE,
    )


def _compiled() -> re.Pattern[str]:
    # <ADDR> is fail2ban's IP group; the contract names it (?P<host>\S+).
    return re.compile(_failregex().replace("<ADDR>", r"(?P<host>\S+)"))


def test_failregex_is_the_contract_regex_with_an_optional_date_and_prefix() -> None:
    contract = _contract_regex()
    date = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    assert contract.startswith("^" + date + " - meshloom")
    tail = contract[len("^" + date) :].replace(r"(?P<host>\S+)", "<ADDR>")
    # Same fields, same order, same proxied=0 ending; only the leading part is relaxed.
    assert _failregex().endswith(r"\s*" + tail.lstrip(" "))


def test_failregex_matches_the_contract_samples_and_never_proxied_lines() -> None:
    samples = _sample_lines()
    assert any("proxied=1" in s for s in samples) and any("proxied=0" in s for s in samples)
    rx = _compiled()
    contract = re.compile(_contract_regex())
    for line in samples:
        expected = "proxied=0" in line
        # A log file: fail2ban cuts the date off the front before matching.
        undated = line.split(" ", 2)[2]
        # The systemd backend: "<host> <ident>[<pid>]: <message>", date kept in the message.
        for form in (undated, SYSTEMD_PREFIX + line):
            assert bool(rx.match(form)) is expected, form
        # Whatever the contract regex accepts, the filter accepts with the same address.
        if contract.match(line):
            assert rx.match(SYSTEMD_PREFIX + line).group("host") == contract.match(line).group(
                "host"
            )  # type: ignore[union-attr]


def test_failregex_ignores_lines_without_an_address_or_from_another_logger() -> None:
    rx = _compiled()
    base = "2026-10-27 10:15:02 - meshloom.auth - WARNING - AUTH_FAIL ip=%s route=login reason=x via=lan proxied=0 user=-"
    assert rx.match(base % "203.0.113.7")
    assert rx.match(base % "2001:db8::1")
    assert rx.match((base % "203.0.113.7").replace("meshloom.auth", "meshloom.other")) is None
    assert rx.match((base % "203.0.113.7").replace("WARNING", "INFO")) is None


def test_datepattern_is_the_local_time_of_the_contract() -> None:
    assert "datepattern = ^%%Y-%%m-%%d %%H:%%M:%%S" in FILTER
    assert "%Y-%m-%d %H:%M:%S" in CONTRACT


def test_jail_ships_disabled_and_reads_the_journal_of_the_unit() -> None:
    assert re.search(r"^enabled = false$", JAIL, re.MULTILINE)
    assert re.search(r"^filter = meshloom$", JAIL, re.MULTILINE)
    assert re.search(r"^backend = systemd$", JAIL, re.MULTILINE)
    assert re.search(r"^journalmatch = _SYSTEMD_UNIT=meshloom\.service$", JAIL, re.MULTILINE)
    for line in ("maxretry = 5", "findtime = 10m", "bantime = 1h", "bantime.increment = true"):
        assert line in JAIL


def test_packages_install_both_files_as_kept_config_and_only_suggest_fail2ban() -> None:
    for src, dst in (
        ("fail2ban/meshloom-filter.conf", "/etc/fail2ban/filter.d/meshloom.conf"),
        ("fail2ban/meshloom-jail.conf", "/etc/fail2ban/jail.d/meshloom.conf"),
    ):
        block = f"  - src: __PKGDIR__/{src}\n    dst: {dst}\n    type: config|noreplace\n"
        assert block in NFPM
    assert NFPM.count("      - fail2ban\n") == 2
    depends = re.findall(r"depends:\n((?:      - .+\n)+)", NFPM)
    assert depends and all("fail2ban" not in d for d in depends)
    assert "    suggests:\n      - fail2ban" in NFPM
