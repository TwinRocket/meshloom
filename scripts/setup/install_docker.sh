#!/usr/bin/env bash
# install_docker.sh
#
# Generates a local docker-compose.yml for Meshloom from a guided prompt flow.
# The generated compose file is intentionally gitignored so local customization
# does not create merge churn on future pulls.
#
# Run from anywhere inside the repo:
#   bash scripts/setup/install_docker.sh

set -euo pipefail

is_root() { [ "$(id -u)" -eq 0 ]; }

priv() {
    if is_root; then
        printf '%s' "$*"
    else
        printf 'sudo %s' "$*"
    fi
}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
PURPLE='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m'

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_FILE="$REPO_DIR/docker-compose.yml"
EXAMPLE_FILE="$REPO_DIR/docker-compose.example.yml"
SNAKEOIL_CERT_DIR="$REPO_DIR/.docker-certs"
SNAKEOIL_CERT_BASENAME="meshloom-snakeoil.crt"
SNAKEOIL_KEY_BASENAME="meshloom-snakeoil.key"
SNAKEOIL_CERT_HOST_PATH="$SNAKEOIL_CERT_DIR/$SNAKEOIL_CERT_BASENAME"
SNAKEOIL_KEY_HOST_PATH="$SNAKEOIL_CERT_DIR/$SNAKEOIL_KEY_BASENAME"
SNAKEOIL_CERT_CONTAINER_PATH="/app/certs/$SNAKEOIL_CERT_BASENAME"
SNAKEOIL_KEY_CONTAINER_PATH="/app/certs/$SNAKEOIL_KEY_BASENAME"

IMAGE_MODE="image"
TRANSPORT_MODE="serial"
SERIAL_HOST_PATH="/dev/ttyACM0"
SERIAL_COMPOSE_HOST_PATH="/dev/ttyACM0"
SERIAL_CONTAINER_PATH="/dev/meshcore-radio"
TCP_HOST=""
TCP_PORT="5000"
BLE_ADDRESS=""
BLE_PIN=""
RUN_AS_HOST_USER="N"
ENABLE_SNAKEOIL_TLS="Y"
BLE_MANUAL_WARNING=false
LOCAL_ACCESS_IP=""
SERIAL_FOUND_HOST_PATHS=()
SERIAL_FOUND_LABELS=()
SERIAL_FOUND_DISPLAYS=()

