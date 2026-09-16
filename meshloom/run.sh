#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
set -euo pipefail

# Home Assistant hands options to an add-on as a JSON file, not as environment
# variables, so this is the one piece of glue the add-on needs: read the options
# and hand them to Meshloom the way Meshloom already expects them.

export MESHCORE_DATABASE_PATH="/config/meshcore.db"
export MESHCORE_LOG_LEVEL="$(bashio::config 'log_level')"

# The port is decided here rather than in the app. Home Assistant maps a fixed
# container port and lets the reachable port be remapped in its Network panel; a
# different value inside Meshloom would leave the proxy listening where nothing is
# forwarded — a proxy that reports itself as running and that nobody can reach.
# MESHCORE_MANAGED_PORTS tells Meshloom to say so instead of offering the field.
export MESHCORE_RADIO_PROXY_PORT="$(bashio::config 'proxy_port')"
export MESHCORE_MANAGED_PORTS="true"

for option in public_url basic_auth_username basic_auth_password vapid_subject; do
  if bashio::config.has_value "${option}"; then
    # MESHCORE_PUBLIC_URL, MESHCORE_BASIC_AUTH_USERNAME, and so on.
    export "MESHCORE_${option^^}"="$(bashio::config "${option}")"
  fi
done

if bashio::config.true 'disable_bots'; then
  export MESHCORE_DISABLE_BOTS="true"
fi

if ! bashio::config.has_value 'basic_auth_password'; then
  # Not fatal: ingress authenticates with Home Assistant, and the port published on
  # the host is the proxy rather than the web interface. Worth saying out loud all
  # the same, because the app can run bots.
  bashio::log.notice "No Basic Auth password set; the web interface is reachable through ingress only."
fi

bashio::log.info "Starting Meshloom on port 8000, radio proxy on ${MESHCORE_RADIO_PROXY_PORT}"
exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
