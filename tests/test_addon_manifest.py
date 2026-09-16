"""The Home Assistant add-on manifest, checked against what it has to agree with."""

from __future__ import annotations

from pathlib import Path

import yaml

ADDON = Path(__file__).resolve().parents[1] / "meshloom"


def test_the_repository_manifest_sits_where_home_assistant_looks() -> None:
    """At the repository root, beside a folder per add-on.

    Anywhere else and adding the repository in Home Assistant finds nothing: the
    dialog accepts the URL and then reports no add-ons, which reads as a broken
    project rather than a misplaced file.
    """
    root = ADDON.parent
    assert (root / "repository.yaml").is_file()
    assert (ADDON / "config.yaml").is_file()


def _config() -> dict:
    return yaml.safe_load((ADDON / "config.yaml").read_text(encoding="utf-8"))


def test_the_published_port_and_the_option_are_the_same_port() -> None:
    """Two places name the proxy's container port, and they must not drift.

    `ports` is what Home Assistant forwards; `options.proxy_port` is what run.sh
    hands to Meshloom. If they disagree the proxy listens where nothing arrives —
    running, reported as running, reachable by nobody.
    """
    config = _config()
    forwarded = {key.split("/")[0] for key in config["ports"]}
    assert str(config["options"]["proxy_port"]) in forwarded


def test_the_web_interface_is_not_published_on_the_host() -> None:
    """It is reached through ingress, which authenticates with Home Assistant."""
    config = _config()
    assert config["ingress"] is True
    assert config["ingress_port"] == 8000
    assert "8000/tcp" not in config["ports"]


def test_the_radio_can_be_reached() -> None:
    """A USB radio needs the serial devices; without this the add-on sees none."""
    config = _config()
    assert config["uart"] is True


def test_every_option_is_described_by_the_schema() -> None:
    """An option with no schema entry is silently dropped by the supervisor."""
    config = _config()
    assert set(config["options"]) == set(config["schema"])


def test_run_sh_hands_the_host_port_to_meshloom_and_locks_it() -> None:
    """The lock is the point: the app must not offer a field that cannot work."""
    run = (ADDON / "run.sh").read_text(encoding="utf-8")
    assert "MESHCORE_RADIO_PROXY_PORT" in run
    assert "MESHCORE_MANAGED_PORTS" in run


def test_the_readme_button_points_at_this_repository() -> None:
    """The one-click button carries the repository URL, so it can point elsewhere.

    A button that opens the dialog with the wrong URL fails after the reader has
    already agreed to it, which is worse than no button.
    """
    readme = (ADDON.parent / "README.md").read_text(encoding="utf-8")
    assert "supervisor_add_addon_repository" in readme
    assert "repository_url=https%3A%2F%2Fgithub.com%2Fbagl3y%2Fmeshloom" in readme


def test_the_release_script_rewrites_the_addon_version() -> None:
    """The add-on advertises the image it installs; a stale version is a lie.

    Kept honest by the release rather than by discipline — the same reason
    pyproject.toml and package.json are rewritten there.
    """
    publish = (ADDON.parent / "scripts" / "build" / "publish.sh").read_text(encoding="utf-8")
    assert "meshloom/config.yaml" in publish

    config_text = (ADDON / "config.yaml").read_text(encoding="utf-8")
    # The pattern the script substitutes on has to match what the file actually
    # contains, or the rewrite silently does nothing.
    assert any(line.startswith('version: "') for line in config_text.splitlines())