find_serial_devices() {
    local path
    local resolved
    local label
    local existing

    SERIAL_FOUND_HOST_PATHS=()
    SERIAL_FOUND_LABELS=()
    SERIAL_FOUND_DISPLAYS=()

    if [ -d /dev/serial/by-id ]; then
        while IFS= read -r path; do
            [ -n "$path" ] || continue
            resolved="$(readlink -f "$path" 2>/dev/null || true)"
            [ -n "$resolved" ] || resolved="$path"
            label="$(basename "$path")"
            SERIAL_FOUND_HOST_PATHS+=("$path")
            SERIAL_FOUND_LABELS+=("$label")
            SERIAL_FOUND_DISPLAYS+=("$path -> $resolved")
        done < <(find /dev/serial/by-id -maxdepth 1 -type l | sort)
    fi

    for path in /dev/ttyACM* /dev/ttyUSB* /dev/cu.usbmodem* /dev/cu.usbserial*; do
        [ -e "$path" ] || continue
        resolved="$(readlink -f "$path" 2>/dev/null || true)"
        [ -n "$resolved" ] || resolved="$path"

        if ((${#SERIAL_FOUND_HOST_PATHS[@]} > 0)); then
            for existing in "${SERIAL_FOUND_DISPLAYS[@]}"; do
                if [[ "$existing" = *"-> $resolved" ]]; then
                    resolved=""
                    break
                fi
            done
            [ -n "$resolved" ] || continue
        fi

        SERIAL_FOUND_HOST_PATHS+=("$path")
        SERIAL_FOUND_LABELS+=("$(basename "$path")")
        SERIAL_FOUND_DISPLAYS+=("$path")
    done
}

yaml_quote() {
    local value="$1"
    value=${value//\'/\'\'}
    printf "'%s'" "$value"
}

normalize_serial_host_path_for_compose() {
    local selected_path="$1"
    local resolved_path=""

    if [[ "$selected_path" != *:* ]]; then
        SERIAL_COMPOSE_HOST_PATH="$selected_path"
        return 0
    fi

    resolved_path="$(readlink -f "$selected_path" 2>/dev/null || true)"
    if [ -z "$resolved_path" ]; then
        echo -e "${RED}Error:${NC} the selected serial path contains ':' and could not be resolved to a raw /dev/tty-style device path."
        echo "Selected path: $selected_path"
        echo "Please enter the raw serial device path instead (for example /dev/ttyACM0)."
        exit 1
    fi

    if [[ "$resolved_path" == *:* ]]; then
        echo -e "${RED}Error:${NC} the selected serial path still resolves to a path containing ':', which Docker Compose cannot use here."
        echo "Selected path: $selected_path"
        echo "Resolved path: $resolved_path"
        echo "Please enter the raw serial device path instead (for example /dev/ttyACM0)."
        exit 1
    fi

    echo -e "${YELLOW}Note:${NC} the selected serial path contains ':', so Docker Compose will use the resolved raw device path instead: ${resolved_path}"
    SERIAL_COMPOSE_HOST_PATH="$resolved_path"
}

detect_primary_local_ip() {
    local ip=""
    local iface=""

    if command -v hostname &>/dev/null; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    fi

    if [ -z "$ip" ] && command -v ip &>/dev/null; then
        ip="$(ip route get 1.1.1.1 2>/dev/null | awk '/src/ {for (i = 1; i <= NF; i++) if ($i == "src") {print $(i + 1); exit}}')"
    fi

    if [ -z "$ip" ] && command -v route &>/dev/null && command -v ipconfig &>/dev/null; then
        iface="$(route -n get default 2>/dev/null | awk '/interface:/{print $2; exit}')"
        if [ -n "$iface" ]; then
            ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
        fi
    fi

    if [ -z "$ip" ]; then
        ip="127.0.0.1"
    fi

    printf '%s' "$ip"
}

ensure_snakeoil_requirements() {
    local dep

    for dep in openssl mktemp; do
        if ! command -v "$dep" &>/dev/null; then
            echo -e "${RED}Error: ${dep} is required to generate the snakeoil TLS certificate.${NC}"
            exit 1
        fi
    done
}

generate_snakeoil_certificate() {
    local san_ip="$1"
    local tmp_config=""

    mkdir -p "$SNAKEOIL_CERT_DIR"
    tmp_config="$(mktemp)"

    cat >"$tmp_config" <<EOF
[req]
default_bits = 2048
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = Meshloom Snakeoil
O = Meshloom for MeshCore

[v3_req]
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
IP.1 = 127.0.0.1
EOF

    if [ -n "$san_ip" ] && [ "$san_ip" != "127.0.0.1" ]; then
        printf 'IP.2 = %s\n' "$san_ip" >>"$tmp_config"
    fi

    openssl req \
        -x509 \
        -nodes \
        -newkey rsa:2048 \
        -days 3650 \
        -keyout "$SNAKEOIL_KEY_HOST_PATH" \
        -out "$SNAKEOIL_CERT_HOST_PATH" \
        -config "$tmp_config" \
        -extensions v3_req >/dev/null 2>&1

    rm -f "$tmp_config"

    chmod 600 "$SNAKEOIL_KEY_HOST_PATH"
    chmod 644 "$SNAKEOIL_CERT_HOST_PATH"
}

if ! command -v docker &>/dev/null; then
    echo -e "${RED}Warning: docker was not found in PATH. A compose file will still be generated, but you will need Docker installed to run it.${NC}"
elif ! docker compose version &>/dev/null; then
    echo -e "${RED}Warning: docker compose is not available. A compose file will still be generated, but you will need the Docker Compose plugin to run it.${NC}"
fi

echo -e "${BOLD}=== Meshloom for MeshCore — Docker Setup ===${NC}"
echo
echo -e "  Repo directory     : ${CYAN}${REPO_DIR}${NC}"
echo -e "  Example compose    : ${CYAN}${EXAMPLE_FILE}${NC}"
echo -e "  Output compose     : ${CYAN}${COMPOSE_FILE}${NC}"
echo

if [ -f "$COMPOSE_FILE" ] && [ -z "${MESHLOOM_NONINTERACTIVE:-}" ]; then
    echo -e "${YELLOW}A local docker-compose.yml already exists.${NC}"
    read -r -p "Overwrite it? [y/N]: " OVERWRITE
    OVERWRITE="${OVERWRITE:-N}"
    if ! [[ "$OVERWRITE" =~ ^[Yy]$ ]]; then
        echo -e "${YELLOW}Leaving the existing compose file untouched.${NC}"
        exit 0
    fi
fi

docker_allows_usb() {
    [ "$(uname -s)" = "Linux" ] || return 1
    docker info 2>/dev/null | grep -qi 'rootless' && return 1
    return 0
}

if [ -n "${MESHLOOM_IMAGE_MODE:-}" ]; then
    IMAGE_MODE="$MESHLOOM_IMAGE_MODE"
else
    echo -e "${BOLD}─── Image Source ────────────────────────────────────────────────────${NC}"
    echo "How should Docker run Meshloom?"
    echo "  1) Use the published GHCR image (default)"
    echo "  2) Build locally from this checkout"
    echo
    read -r -p "Select image mode [1-2] (default: 1): " IMAGE_CHOICE
    IMAGE_CHOICE="${IMAGE_CHOICE:-1}"
    echo

    case "$IMAGE_CHOICE" in
        2) IMAGE_MODE="build" ;;
        *) IMAGE_MODE="image" ;;
    esac
fi
echo -e "${GREEN}Image mode: ${IMAGE_MODE}${NC}"
echo

prompt_docker_serial() {
    TRANSPORT_MODE="serial"
    find_serial_devices

    if ((${#SERIAL_FOUND_HOST_PATHS[@]} == 0)); then
        echo -e "${YELLOW}No serial devices were auto-detected.${NC}"
        read -r -p "Serial device path on the host (default: /dev/ttyACM0): " SERIAL_HOST_PATH
        SERIAL_HOST_PATH="${SERIAL_HOST_PATH:-/dev/ttyACM0}"
    else
        echo "Detected serial devices:"
        for i in "${!SERIAL_FOUND_HOST_PATHS[@]}"; do
            printf '  %d) %s (%s)\n' "$((i + 1))" "${SERIAL_FOUND_LABELS[$i]}" "${SERIAL_FOUND_DISPLAYS[$i]}"
        done
        echo "  m) Enter a path manually"
        echo
        read -r -p "Select serial device [1-${#SERIAL_FOUND_HOST_PATHS[@]} or m] (default: 1): " SERIAL_CHOICE
        SERIAL_CHOICE="${SERIAL_CHOICE:-1}"

        if [[ "$SERIAL_CHOICE" =~ ^[Mm]$ ]]; then
            read -r -p "Serial device path on the host (default: ${SERIAL_FOUND_HOST_PATHS[0]}): " SERIAL_HOST_PATH
            SERIAL_HOST_PATH="${SERIAL_HOST_PATH:-${SERIAL_FOUND_HOST_PATHS[0]}}"
        elif [[ "$SERIAL_CHOICE" =~ ^[0-9]+$ ]] && [ "$SERIAL_CHOICE" -ge 1 ] && [ "$SERIAL_CHOICE" -le "${#SERIAL_FOUND_HOST_PATHS[@]}" ]; then
            SERIAL_HOST_PATH="${SERIAL_FOUND_HOST_PATHS[$((SERIAL_CHOICE - 1))]}"
        else
            SERIAL_HOST_PATH="${SERIAL_FOUND_HOST_PATHS[0]}"
            echo -e "${YELLOW}Invalid selection; defaulting to ${SERIAL_HOST_PATH}.${NC}"
        fi
    fi

    normalize_serial_host_path_for_compose "$SERIAL_HOST_PATH"
    echo -e "${GREEN}Serial passthrough: ${SERIAL_COMPOSE_HOST_PATH} -> ${SERIAL_CONTAINER_PATH}${NC}"
}

if [ -n "${MESHLOOM_TRANSPORT:-}" ]; then
    case "$MESHLOOM_TRANSPORT" in
        serial|serial-auto)
            if ! docker_allows_usb; then
                echo -e "${RED}Error: USB passthrough needs rootful Docker on Linux.${NC}"
                exit 1
            fi
            TRANSPORT_MODE="serial"
            SERIAL_HOST_PATH="${MESHLOOM_SERIAL_PORT:-/dev/ttyACM0}"
            if [ "$MESHLOOM_TRANSPORT" = "serial-auto" ]; then
                if [ -e /dev/ttyACM0 ]; then SERIAL_HOST_PATH="/dev/ttyACM0"
                elif [ -e /dev/ttyUSB0 ]; then SERIAL_HOST_PATH="/dev/ttyUSB0"
                fi
            fi
            normalize_serial_host_path_for_compose "$SERIAL_HOST_PATH"
            echo -e "${GREEN}Serial passthrough: ${SERIAL_COMPOSE_HOST_PATH} -> ${SERIAL_CONTAINER_PATH}${NC}"
            ;;
        tcp)
            TRANSPORT_MODE="tcp"
            echo -e "${GREEN}Network radio: no USB device mapping. Configure TCP in the web UI.${NC}"
            ;;
        *)
            echo -e "${RED}Error: Docker installer does not support MESHLOOM_TRANSPORT=${MESHLOOM_TRANSPORT}${NC}"
            exit 1
            ;;
    esac
    echo
else
    echo -e "${BOLD}─── Transport ───────────────────────────────────────────────────────${NC}"
    echo "How will the container reach your MeshCore radio?"
    if docker_allows_usb; then
        echo "  1) Serial device passthrough (default)"
        echo "  2) TCP"
        echo
        read -r -p "Select transport [1-2] (default: 1): " TRANSPORT_CHOICE
        TRANSPORT_CHOICE="${TRANSPORT_CHOICE:-1}"
        echo
        case "$TRANSPORT_CHOICE" in
            2)
                TRANSPORT_MODE="tcp"
                echo -e "${GREEN}Network radio: no USB device mapping. Configure TCP in the web UI.${NC}"
                ;;
            *)
                prompt_docker_serial
                ;;
        esac
    else
        echo "This host can only pass a network radio into Docker (USB needs rootful Linux Docker)."
        echo
        TRANSPORT_MODE="tcp"
        echo -e "${GREEN}Network radio: no USB device mapping. Configure TCP in the web UI.${NC}"
    fi
    echo
