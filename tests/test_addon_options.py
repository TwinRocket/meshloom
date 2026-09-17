"""The add-on's options-to-environment translation.

This is the piece that was a shell script the published image could not even run,
which is how it went unnoticed: nothing could exercise it. In Python it is ordinary
code with ordinary tests.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ENTRYPOINT = Path(__file__).resolve().parents[1] / "meshloom" / "run.py"

_spec = importlib.util.spec_from_file_location("meshloom_addon_entrypoint", ENTRYPOINT)
assert _spec and _spec.loader
addon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(addon)


def test_the_port_the_host_chose_reaches_meshloom() -> None:
    env = addon.build_environment({"proxy_port": 5055})
    assert env["MESHCORE_RADIO_PROXY_PORT"] == "5055"
    # And Meshloom is told the port is not its to offer.
    assert env["MESHCORE_MANAGED_PORTS"] == "true"


def test_a_nonsense_port_is_ignored_rather_than_passed_on() -> None:
    for value in (0, 70000, -1, "5051", None):
        assert "MESHCORE_RADIO_PROXY_PORT" not in addon.build_environment({"proxy_port": value})


def test_blank_options_are_left_unset_rather_than_set_to_nothing() -> None:
    """An empty string would override Meshloom's own default with nothing."""
    env = addon.build_environment({"public_url": "   ", "vapid_subject": ""})
    assert "MESHCORE_PUBLIC_URL" not in env
    assert "MESHCORE_VAPID_SUBJECT" not in env


def test_values_are_trimmed_before_they_are_passed_on() -> None:
    env = addon.build_environment({"public_url": "  https://mesh.example.org  "})
    assert env["MESHCORE_PUBLIC_URL"] == "https://mesh.example.org"


def test_the_database_lives_where_the_addon_keeps_its_data() -> None:
    assert addon.build_environment({})["MESHCORE_DATABASE_PATH"] == "/config/meshcore.db"


def test_bots_are_only_disabled_when_actually_asked() -> None:
    assert "MESHCORE_DISABLE_BOTS" not in addon.build_environment({"disable_bots": False})
    assert addon.build_environment({"disable_bots": True})["MESHCORE_DISABLE_BOTS"] == "true"


def test_a_missing_options_file_does_not_stop_the_addon(tmp_path: Path) -> None:
    """Meshloom's defaults are serviceable; a container that will not start says
    far less about what is wrong than one that starts and logs it."""
    assert addon.read_options(tmp_path / "absent.json") == {}


def test_a_malformed_options_file_does_not_stop_it_either(tmp_path: Path) -> None:
    broken = tmp_path / "options.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert addon.read_options(broken) == {}


def test_options_are_read_as_written(tmp_path: Path) -> None:
    written = tmp_path / "options.json"
    written.write_text(json.dumps({"log_level": "DEBUG", "proxy_port": 5051}), encoding="utf-8")
    assert addon.read_options(written) == {"log_level": "DEBUG", "proxy_port": 5051}


def test_every_option_in_the_manifest_is_understood_here() -> None:
    """An option the manifest offers and this ignores is a control that does nothing."""
    import yaml

    config = yaml.safe_load((ENTRYPOINT.parent / "config.yaml").read_text(encoding="utf-8"))
    handled = set(addon.PASS_THROUGH) | {"proxy_port", "disable_bots"}
    assert set(config["options"]) <= handled


def test_the_addon_declares_its_install_kind() -> None:
    """The updater must not guess Home Assistant; this entrypoint names it."""
    assert addon.build_environment({})["MESHLOOM_INSTALL_KIND"] == "addon"


def test_the_addon_allows_home_assistant_to_frame_it() -> None:
    """Ingress is an iframe on Home Assistant's origin.

    The default headers refuse framing outright, which shows as a blank panel and
    a healthy 200 in the log — the browser gives up before requesting an asset.
    """
    env = addon.build_environment({})
    assert env["MESHCORE_EMBEDDABLE_SAME_ORIGIN"] == "true"
