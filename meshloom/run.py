#!/usr/bin/env python3
"""Turn the add-on's options into the environment Meshloom already understands.

Home Assistant writes the options a reader set to a JSON file and starts the
container; it does not turn them into environment variables. Something has to, and
it is this — written in Python because that is what the image has. The convention
elsewhere is a bashio shell script, which needs the Home Assistant base image; this
add-on builds on Meshloom's own published image instead, so bashio is not there.

Exec'ing rather than spawning keeps Meshloom as PID 1, so the supervisor's stop
signal reaches it and a restart is not a kill.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

OPTIONS_FILE = Path("/data/options.json")

# Options whose value is passed straight through when it is not blank.
PASS_THROUGH = {
    "log_level": "MESHCORE_LOG_LEVEL",
}

# Deprecated options, still declared in the manifest for one release: the Supervisor
# rewrites options.json from the schema at every start and drops undeclared keys, so
# an option removed from the schema would never reach this file. The value is handed
# on so Meshloom copies it into its database once (app/services/legacy_env_settings.py)
# and never reads it again. ``vapid_subject`` now lives in Settings → Notifications;
# ``basic_auth_*`` become the administrator account once, when the database has
# no account yet (app/auth/setup_code.py), since Home Assistant's own sign-in
# identifies ingress users (app/auth/providers/ingress.py).
# Remove them here and from config.yaml in the next release.
RETIRED_PASS_THROUGH = {
    "vapid_subject": "MESHCORE_VAPID_SUBJECT",
    "basic_auth_username": "MESHCORE_BASIC_AUTH_USERNAME",
    "basic_auth_password": "MESHCORE_BASIC_AUTH_PASSWORD",
}


# ``--no-proxy-headers``: uvicorn would otherwise take the client address from
# ``X-Forwarded-For`` sent by any loopback caller (its default
# ``forwarded_allow_ips`` is 127.0.0.1), so a bot or a webhook aimed at
# 127.0.0.1:8000 could pass for the Supervisor and mint a Home Assistant
# identity. The app reads proxy headers in one place (app/origin_policy.py).
UVICORN_ARGV = [
    "uv",
    "run",
    "uvicorn",
    "app.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--no-proxy-headers",
]


def read_options(path: Path = OPTIONS_FILE) -> dict[str, object]:
    """The options Home Assistant wrote, or none at all.

    A missing or malformed file must not stop the add-on: Meshloom's own defaults
    are serviceable, and a container that will not start says far less about what
    is wrong than one that starts and logs it.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print("No options file; starting with defaults", file=sys.stderr)
    except (OSError, json.JSONDecodeError) as error:
        print(f"Could not read options ({error}); starting with defaults", file=sys.stderr)
    return {}


def build_environment(options: dict[str, object]) -> dict[str, str]:
    """The environment those options describe."""
    env: dict[str, str] = {
        # The add-on's own storage, which survives updates and is what gets backed up.
        "MESHCORE_DATABASE_PATH": "/config/meshcore.db",
        # The port is the host's to decide: Home Assistant forwards a fixed container
        # port and the published one is remapped in its Network panel. A different
        # value inside Meshloom would leave the proxy listening where nothing
        # arrives, so Meshloom is told to say so rather than offer the field.
        "MESHCORE_MANAGED_PORTS": "true",
        # Ingress serves this inside an iframe on Home Assistant's own origin, and
        # the default headers refuse to be framed at all — which shows as a blank
        # panel with a perfectly healthy 200 in the log.
        "MESHCORE_EMBEDDABLE_SAME_ORIGIN": "true",
        # Declared, not sniffed: the in-app updater must not offer apt/compose apply.
        "MESHLOOM_INSTALL_KIND": "addon",
    }

    for option, variable in (PASS_THROUGH | RETIRED_PASS_THROUGH).items():
        value = options.get(option)
        if isinstance(value, str) and value.strip():
            env[variable] = value.strip()

    port = options.get("proxy_port")
    if isinstance(port, int) and 0 < port < 65536:
        env["MESHCORE_RADIO_PROXY_PORT"] = str(port)

    if options.get("disable_bots") is True:
        env["MESHCORE_DISABLE_BOTS"] = "true"

    return env


def main() -> None:
    environment = build_environment(read_options())
    os.environ.update(environment)

    proxy_port = environment.get("MESHCORE_RADIO_PROXY_PORT", "unset")
    print(f"Starting Meshloom on port 8000, radio proxy on {proxy_port}", file=sys.stderr)

    os.execvp("uv", UVICORN_ARGV)


if __name__ == "__main__":
    main()