fi

if [ -n "${MESHLOOM_NONINTERACTIVE:-}" ]; then
    ENABLE_SNAKEOIL_TLS="${MESHLOOM_SNAKEOIL:-N}"
fi

if [ -z "${MESHLOOM_NONINTERACTIVE:-}" ]; then
    echo -e "${BOLD}─── HTTPS / Snakeoil TLS ────────────────────────────────────────────${NC}"
    echo "Generating a local self-signed certificate enables HTTPS-only browser features"
    echo "such as the channel key finder and, in some browsers, notifications."
    echo "Browsers will still warn that the certificate is untrusted."
    echo
    read -r -p "Generate and enable a snakeoil TLS certificate? [Y/n]: " ENABLE_SNAKEOIL_TLS
    ENABLE_SNAKEOIL_TLS="${ENABLE_SNAKEOIL_TLS:-Y}"
fi
LOCAL_ACCESS_IP="$(detect_primary_local_ip)"
if [[ "$ENABLE_SNAKEOIL_TLS" =~ ^[Yy]$ ]]; then
    ensure_snakeoil_requirements
    generate_snakeoil_certificate "$LOCAL_ACCESS_IP"
    echo -e "${GREEN}Generated snakeoil TLS certificate in ${SNAKEOIL_CERT_DIR}.${NC}"
    echo -e "${YELLOW}Browsers will show an untrusted/self-signed certificate warning.${NC}"
