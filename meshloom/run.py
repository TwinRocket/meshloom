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
    "public_url": "MESHCORE_PUBLIC_URL",
    "basic_auth_username": "MESHCORE_BASIC_AUTH_USERNAME",
    "basic_auth_password": "MESHCORE_BASIC_AUTH_PASSWORD",
    "vapid_subject": "MESHCORE_VAPID_SUBJECT",
}


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
    }

    for option, variable in PASS_THROUGH.items():
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

    os.execvp(
        "uv",
        ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
    )


if __name__ == "__main__":
    main()