else
    echo -e "${GREEN}Skipping snakeoil TLS generation. The container will serve plain HTTP.${NC}"
fi
echo

if [ "$(uname -s)" = "Linux" ] && [ -z "${MESHLOOM_NONINTERACTIVE:-}" ]; then
    echo -e "${BOLD}─── Container User ──────────────────────────────────────────────────${NC}"
    echo "The container runs as root by default for maximum serial compatibility."
    echo "You can override that and run as your host UID/GID instead to avoid"
    echo "root-owned files in ./data."
    echo
    read -r -p "Run as your current UID/GID instead of the default root user? [y/N]: " RUN_AS_HOST_USER
    RUN_AS_HOST_USER="${RUN_AS_HOST_USER:-N}"
    if [[ "$RUN_AS_HOST_USER" =~ ^[Yy]$ ]] && [ "$TRANSPORT_MODE" = "serial" ]; then
        echo
        echo -e "${YELLOW}Note:${NC} host-user mode can be less reliable for serial device access than running as root."
        echo "It may require extra group setup such as dialout, or other manual"
        echo "container customization, depending on your host."
        echo "If serial access becomes unreliable, rerun this setup and keep the"
        echo "default root user instead."
    fi
    echo
fi

mkdir -p "$REPO_DIR/data"

{
    echo "# Generated by scripts/setup/install_docker.sh"
    echo "# This file is gitignored. Re-run the setup script to regenerate it."
    echo "services:"
    echo "  meshloom:"
    if [ "$IMAGE_MODE" = "build" ]; then
        echo "    build: ."
    else
        echo "    image: ghcr.io/twinrocket/meshloom:latest"
    fi
    if [[ "$RUN_AS_HOST_USER" =~ ^[Yy]$ ]]; then
        echo "    user: \"$(id -u):$(id -g)\""
    fi
    echo "    ports:"
    echo "      - \"8000:8000\""
    echo "    volumes:"
    echo "      - ./data:/app/data"
    if [[ "$ENABLE_SNAKEOIL_TLS" =~ ^[Yy]$ ]]; then
        echo "      - ./.docker-certs:/app/certs:ro"
    fi
    if [ "$TRANSPORT_MODE" = "serial" ]; then
        echo "    devices:"
        echo "      - ${SERIAL_COMPOSE_HOST_PATH}:${SERIAL_CONTAINER_PATH}"
    fi
    if [[ "$ENABLE_SNAKEOIL_TLS" =~ ^[Yy]$ ]]; then
        echo "    command:"
        echo "      - uv"
        echo "      - run"
        echo "      - uvicorn"
        echo "      - app.main:app"
        echo "      - --host"
        echo "      - 0.0.0.0"
        echo "      - --port"
        echo "      - \"8000\""
        echo "      - --ssl-keyfile"
        echo "      - $SNAKEOIL_KEY_CONTAINER_PATH"
        echo "      - --ssl-certfile"
        echo "      - $SNAKEOIL_CERT_CONTAINER_PATH"
    fi
    echo "    environment:"
    echo "      MESHCORE_DATABASE_PATH: $(yaml_quote "data/meshcore.db")"
    echo "    restart: unless-stopped"
} >"$COMPOSE_FILE"

echo -e "${GREEN}Generated ${COMPOSE_FILE}.${NC}"
echo
echo -e "${BOLD}Docker commands${NC}"
if [ "$IMAGE_MODE" = "build" ]; then
    echo "  $(priv docker compose up -d --build)    # build the local image and start Meshloom in the background"
else
    echo "  $(priv docker compose up -d)            # start Meshloom in the background"
fi
echo "  $(priv docker compose logs -f)          # follow the container logs live"
echo
echo "  $(priv docker compose down)             # stop and remove the running container"
echo "  $(priv docker compose restart)          # restart the container without changing the image"
echo "  $(priv docker compose pull) && $(priv docker compose up -d)   # upgrade to the latest published image and restart"
echo
echo -e "${YELLOW}Note:${NC} serial passthrough generally needs ${BOLD}rootful Docker${NC}."
echo "If Docker is running rootless on this host, serial-device mappings may fail even with a valid compose file."
echo
echo -e "${GREEN}Your new docker file is ready at ${COMPOSE_FILE}.${NC}"
echo -e "${GREEN}Feel free to edit it by hand as desired, or:${NC}"
echo
echo -e "${PURPLE}┌───────────────────────────────────────────────┐${NC}"
echo -e "${PURPLE}│ Run ${GREEN}${BOLD}$(priv docker compose up -d)${NC}${PURPLE} to get started. │${NC}"
echo -e "${PURPLE}└───────────────────────────────────────────────┘${NC}"
if [[ "$ENABLE_SNAKEOIL_TLS" =~ ^[Yy]$ ]]; then
    echo
    echo -e "After the container starts, open ${CYAN}https://${LOCAL_ACCESS_IP}:8000${NC}. Note that this address may change if you use DHCP/have not configured a static IP for your host via your router."
    echo -e "${YELLOW}Expect an untrusted/self-signed certificate warning the first time you connect.${NC}"
else
    echo
    echo -e "After the container starts, open ${CYAN}http://${LOCAL_ACCESS_IP}:8000${NC}. Note that this address may change if you use DHCP/have not configured a static IP for your host via your router."
fi
echo "If the interface does not appear, follow the logs to view errors with:"
echo "  $(priv docker compose logs -f)"
