#!/usr/bin/env bash
# Meshloom installer — self-contained (English + French).
#
# One-liner (keeps a real TTY for prompts; do not use curl | bash):
#   /bin/bash -c "$(curl -fsSL https://get.meshloom.app)"
#
# Run from a checkout:
#   bash scripts/setup/install.sh
#
# Answers are normalised (CR/whitespace) because some terminals send "2\r".

set -euo pipefail

is_root() { [ "$(id -u)" -eq 0 ]; }

# Privileged commands: run directly when already root so sudo is not required
# (and is not assumed to exist) on root-only hosts.
as_root() {
    if is_root; then
        "$@"
    else
        command sudo "$@"
    fi
}

priv() {
    if is_root; then
        printf '%s' "$*"
    else
        printf 'sudo %s' "$*"
    fi
}

REPO="TwinRocket/meshloom"
GIT_URL="https://github.com/${REPO}.git"
PAGES_BASE="https://twinrocket.github.io/meshloom"
GHCR_IMAGE="ghcr.io/twinrocket/meshloom"
API_RELEASES="https://api.github.com/repos/${REPO}/releases/latest"

ML_LANG="en"
OS_FAMILY=""
PKG_MGR=""
DOCKER_KIND="" # none | linux-rootful | linux-rootless | desktop
INSTALL_MODE=""
TRANSPORT=""
SERIAL_PORT=""
TCP_HOST=""
TCP_PORT="5000"
BLE_ADDRESS=""
BLE_PIN=""
INSTALL_DIR=""
IN_CHECKOUT=""
STEP_TOTAL=3
INSTALL_STEP=3
UI_CLEAR=1
UI_COLOR=1
INSTALL_LOG=""
INSTALLED_VERSION=""
TARGET_VERSION=""
UPGRADE_KIND=""
LANG_SAVED=""

# ── i18n ──────────────────────────────────────────────────────────────────────

t() {
    local key="$1"
    shift || true
    case "${ML_LANG}:${key}" in
        en:title) echo "Meshloom installation" ;;
        fr:title) echo "Installation Meshloom" ;;
        en:tagline) echo "Web interface for MeshCore mesh radio networks" ;;
        fr:tagline) echo "Interface web pour réseaux radio maillés MeshCore" ;;
        en:step) echo "Step" ;;
        fr:step) echo "Étape" ;;
        en:choice) echo "Your choice" ;;
        fr:choice) echo "Votre choix" ;;
        en:recommended) echo "recommended" ;;
        fr:recommended) echo "recommandé" ;;
        en:hint_yes) echo "[Y/n]" ;;
        fr:hint_yes) echo "[O/n]" ;;
        en:hint_no) echo "[y/N]" ;;
        fr:hint_no) echo "[o/N]" ;;
        en:yes) echo "yes" ;;
        fr:yes) echo "oui" ;;
        en:no) echo "no" ;;
        fr:no) echo "non" ;;
        en:invalid) echo "That choice is not in the list." ;;
        fr:invalid) echo "Ce choix ne figure pas dans la liste." ;;
        en:required) echo "This value is required." ;;
        fr:required) echo "Cette valeur est obligatoire." ;;
        en:cancelled) echo "Cancelled." ;;
        fr:cancelled) echo "Annulé." ;;
        en:need_tty) echo "This installer asks questions, so it needs a terminal. Run: /bin/bash -c \"\$(curl -fsSL https://get.meshloom.app)\"" ;;
        fr:need_tty) echo "Cette installation pose des questions et nécessite un terminal. Lancez : /bin/bash -c \"\$(curl -fsSL https://get.meshloom.app)\"" ;;
        en:detected) echo "Detected" ;;
        fr:detected) echo "Détecté" ;;
        en:docker_ready) echo "Docker is available" ;;
        fr:docker_ready) echo "Docker est disponible" ;;
        en:docker_absent) echo "Docker is not installed yet" ;;
        fr:docker_absent) echo "Docker n'est pas encore installé" ;;
        en:step_method) echo "Installation method" ;;
        fr:step_method) echo "Mode d'installation" ;;
        en:q_method) echo "How should Meshloom run on this machine?" ;;
        fr:q_method) echo "Comment Meshloom doit-il fonctionner sur cette machine ?" ;;
        en:opt_service) echo "Install as a background service" ;;
        fr:opt_service) echo "Installer comme service en arrière-plan" ;;
        en:opt_service_desc) echo "Starts automatically with the machine. Works with USB, network and Bluetooth radios." ;;
        fr:opt_service_desc) echo "Démarre automatiquement avec la machine. Compatible avec les radios USB, réseau et Bluetooth." ;;
        en:opt_docker) echo "Run with Docker" ;;
        fr:opt_docker) echo "Lancer avec Docker" ;;
        en:opt_docker_desc_usb) echo "Runs in a container. This machine can use a USB radio or a radio on the network." ;;
        fr:opt_docker_desc_usb) echo "Fonctionne dans un conteneur. Cette machine peut utiliser une radio USB ou une radio sur le réseau." ;;
        en:opt_docker_desc_tcp) echo "Runs in a container. Here Docker cannot reach USB ports, so the radio must be on the network." ;;
        fr:opt_docker_desc_tcp) echo "Fonctionne dans un conteneur. Ici Docker n'accède pas aux ports USB : la radio doit être sur le réseau." ;;
        en:opt_browser) echo "Only open Meshloom in a browser" ;;
        fr:opt_browser) echo "Ouvrir seulement Meshloom dans un navigateur" ;;
        en:opt_browser_desc) echo "Installs nothing. Choose this if Meshloom already runs on another machine." ;;
        fr:opt_browser_desc) echo "N'installe rien. À choisir si Meshloom fonctionne déjà sur une autre machine." ;;
        en:note_linux_only) echo "The Meshloom server is built for Linux. On this system, use Docker with a radio on the network." ;;
        fr:note_linux_only) echo "Le serveur Meshloom est conçu pour Linux. Sur ce système, utilisez Docker avec une radio sur le réseau." ;;
        en:note_radio_elsewhere) echo "For a USB or Bluetooth radio, install Meshloom on a Linux machine (Raspberry Pi, NAS…) and plug the radio in there." ;;
        fr:note_radio_elsewhere) echo "Pour une radio USB ou Bluetooth, installez Meshloom sur une machine Linux (Raspberry Pi, NAS…) et branchez-y la radio." ;;
        en:step_radio) echo "Radio connection" ;;
        fr:step_radio) echo "Connexion radio" ;;
        en:q_radio) echo "Should Docker map a USB radio, or will you configure a network radio in the web interface?" ;;
        fr:q_radio) echo "Docker doit-il mapper une radio USB, ou configurerez-vous une radio réseau dans l'interface ?" ;;
        en:opt_serial_auto) echo "USB cable, detected automatically" ;;
        fr:opt_serial_auto) echo "Câble USB, détection automatique" ;;
        en:opt_serial_auto_desc) echo "The radio is plugged into this machine and is the only serial device." ;;
        fr:opt_serial_auto_desc) echo "La radio est branchée sur cette machine et c'est le seul port série." ;;
        en:opt_serial) echo "USB cable, chosen manually" ;;
        fr:opt_serial) echo "Câble USB, choix manuel" ;;
        en:opt_serial_desc) echo "Use this when several serial devices are plugged in." ;;
        fr:opt_serial_desc) echo "À utiliser si plusieurs ports série sont branchés." ;;
        en:opt_tcp) echo "On the network (TCP)" ;;
        fr:opt_tcp) echo "Sur le réseau (TCP)" ;;
        en:opt_tcp_desc) echo "No USB mapping. Choose TCP in the web interface after install." ;;
        fr:opt_tcp_desc) echo "Pas de mapping USB. Choisissez TCP dans l'interface après l'installation." ;;
        en:opt_ble) echo "Bluetooth (BLE)" ;;
        fr:opt_ble) echo "Bluetooth (BLE)" ;;
        en:opt_ble_desc) echo "Pairs with the radio over Bluetooth. Needs its address and PIN." ;;
        fr:opt_ble_desc) echo "Appairage avec la radio en Bluetooth. Nécessite son adresse et son code PIN." ;;
        en:prompt_serial) echo "Serial device path" ;;
        fr:prompt_serial) echo "Chemin du port série" ;;
        en:hint_serial) echo "For example /dev/ttyUSB0 or /dev/ttyACM0." ;;
        fr:hint_serial) echo "Par exemple /dev/ttyUSB0 ou /dev/ttyACM0." ;;
        en:prompt_tcp_host) echo "Radio address" ;;
        fr:prompt_tcp_host) echo "Adresse de la radio" ;;
        en:hint_tcp_host) echo "IP address or hostname, for example 192.168.1.42." ;;
        fr:hint_tcp_host) echo "Adresse IP ou nom d'hôte, par exemple 192.168.1.42." ;;
        en:prompt_tcp_port) echo "TCP port" ;;
        fr:prompt_tcp_port) echo "Port TCP" ;;
        en:prompt_ble_addr) echo "Bluetooth address" ;;
        fr:prompt_ble_addr) echo "Adresse Bluetooth" ;;
        en:hint_ble_addr) echo "Six pairs separated by colons, for example AA:BB:CC:DD:EE:FF." ;;
        fr:hint_ble_addr) echo "Six paires séparées par des deux-points, par exemple AA:BB:CC:DD:EE:FF." ;;
        en:prompt_ble_pin) echo "Bluetooth PIN" ;;
        fr:prompt_ble_pin) echo "Code PIN Bluetooth" ;;
        en:hint_ble_pin) echo "Shown on the radio screen." ;;
        fr:hint_ble_pin) echo "Affiché sur l'écran de la radio." ;;
        en:step_install) echo "Installation" ;;
        fr:step_install) echo "Installation" ;;
        en:recap) echo "Summary" ;;
        fr:recap) echo "Récapitulatif" ;;
        en:recap_mode) echo "Method" ;;
        fr:recap_mode) echo "Mode" ;;
        en:recap_radio) echo "Radio" ;;
        fr:recap_radio) echo "Radio" ;;
        en:recap_radio_ui) echo "Configured in the web interface" ;;
        fr:recap_radio_ui) echo "À configurer dans l'interface web" ;;
        en:q_confirm) echo "Start the installation?" ;;
        fr:q_confirm) echo "Lancer l'installation ?" ;;
        en:q_confirm_upgrade) echo "Upgrade from $1 to $2?" ;;
        fr:q_confirm_upgrade) echo "Mettre à jour de $1 vers $2 ?" ;;
        en:q_confirm_reinstall) echo "Reinstall version $1?" ;;
        fr:q_confirm_reinstall) echo "Réinstaller la version $1 ?" ;;
        en:q_confirm_upgrade_from) echo "Upgrade from $1?" ;;
        fr:q_confirm_upgrade_from) echo "Mettre à jour depuis $1 ?" ;;
        en:step_upgrade) echo "Upgrade" ;;
        fr:step_upgrade) echo "Mise à jour" ;;
        en:recap_version) echo "Version" ;;
        fr:recap_version) echo "Version" ;;
        en:upgrade_keeps_data) echo "Messages, contacts and radio settings are kept." ;;
        fr:upgrade_keeps_data) echo "Les messages, contacts et réglages radio sont conservés." ;;
        en:using_saved_lang) echo "Using the saved language. Override with MESHLOOM_LANG=en or MESHLOOM_LANG=fr." ;;
        fr:using_saved_lang) echo "Langue mémorisée. Pour changer : MESHLOOM_LANG=en ou MESHLOOM_LANG=fr." ;;
        en:done_upgrade) echo "Meshloom has been upgraded." ;;
        fr:done_upgrade) echo "Meshloom a été mis à jour." ;;
        en:sudo_note) echo "Some steps need administrator rights; your password may be requested." ;;
        fr:sudo_note) echo "Certaines étapes nécessitent les droits administrateur ; votre mot de passe peut être demandé." ;;
        en:using_repo) echo "Installing from the Meshloom package repository." ;;
        fr:using_repo) echo "Installation depuis le dépôt de paquets Meshloom." ;;
        en:using_asset) echo "Installing the package from the latest release." ;;
        fr:using_asset) echo "Installation du paquet depuis la dernière version publiée." ;;
        en:asset_bad) echo "The downloaded package is not usable; falling back to a source install." ;;
        fr:asset_bad) echo "Le paquet téléchargé est inutilisable ; retour à une installation depuis les sources." ;;
        en:using_clone) echo "No ready-made package for this system; installing from source." ;;
        fr:using_clone) echo "Aucun paquet prêt pour ce système ; installation depuis les sources." ;;
        en:prompt_dir) echo "Installation folder" ;;
        fr:prompt_dir) echo "Dossier d'installation" ;;
        en:missing) echo "Missing on this machine" ;;
        fr:missing) echo "Absent de cette machine" ;;
        en:offer_install) echo "Install it now?" ;;
        fr:offer_install) echo "L'installer maintenant ?" ;;
        en:how_install) echo "It can be installed with:" ;;
        fr:how_install) echo "Il peut être installé avec :" ;;
        en:wrote_config) echo "Configuration written to" ;;
        fr:wrote_config) echo "Configuration écrite dans" ;;
        en:q_start_now) echo "Start Meshloom now?" ;;
        fr:q_start_now) echo "Démarrer Meshloom maintenant ?" ;;
        en:docker_usb_needs_root) echo "Sharing a USB radio needs Docker running as root on Linux. Choose the network option, or install Meshloom as a service." ;;
        fr:docker_usb_needs_root) echo "Le partage d'une radio USB nécessite Docker en mode root sur Linux. Choisissez l'option réseau, ou installez Meshloom comme service." ;;
        en:working) echo "This can take a few minutes." ;;
        fr:working) echo "Cela peut prendre quelques minutes." ;;
        en:failed) echo "The installation stopped on an error. Last lines of the log:" ;;
        fr:failed) echo "L'installation s'est arrêtée sur une erreur. Dernières lignes du journal :" ;;
        en:log_at) echo "Full log" ;;
        fr:log_at) echo "Journal complet" ;;
        en:phase_ok) echo "Done" ;;
        fr:phase_ok) echo "Terminé" ;;
        en:done) echo "Meshloom is installed." ;;
        fr:done) echo "Meshloom est installé." ;;
        en:open_at) echo "Open in your browser" ;;
        fr:open_at) echo "Ouvrez dans votre navigateur" ;;
        en:open_lan) echo "From another device on the same network" ;;
        fr:open_lan) echo "Depuis un autre appareil du même réseau" ;;
        en:service_hint) echo "Check or restart it with" ;;
        fr:service_hint) echo "Vérifiez ou redémarrez-le avec" ;;
        en:update_apt) echo "To update later" ;;
        fr:update_apt) echo "Pour mettre à jour plus tard" ;;
        en:update_dnf) echo "To update later" ;;
        fr:update_dnf) echo "Pour mettre à jour plus tard" ;;
        en:update_docker) echo "To update later, in that folder" ;;
        fr:update_docker) echo "Pour mettre à jour plus tard, dans ce dossier" ;;
        en:update_git) echo "To update later: git pull, then restart the service" ;;
        fr:update_git) echo "Pour mettre à jour plus tard : git pull, puis redémarrez le service" ;;
        en:step_browser) echo "Open Meshloom" ;;
        fr:step_browser) echo "Ouvrir Meshloom" ;;
        en:browser_body) echo "Nothing was installed. Open the Meshloom already running on your network:" ;;
        fr:browser_body) echo "Rien n'a été installé. Ouvrez le Meshloom déjà en service sur votre réseau :" ;;
        en:browser_hint) echo "Its address is that machine's IP address followed by port 8000." ;;
        fr:browser_hint) echo "Son adresse est l'adresse IP de cette machine suivie du port 8000." ;;
        *) echo "$key" ;;
    esac
}

# ── UI ────────────────────────────────────────────────────────────────────────

ui_b() { if [ "$UI_COLOR" = 1 ]; then printf '\033[1m%s\033[0m' "$1"; else printf '%s' "$1"; fi; }
ui_dim() { if [ "$UI_COLOR" = 1 ]; then printf '\033[2m%s\033[0m\n' "$1"; else printf '%s\n' "$1"; fi; }
ui_ok() { if [ "$UI_COLOR" = 1 ]; then printf '\033[0;32m%s\033[0m\n' "$1"; else printf '%s\n' "$1"; fi; }
ui_warn() { if [ "$UI_COLOR" = 1 ]; then printf '\033[1;33m%s\033[0m\n' "$1"; else printf '%s\n' "$1"; fi; }
ui_err() { if [ "$UI_COLOR" = 1 ]; then printf '\033[0;31m%s\033[0m\n' "$1" >&2; else printf '%s\n' "$1" >&2; fi; }

ui_init() {
    if [ ! -t 0 ]; then
        printf '%s\n' "$(t need_tty)"
        exit 1
    fi
    [ -t 1 ] || { UI_CLEAR=0; UI_COLOR=0; }
    [ -n "${NO_COLOR:-}" ] && UI_COLOR=0
    case "${TERM:-}" in
        "" | dumb) UI_CLEAR=0; UI_COLOR=0 ;;
    esac
    trap 'printf "\n%s\n" "$(t cancelled)"; exit 130' INT TERM
}

ui_screen() {
    local label="$1" num="${2:-}"
    if [ "$UI_CLEAR" = 1 ]; then
        printf '\033[H\033[2J\033[3J'
        printf '\n  %s\n' "$(ui_b Meshloom)"
        ui_dim "  $(t tagline)"
        printf '\n'
        if [ -n "$num" ]; then
            ui_dim "  $(t step) ${num}/${STEP_TOTAL}  ·  ${label}"
        else
            ui_dim "  ${label}"
        fi
        ui_dim "  ────────────────────────────────────────────────────────"
        printf '\n'
    else
        printf '\n== '
        [ -n "$num" ] && printf '[%s/%s] ' "$num" "$STEP_TOTAL"
        printf '%s ==\n\n' "$label"
    fi
}

ui_wrap() {
    local indent="$1" width="$2" text="$3" pad line="" w
    pad="$(printf '%*s' "$indent" '')"
    # shellcheck disable=SC2086
    for w in $text; do
        if [ -z "$line" ]; then
            line="$w"
        elif [ $((${#line} + 1 + ${#w})) -le "$width" ]; then
            line="$line $w"
        else
            ui_dim "${pad}${line}"
            line="$w"
        fi
    done
    [ -n "$line" ] && ui_dim "${pad}${line}"
}

ui_option() {
    local n="$1" label="$2" desc="$3" badge="${4:-}"
    printf '  %2s) %s' "$n" "$(ui_b "$label")"
    if [ -n "$badge" ]; then
        if [ "$UI_COLOR" = 1 ]; then
            printf '  \033[0;32m· %s\033[0m' "$badge"
        else
            printf '  (%s)' "$badge"
        fi
    fi
    printf '\n'
    ui_wrap 6 70 "$desc"
    printf '\n'
}

ui_norm() {
    local s="${1//$'\r'/}"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

ui_ask() {
    local prompt="$1" default="${2:-}" hint="${3:-}" raw=""
    [ -n "$hint" ] && ui_wrap 5 70 "$hint" >&2
    if [ -n "$default" ]; then
        printf '  → %s (%s) : ' "$prompt" "$default" >&2
    else
        printf '  → %s : ' "$prompt" >&2
    fi
    IFS= read -r raw || {
        printf '\n%s\n' "$(t cancelled)" >&2
        exit 130
    }
    raw="$(ui_norm "$raw")"
    [ -n "$raw" ] || raw="$default"
    printf '%s' "$raw"
}

ui_ask_required() {
    local prompt="$1" hint="${2:-}" value=""
    while [ -z "$value" ]; do
        value="$(ui_ask "$prompt" "" "$hint")"
        if [ -z "$value" ]; then
            ui_err "$(t required)"
        fi
    done
    printf '%s' "$value"
}

ui_menu() {
    local max="$1" default="${2:-1}" ans
    while :; do
        ans="$(ui_ask "$(t choice) [1-${max}]" "$default")"
        case "$ans" in
            '' | *[!0-9]*) ;;
            *)
                if [ "$ans" -ge 1 ] && [ "$ans" -le "$max" ]; then
                    printf '%s' "$ans"
                    return 0
                fi
                ;;
        esac
        ui_err "$(t invalid)"
    done
}

ui_yesno() {
    local q="$1" def="$2" ans
    while :; do
        if [ "$def" = y ]; then
            ans="$(ui_ask "${q} $(t hint_yes)" "y")"
        else
            ans="$(ui_ask "${q} $(t hint_no)" "n")"
        fi
        case "$(printf '%s' "$ans" | tr 'A-Z' 'a-z')" in
            y | yes | o | oui) return 0 ;;
            n | no | non) return 1 ;;
            *) ui_err "$(t invalid)" ;;
        esac
    done
}

os_label() {
    case "$OS_FAMILY" in
        linux) echo "Linux" ;;
        darwin) echo "macOS" ;;
        windows) echo "Windows" ;;
        *) echo "$OS_FAMILY" ;;
    esac
}

phase() {
    printf '  · %s\n' "$1"
}

phase_ok() {
    ui_ok "  ✓ $(t phase_ok)"
}

ensure_log() {
    if [ -z "$INSTALL_LOG" ]; then
        INSTALL_LOG="$(mktemp /tmp/meshloom-install.XXXXXX)"
    fi
}

run_quiet() {
    ensure_log
    if ! "$@" >>"$INSTALL_LOG" 2>&1; then
        ui_err "$(t failed)"
        tail -n 20 "$INSTALL_LOG" >&2 || true
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        exit 1
    fi
}

# Like run_quiet but returns the exit status instead of aborting, for steps that
# have a working fallback (release asset download -> source install).
run_soft() {
    ensure_log
    "$@" >>"$INSTALL_LOG" 2>&1
}

log_note() {
    ensure_log
    printf '%s\n' "$*" >>"$INSTALL_LOG"
}

# ── detection ─────────────────────────────────────────────────────────────────

detect_os() {
    case "$(uname -s)" in
        Linux) OS_FAMILY="linux" ;;
        Darwin) OS_FAMILY="darwin" ;;
        MINGW* | MSYS* | CYGWIN*) OS_FAMILY="windows" ;;
        *) OS_FAMILY="other" ;;
    esac
    if command -v apt-get >/dev/null 2>&1; then
        PKG_MGR="apt"
    elif command -v dnf >/dev/null 2>&1; then
        PKG_MGR="dnf"
    else
        PKG_MGR="none"
    fi
}

docker_is_rootless() {
    docker info 2>/dev/null | grep -qi 'rootless'
}

detect_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        DOCKER_KIND="none"
        return
    fi
    if [ "$OS_FAMILY" = "darwin" ] || [ "$OS_FAMILY" = "windows" ]; then
        DOCKER_KIND="desktop"
        return
    fi
    if docker_is_rootless; then
        DOCKER_KIND="linux-rootless"
    else
        DOCKER_KIND="linux-rootful"
    fi
}

detect_checkout() {
    local here=""
    if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
        here="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd 2>/dev/null || true)"
    fi
    if [ -n "$here" ] && [ -f "$here/app/main.py" ] && [ -f "$here/scripts/setup/install_service.sh" ]; then
        IN_CHECKOUT="$here"
    fi
}

user_installer_conf() {
    printf '%s' "${XDG_CONFIG_HOME:-$HOME/.config}/meshloom/installer.conf"
}

system_installer_conf() {
    printf '%s' "/etc/meshloom/installer.conf"
}

normalize_version() {
    local v="$1"
    v="${v#v}"
    v="${v#V}"
    v="${v%%+*}"
    case "$v" in
        *-*) v="${v%%-*}" ;;
    esac
    printf '%s' "$v"
}

is_installer_lang() {
    case "$1" in
        en | fr) return 0 ;;
        *) return 1 ;;
    esac
}

conf_get() {
    local file="$1" key="$2" raw=""
    [ -f "$file" ] || return 1
    raw="$(sed -n "s/^${key}=//p" "$file" | head -n 1)"
    raw="$(ui_norm "$raw")"
    [ -n "$raw" ] || return 1
    printf '%s' "$raw"
}

write_installer_conf() {
    local dest="$1" lang="$2" version="${3:-}"
    {
        echo "lang=$lang"
        if [ -n "$version" ]; then
            echo "version=$version"
        fi
    } >"$dest"
}

save_user_installer_conf() {
    local dest prev_version=""
    dest="$(user_installer_conf)"
    mkdir -p "$(dirname "$dest")"
    prev_version="$(conf_get "$dest" version || true)"
    write_installer_conf "$dest" "$ML_LANG" "${TARGET_VERSION:-$prev_version}"
}

save_system_installer_conf() {
    local dest tmp prev_version=""
    dest="$(system_installer_conf)"
    tmp="$(mktemp /tmp/meshloom-installer.XXXXXX)"
    if [ -f "$dest" ]; then
        prev_version="$(conf_get "$dest" version || true)"
    fi
    write_installer_conf "$tmp" "$ML_LANG" "${TARGET_VERSION:-$prev_version}"
    as_root mkdir -p "$(dirname "$dest")"
    as_root cp "$tmp" "$dest"
    as_root chmod 644 "$dest"
    rm -f "$tmp"
}

load_saved_language() {
    local raw=""
    raw="$(printf '%s' "${MESHLOOM_LANG:-}" | tr 'A-Z' 'a-z')"
    raw="$(ui_norm "$raw")"
    if is_installer_lang "$raw"; then
        ML_LANG="$raw"
        LANG_SAVED="env"
        return 0
    fi
    raw="$(conf_get "$(system_installer_conf)" lang || true)"
    raw="$(printf '%s' "$raw" | tr 'A-Z' 'a-z')"
    if is_installer_lang "$raw"; then
        ML_LANG="$raw"
        LANG_SAVED="system"
        return 0
    fi
    raw="$(conf_get "$(user_installer_conf)" lang || true)"
    raw="$(printf '%s' "$raw" | tr 'A-Z' 'a-z')"
    if is_installer_lang "$raw"; then
        ML_LANG="$raw"
        LANG_SAVED="user"
        return 0
    fi
    return 1
}

read_project_version() {
    local dir="$1" v=""
    [ -n "$dir" ] && [ -d "$dir" ] || return 1
    if [ -f "${dir}/build_info.json" ]; then
        v="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${dir}/build_info.json" | head -n 1)"
    fi
    if [ -z "$v" ] && [ -f "${dir}/pyproject.toml" ]; then
        v="$(sed -n 's/^version = "\([^"]*\)".*/\1/p' "${dir}/pyproject.toml" | head -n 1)"
    fi
    v="$(normalize_version "$v")"
    [ -n "$v" ] || return 1
    printf '%s' "$v"
}

package_installed_version() {
    local v=""
    if command -v dpkg-query >/dev/null 2>&1; then
        v="$(dpkg-query -W -f='${Version}' meshloom 2>/dev/null || true)"
        if [ -n "$v" ]; then
            normalize_version "$v"
            return 0
        fi
    fi
    if command -v rpm >/dev/null 2>&1; then
        v="$(rpm -q --qf '%{VERSION}' meshloom 2>/dev/null || true)"
        case "$v" in
            "" | *not\ installed*) ;;
            *)
                normalize_version "$v"
                return 0
                ;;
        esac
    fi
    return 1
}

unit_workdir() {
    systemctl show -p WorkingDirectory --value meshloom 2>/dev/null || true
}

compose_image_version() {
    local file="$1" v=""
    [ -f "$file" ] || return 1
    v="$(sed -n 's/.*meshloom:\([^[:space:]"]*\).*/\1/p' "$file" | head -n 1)"
    v="$(normalize_version "$v")"
    if [ -z "$v" ] || [ "$v" = "latest" ]; then
        return 1
    fi
    printf '%s' "$v"
}

rewrite_compose_image_tag() {
    # Pin the Meshloom GHCR image so compose pull fetches that release.
    local file="$1" tag="$2"
    tag="${tag#v}"
    [ -n "$tag" ] || return 1
    [ -f "$file" ] || return 1
    awk -v tag="$tag" '
        $1 == "image:" { sub(/:[^[:space:]]+$/, ":" tag) }
        { print }
    ' "$file" >"${file}.new"
    mv "${file}.new" "$file"
}

detect_installed_version() {
    local v="" wd="" conf=""
    INSTALLED_VERSION=""
    if v="$(package_installed_version)"; then
        INSTALLED_VERSION="$v"
        return 0
    fi
    if v="$(read_project_version /opt/meshloom)"; then
        INSTALLED_VERSION="$v"
        return 0
    fi
    wd="$(unit_workdir)"
    if [ -n "$wd" ] && [ "$wd" != "/" ] && v="$(read_project_version "$wd")"; then
        INSTALLED_VERSION="$v"
        return 0
    fi
    for conf in "$(system_installer_conf)" "$(user_installer_conf)"; do
        v="$(normalize_version "$(conf_get "$conf" version || true)")"
        if [ -n "$v" ]; then
            INSTALLED_VERSION="$v"
            return 0
        fi
    done
    if v="$(compose_image_version "${IN_CHECKOUT:+${IN_CHECKOUT}/docker-compose.yml}")"; then
        INSTALLED_VERSION="$v"
        return 0
    fi
    if v="$(compose_image_version "${HOME}/meshloom/docker-compose.yml")"; then
        INSTALLED_VERSION="$v"
        return 0
    fi
    return 1
}

detect_target_version() {
    local v="" tag=""
    TARGET_VERSION=""
    if [ "$INSTALL_MODE" = "service" ] && [ -n "$IN_CHECKOUT" ]; then
        if [ "$PKG_MGR" != "apt" ] && [ "$PKG_MGR" != "dnf" ]; then
            if v="$(read_project_version "$IN_CHECKOUT")"; then
                TARGET_VERSION="$v"
                return 0
            fi
        elif [ "$PKG_MGR" = "apt" ] && ! http_ok "${PAGES_BASE}/apt/dists/stable/Release"; then
            if v="$(read_project_version "$IN_CHECKOUT")"; then
                TARGET_VERSION="$v"
                return 0
            fi
        elif [ "$PKG_MGR" = "dnf" ] && ! http_ok "${PAGES_BASE}/rpm/$(rpm_arch)/repodata/repomd.xml"; then
            if v="$(read_project_version "$IN_CHECKOUT")"; then
                TARGET_VERSION="$v"
                return 0
            fi
        fi
    fi
    tag="$(latest_release_tag || true)"
    v="$(normalize_version "$tag")"
    if [ -n "$v" ]; then
        TARGET_VERSION="$v"
        return 0
    fi
    if [ -n "$IN_CHECKOUT" ] && v="$(read_project_version "$IN_CHECKOUT")"; then
        TARGET_VERSION="$v"
        return 0
    fi
    return 1
}

resolve_upgrade_kind() {
    UPGRADE_KIND=""
    detect_installed_version || true
    detect_target_version || true
    if [ -z "$INSTALLED_VERSION" ]; then
        return 0
    fi
    if [ -n "$TARGET_VERSION" ] && [ "$INSTALLED_VERSION" != "$TARGET_VERSION" ]; then
        UPGRADE_KIND="upgrade"
    else
        UPGRADE_KIND="reinstall"
    fi
}

host_arch() {
    case "$(uname -m)" in
        x86_64 | amd64) echo "amd64" ;;
        aarch64 | arm64) echo "arm64" ;;
        *) echo "unknown" ;;
    esac
}

rpm_arch() {
    case "$(host_arch)" in
        amd64) echo "x86_64" ;;
        arm64) echo "aarch64" ;;
        *) echo "unknown" ;;
    esac
}

http_ok() {
    curl -fsSIL --max-time 8 "$1" >/dev/null 2>&1
}

latest_release_tag() {
    curl -fsSL --max-time 15 "$API_RELEASES" 2>/dev/null |
        sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' |
        head -n 1
}

release_asset_url() {
    # Anchored on end-of-URL so sidecar assets (.deb.asc, .rpm.sha256, …) can
    # never be picked up as the package itself.
    local suffix="$1" pattern
    pattern="$(printf '%s' "$suffix" | sed 's/[.[\*^$\\]/\\&/g')"
    curl -fsSL --max-time 15 "$API_RELEASES" 2>/dev/null |
        tr ',' '\n' |
        sed -n 's/.*"browser_download_url"[[:space:]]*:[[:space:]]*"\(https:[^"]*\)".*/\1/p' |
        grep -E "${pattern}\$" |
        head -n 1
}

file_size() {
    wc -c <"$1" 2>/dev/null | tr -d '[:space:]'
}

# Leading bytes as lowercase hex, so package magic can be checked without
# assuming `file` is installed.
file_magic_hex() {
    od -An -v -tx1 -N "$2" "$1" 2>/dev/null | tr -d '[:space:]'
}

# A downloaded file is only handed to apt/dnf if it really is a package.
# An empty tempfile, a truncated transfer or an HTML error page all fail here.
pkg_file_is_valid() {
    local file="$1" kind="$2" size
    size="$(file_size "$file")"
    [ -n "$size" ] || return 1
    # Smallest real Meshloom package is orders of magnitude above this.
    [ "$size" -ge 4096 ] || return 1
    case "$kind" in
        # "!<arch>\n" — ar archive header used by every .deb.
        deb) [ "$(file_magic_hex "$file" 8)" = "213c617263683e0a" ] ;;
        rpm) [ "$(file_magic_hex "$file" 4)" = "edabeedb" ] ;;
        *) return 1 ;;
    esac
}

lan_ip() {
    local ip=""
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [ -z "$ip" ] && command -v ip >/dev/null 2>&1; then
        ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }}')"
    fi
    case "$ip" in
        "" | 127.* | ::1) printf '' ;;
        *) printf '%s' "$ip" ;;
    esac
}

# ── prompts ───────────────────────────────────────────────────────────────────

ensure_cmd() {
    local cmd="$1" packages="$2" yn
    if command -v "$cmd" >/dev/null 2>&1; then
        return 0
    fi
    ui_warn "$(t missing): ${cmd}"
    if [ "$PKG_MGR" = "apt" ]; then
        printf '  %s %s\n' "$(t how_install)" "$(priv apt-get install -y $packages)"
        yn="$(ui_ask "$(t offer_install) $(t hint_no)" "n")"
        case "$(printf '%s' "$yn" | tr 'A-Z' 'a-z')" in
            y | yes | o | oui)
                as_root apt-get update
                # shellcheck disable=SC2086
                as_root apt-get install -y $packages
                ;;
        esac
    elif [ "$PKG_MGR" = "dnf" ]; then
        printf '  %s %s\n' "$(t how_install)" "$(priv dnf install -y $packages)"
        yn="$(ui_ask "$(t offer_install) $(t hint_no)" "n")"
        case "$(printf '%s' "$yn" | tr 'A-Z' 'a-z')" in
            y | yes | o | oui)
                # shellcheck disable=SC2086
                as_root dnf install -y $packages
                ;;
        esac
    else
        printf '  %s your package manager, then re-run this installer.\n' "$(t how_install)"
    fi
    if ! command -v "$cmd" >/dev/null 2>&1; then
        ui_err "$(t missing): ${cmd}"
        exit 1
    fi
}

ensure_uv() {
    local yn
    if command -v uv >/dev/null 2>&1; then
        return 0
    fi
    ui_warn "$(t missing): uv"
    printf '  %s curl -LsSf https://astral.sh/uv/install.sh | sh\n' "$(t how_install)"
    yn="$(ui_ask "$(t offer_install) $(t hint_no)" "n")"
    case "$(printf '%s' "$yn" | tr 'A-Z' 'a-z')" in
        y | yes | o | oui)
            curl -LsSf https://astral.sh/uv/install.sh | sh
            # shellcheck disable=SC1090
            [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
            export PATH="$HOME/.local/bin:$PATH"
            ;;
    esac
    if ! command -v uv >/dev/null 2>&1; then
        ui_err "$(t missing): uv"
        exit 1
    fi
}

ensure_docker() {
    ensure_cmd docker "docker.io docker-compose-v2" || true
    if ! command -v docker >/dev/null 2>&1; then
        if [ "$PKG_MGR" = "dnf" ]; then
            ensure_cmd docker "docker docker-compose"
        else
            echo "See https://docs.docker.com/engine/install/"
            exit 1
        fi
    fi
    if docker compose version >/dev/null 2>&1; then
        return 0
    fi
    if command -v docker-compose >/dev/null 2>&1; then
        return 0
    fi
    ui_err "$(t missing): docker compose"
    exit 1
}

compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        echo "docker compose"
    else
        echo "docker-compose"
    fi
}

choose_language() {
    local default=1 ans
    if load_saved_language; then
        save_user_installer_conf
        return 0
    fi
    case "$(printf '%s%s%s' "${LC_ALL:-}" "${LC_MESSAGES:-}" "${LANG:-}" | tr 'A-Z' 'a-z')" in
        *fr*) default=2 ;;
    esac
    ui_screen "Language / Langue" ""
    ui_option 1 "English" "Continue in English."
    ui_option 2 "Français" "Continuer en français."
    while :; do
        ans="$(ui_ask "Language / Langue [1-2]" "$default")"
        case "$(printf '%s' "$ans" | tr 'A-Z' 'a-z')" in
            2 | fr | fra | français | francais | french)
                ML_LANG="fr"
                save_user_installer_conf
                return 0
                ;;
            1 | en | eng | english | anglais)
                ML_LANG="en"
                save_user_installer_conf
                return 0
                ;;
            *) ui_err "Invalid choice. / Choix invalide." ;;
        esac
    done
}

choose_install_mode() {
    local i=1 choice docker_desc
    SERVICE_IDX=""
    DOCKER_IDX=""
    BROWSER_IDX=""
    ui_screen "$(t step_method)" 1
    if [ -n "$LANG_SAVED" ]; then
        ui_dim "  $(t using_saved_lang)"
        printf '\n'
    fi
    printf '  %s : %s' "$(t detected)" "$(os_label)"
    if [ "$DOCKER_KIND" != "none" ]; then
        printf ' · %s' "$(t docker_ready)"
    else
        printf ' · %s' "$(t docker_absent)"
    fi
    printf '\n\n'
    printf '  %s\n\n' "$(t q_method)"
    if [ "$OS_FAMILY" != "linux" ]; then
        ui_warn "  $(t note_linux_only)"
        ui_warn "  $(t note_radio_elsewhere)"
        printf '\n'
    fi
    if [ "$OS_FAMILY" = "linux" ]; then
        ui_option "$i" "$(t opt_service)" "$(t opt_service_desc)" "$(t recommended)"
        SERVICE_IDX="$i"
        i=$((i + 1))
    fi
    if [ "$DOCKER_KIND" != "none" ] || [ "$OS_FAMILY" = "linux" ] || [ "$OS_FAMILY" = "darwin" ]; then
        if [ "$DOCKER_KIND" = "linux-rootful" ] || { [ "$DOCKER_KIND" = "none" ] && [ "$OS_FAMILY" = "linux" ]; }; then
            docker_desc="$(t opt_docker_desc_usb)"
        else
            docker_desc="$(t opt_docker_desc_tcp)"
        fi
        ui_option "$i" "$(t opt_docker)" "$docker_desc"
        DOCKER_IDX="$i"
        i=$((i + 1))
    fi
    ui_option "$i" "$(t opt_browser)" "$(t opt_browser_desc)"
    BROWSER_IDX="$i"
    choice="$(ui_menu "$i" 1)"
    if [ -n "$SERVICE_IDX" ] && [ "$choice" = "$SERVICE_IDX" ]; then
        INSTALL_MODE="service"
    elif [ -n "$DOCKER_IDX" ] && [ "$choice" = "$DOCKER_IDX" ]; then
        INSTALL_MODE="docker"
    else
        INSTALL_MODE="browser"
    fi
}

docker_allows_usb() {
    [ "$DOCKER_KIND" = "linux-rootful" ]
}

choose_transport() {
    # Docker only: USB vs network, solely to emit compose devices: mapping.
    # systemd installs configure transport in the web UI (app_settings).
    local allow_usb="n" n=1 choice
    local idx_auto="" idx_serial="" idx_tcp=""
    if docker_allows_usb; then
        allow_usb="y"
    fi

    ui_screen "$(t step_radio)" 2
    printf '  %s\n\n' "$(t q_radio)"
    if [ "$allow_usb" = "y" ]; then
        ui_option "$n" "$(t opt_serial_auto)" "$(t opt_serial_auto_desc)"
        idx_auto="$n"
        n=$((n + 1))
        ui_option "$n" "$(t opt_serial)" "$(t opt_serial_desc)"
        idx_serial="$n"
        n=$((n + 1))
    fi
    ui_option "$n" "$(t opt_tcp)" "$(t opt_tcp_desc)"
    idx_tcp="$n"
    choice="$(ui_menu $((n - 1)) 1)"
    if [ -n "$idx_auto" ] && [ "$choice" = "$idx_auto" ]; then
        TRANSPORT="serial-auto"
    elif [ -n "$idx_serial" ] && [ "$choice" = "$idx_serial" ]; then
        TRANSPORT="serial"
        SERIAL_PORT="$(ui_ask_required "$(t prompt_serial)" "$(t hint_serial)")"
    elif [ "$choice" = "$idx_tcp" ]; then
        TRANSPORT="tcp"
    fi
}

recap_mode_label() {
    case "$INSTALL_MODE" in
        service) t opt_service ;;
        docker) t opt_docker ;;
        *) t opt_browser ;;
    esac
}

recap_radio_label() {
    case "$TRANSPORT" in
        serial-auto) t opt_serial_auto ;;
        serial) t opt_serial ;;
        tcp) t opt_tcp ;;
        ui) t recap_radio_ui ;;
        *) echo "$TRANSPORT" ;;
    esac
}

confirm_question() {
    case "$UPGRADE_KIND" in
        upgrade) t q_confirm_upgrade "$INSTALLED_VERSION" "$TARGET_VERSION" ;;
        reinstall)
            if [ -n "$TARGET_VERSION" ]; then
                t q_confirm_reinstall "$TARGET_VERSION"
            else
                t q_confirm_upgrade_from "$INSTALLED_VERSION"
            fi
            ;;
        *) t q_confirm ;;
    esac
}

confirm_install() {
    local step_label
    resolve_upgrade_kind
    if [ -n "$UPGRADE_KIND" ]; then
        step_label="$(t step_upgrade)"
    else
        step_label="$(t step_install)"
    fi
    ui_screen "$step_label" "$INSTALL_STEP"
    printf '  %s\n' "$(ui_b "$(t recap)")"
    printf '    %s    %s\n' "$(t recap_mode)" "$(recap_mode_label)"
    printf '    %s    %s\n' "$(t recap_radio)" "$(recap_radio_label)"
    if [ -n "$INSTALLED_VERSION" ] && [ -n "$TARGET_VERSION" ]; then
        printf '    %s    %s → %s\n' "$(t recap_version)" "$INSTALLED_VERSION" "$TARGET_VERSION"
    elif [ -n "$INSTALLED_VERSION" ]; then
        printf '    %s    %s\n' "$(t recap_version)" "$INSTALLED_VERSION"
    elif [ -n "$TARGET_VERSION" ]; then
        printf '    %s    %s\n' "$(t recap_version)" "$TARGET_VERSION"
    fi
    printf '\n'
    if [ -n "$UPGRADE_KIND" ]; then
        ui_wrap 2 70 "$(t upgrade_keeps_data)"
        printf '\n'
    fi
    if ! is_root; then
        ui_wrap 2 70 "$(t sudo_note)"
        printf '\n'
    fi
    if ! ui_yesno "$(confirm_question)" y; then
        printf '\n%s\n' "$(t cancelled)"
        exit 130
    fi
    printf '\n'
    ui_dim "  $(t working)"
    if ! is_root; then
        command sudo -v
    fi
}

persist_installer_state() {
    save_user_installer_conf
    if is_root || command -v sudo >/dev/null 2>&1; then
        save_system_installer_conf || true
    fi
}

# Idempotent: re-running the installer (upgrade or reinstall) installs a missing
# apply helper. Package units ship in the .deb; Compose units are written here
# because the curl one-liner has no sibling files on disk.
_package_update_helper_present() {
    [ -x /usr/lib/meshloom/apply-update ] && [ -f /usr/lib/systemd/system/meshloom-update.service ]
}

_env_ensure_key() {
    local dest="$1" key="$2" value="$3"
    if as_root grep -qE "^${key}=" "$dest"; then
        return 0
    fi
    printf '%s=%s\n' "$key" "$value" | as_root tee -a "$dest" >/dev/null
}

_restore_package_update_helper() {
    if [ "${PKG_MGR:-}" = "apt" ]; then
        as_root apt-get update || true
        as_root apt-get install -y --only-upgrade meshloom || true
        if ! _package_update_helper_present; then
            as_root apt-get install --reinstall -y meshloom || true
        fi
    elif [ "${PKG_MGR:-}" = "dnf" ]; then
        as_root dnf install -y meshloom || true
        if ! _package_update_helper_present; then
            as_root dnf reinstall -y meshloom || true
        fi
    fi
}

_install_package_update_helper_fallback() {
    as_root mkdir -p /usr/lib/meshloom /usr/lib/systemd/system /usr/share/polkit-1/rules.d
    as_root tee /usr/lib/meshloom/apply-update >/dev/null <<'EOF'
#!/bin/sh
# Fallback helper written by install.sh when the packaged files are missing.
set -e
JOB_PATH="${MESHLOOM_UPDATE_JOB_PATH:-/var/lib/meshloom/update-job.json}"
STARTED_AT=$(date +%s)
LAST_ATTEMPT=
TARGET=
load_identity() {
    [ -f "$JOB_PATH" ] || return 0
    existing_target=$(sed -n 's/.*"target":"\([^"]*\)".*/\1/p' "$JOB_PATH" | head -n 1)
    existing_last=$(sed -n 's/.*"last_attempt":\([0-9][0-9]*\).*/\1/p' "$JOB_PATH" | head -n 1)
    existing_started=$(sed -n 's/.*"started_at":\([0-9][0-9]*\).*/\1/p' "$JOB_PATH" | head -n 1)
    [ -n "$existing_target" ] && TARGET=$existing_target
    [ -n "$existing_last" ] && LAST_ATTEMPT=$existing_last
    [ -n "$existing_started" ] && STARTED_AT=$existing_started
}
write_job() {
    last_json=${LAST_ATTEMPT:-$STARTED_AT}
    target_json=""
    [ -n "$TARGET" ] && target_json=",\"target\":\"${TARGET}\""
    mkdir -p "$(dirname "$JOB_PATH")"
    printf '%s\n' "{\"state\":\"$1\",\"phase\":\"$2\",\"percent\":null,\"error\":$3,\"started_at\":${STARTED_AT},\"last_attempt\":${last_json}${target_json}}" >"$JOB_PATH"
}
load_identity
write_job applying preparing null
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update || { write_job failed preparing "\"apt-get update failed\""; exit 1; }
    write_job applying downloading null
    if command -v dpkg-query >/dev/null 2>&1 && dpkg-query -W -f='${Status}\n' meshloom 2>/dev/null | grep -q 'ok installed'; then
        apt-get install -y --only-upgrade meshloom || { write_job failed downloading "\"apt-get install --only-upgrade meshloom failed\""; exit 1; }
    else
        apt-get install -y meshloom || { write_job failed downloading "\"apt-get install meshloom failed\""; exit 1; }
    fi
elif command -v dnf >/dev/null 2>&1; then
    write_job applying installing null
    dnf install -y meshloom || { write_job failed installing "\"dnf install meshloom failed\""; exit 1; }
else
    write_job failed preparing "\"no supported package manager\""
    exit 1
fi
write_job succeeded done null
EOF
    as_root chmod 0755 /usr/lib/meshloom/apply-update
    as_root tee /usr/lib/systemd/system/meshloom-update.service >/dev/null <<'EOF'
[Unit]
Description=Meshloom package update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
ExecStart=/usr/lib/meshloom/apply-update
TimeoutStartSec=30min
EOF
    as_root tee /usr/share/polkit-1/rules.d/60-meshloom-update.rules >/dev/null <<'EOF'
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" &&
        action.lookup("unit") == "meshloom-update.service" &&
        action.lookup("verb") == "start" &&
        subject.user == "meshloom") {
        return polkit.Result.YES;
    }
});
EOF
}

ensure_update_helper() {
    local kind="$1" compose_dir="${2:-}"
    case "$kind" in
        package)
            if _package_update_helper_present; then
                as_root systemctl daemon-reload || true
                return 0
            fi
            _restore_package_update_helper
            if _package_update_helper_present; then
                as_root systemctl daemon-reload || true
                return 0
            fi
            _install_package_update_helper_fallback
            as_root systemctl daemon-reload || true
            ;;
        compose)
            [ -n "$compose_dir" ] || return 0
            _install_compose_update_helper "$compose_dir"
            ;;
    esac
}

_install_compose_update_helper() {
    local compose_dir="$1"
    local unit_dir="/etc/systemd/system"
    local env_file="/etc/meshloom/compose-update.env"
    local data_dir="${compose_dir}/data"
    as_root mkdir -p /etc/meshloom /usr/lib/meshloom "$data_dir"
    {
        printf 'MESHLOOM_COMPOSE_DIR=%s\n' "$compose_dir"
        printf 'MESHLOOM_GHCR_IMAGE=%s\n' "$GHCR_IMAGE"
        printf 'MESHLOOM_RELEASES_API=%s\n' "$API_RELEASES"
    } | as_root tee "$env_file" >/dev/null
    as_root tee /usr/lib/meshloom/compose-update >/dev/null <<'EOF'
#!/bin/sh
set -eu
# Host-side helper for installer-managed Docker. Never bind-mount docker.sock
# into the Meshloom container. Rewrite the pinned image tag, then pull/up.
. /etc/meshloom/compose-update.env
JOB="${MESHLOOM_COMPOSE_DIR}/data/update-job.json"
REQ="${MESHLOOM_COMPOSE_DIR}/data/request-update"
COMPOSE="${MESHLOOM_COMPOSE_DIR}/docker-compose.yml"
mkdir -p "$(dirname "$JOB")"
# PathExists only retriggers on absent → present. Drop a leftover request first.
rm -f "$REQ"

json_field() {
    [ -f "$JOB" ] || return 0
    sed -n "s/.*\"$1\":\"\\([^\"]*\\)\".*/\\1/p" "$JOB" | head -n 1
}
json_int() {
    [ -f "$JOB" ] || return 0
    sed -n "s/.*\"$1\":\\([0-9][0-9]*\\).*/\\1/p" "$JOB" | head -n 1
}

now="$(date +%s)"
started="$(json_int started_at || true)"
[ -n "${started:-}" ] || started="$now"
last="$(json_int last_attempt || true)"
[ -n "${last:-}" ] || last="$now"
target="$(json_field target || true)"
if [ -z "${target:-}" ]; then
    target="$(curl -fsSL --max-time 15 "${MESHLOOM_RELEASES_API}" 2>/dev/null |
        sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
fi
target="${target#v}"

write_job() {
    state="$1"
    phase="$2"
    percent="$3"
    error="$4"
    target_json=""
    if [ -n "$target" ]; then
        target_json=",\"target\":\"${target}\""
    fi
    printf '%s\n' "{\"state\":\"${state}\",\"phase\":\"${phase}\",\"percent\":${percent},\"error\":${error},\"started_at\":${started},\"last_attempt\":${last}${target_json}}" >"$JOB"
}

write_job applying downloading null null
if [ -z "$target" ]; then
    write_job failed downloading null "\"could not resolve image tag\""
    exit 1
fi
if [ -f "$COMPOSE" ]; then
    awk -v tag="$target" '
        $1 == "image:" { sub(/:[^[:space:]]+$/, ":" tag) }
        { print }
    ' "$COMPOSE" >"${COMPOSE}.new"
    mv "${COMPOSE}.new" "$COMPOSE"
fi
cd "$MESHLOOM_COMPOSE_DIR"
if docker compose pull; then
    write_job applying installing null null
    if docker compose up -d; then
        write_job succeeded done 100 null
        exit 0
    fi
fi
write_job failed downloading null "\"docker compose pull/up failed\""
exit 1
EOF
    as_root chmod 0755 /usr/lib/meshloom/compose-update
    as_root tee "${unit_dir}/meshloom-compose-update.service" >/dev/null <<EOF
[Unit]
Description=Meshloom Docker Compose apply
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
EnvironmentFile=/etc/meshloom/compose-update.env
ExecStart=/usr/lib/meshloom/compose-update
EOF
    as_root tee "${unit_dir}/meshloom-compose-update.path" >/dev/null <<EOF
[Unit]
Description=Watch Meshloom compose update request

[Path]
PathExists=${data_dir}/request-update
PathChanged=${data_dir}/request-update

[Install]
WantedBy=multi-user.target
EOF
    as_root systemctl daemon-reload || true
    as_root systemctl enable --now meshloom-compose-update.path || true
}

write_meshloom_env() {
    local dest="$1"
    as_root mkdir -p "$(dirname "$dest")"
    if [ ! -f "$dest" ]; then
        {
            echo "# Generated by Meshloom install.sh"
            echo "# Radio transport is configured in the web UI (app_settings), not here."
            echo "MESHCORE_DATABASE_PATH=/var/lib/meshloom/meshcore.db"
            echo "MESHLOOM_INSTALL_KIND=package"
        } | as_root tee "$dest" >/dev/null
        as_root chmod 640 "$dest"
        return
    fi
    # Do not clobber a packaged env. Only fill missing keys the one-liner owns.
    _env_ensure_key "$dest" MESHCORE_DATABASE_PATH /var/lib/meshloom/meshcore.db
    _env_ensure_key "$dest" MESHLOOM_INSTALL_KIND package
    as_root chmod 640 "$dest" || true
}

start_meshloom_unit() {
    as_root systemctl daemon-reload
    as_root systemctl enable meshloom
    as_root systemctl restart meshloom
}

install_from_pages() {
    phase "$(t using_repo)"
    run_quiet as_root mkdir -p /etc/apt/keyrings /etc/yum.repos.d
    if [ "$PKG_MGR" = "apt" ]; then
        if http_ok "${PAGES_BASE}/meshloom.gpg"; then
            curl -fsSL "${PAGES_BASE}/meshloom.gpg" | as_root tee /etc/apt/keyrings/meshloom.gpg >/dev/null
            echo "deb [signed-by=/etc/apt/keyrings/meshloom.gpg] ${PAGES_BASE}/apt stable main" |
                as_root tee /etc/apt/sources.list.d/meshloom.list >/dev/null
        else
            echo "deb [trusted=yes] ${PAGES_BASE}/apt stable main" |
                as_root tee /etc/apt/sources.list.d/meshloom.list >/dev/null
        fi
        run_quiet as_root apt-get update
        run_quiet as_root apt-get install -y meshloom
    else
        if http_ok "${PAGES_BASE}/meshloom.asc"; then
            as_root rpm --import "${PAGES_BASE}/meshloom.asc" >/dev/null 2>&1 || true
        fi
        as_root tee /etc/yum.repos.d/meshloom.repo >/dev/null <<EOF
[meshloom]
name=Meshloom
baseurl=${PAGES_BASE}/rpm/\$basearch
enabled=1
gpgcheck=$(http_ok "${PAGES_BASE}/meshloom.asc" && echo 1 || echo 0)
gpgkey=${PAGES_BASE}/meshloom.asc
EOF
        run_quiet as_root dnf install -y meshloom
    fi
    as_root mkdir -p /etc/meshloom
    write_meshloom_env /etc/meshloom/meshloom.env
    start_meshloom_unit
    persist_installer_state
    ensure_update_helper package
    phase_ok
}

install_from_release_asset() {
    local arch suffix url tmp pkg_ext pkg_kind
    arch="$(host_arch)"
    [ "$arch" != "unknown" ] || return 1
    if [ "$PKG_MGR" = "apt" ]; then
        suffix="_${arch}.deb"
        pkg_ext=".deb"
        pkg_kind="deb"
    else
        suffix=".$(rpm_arch).rpm"
        pkg_ext=".rpm"
        pkg_kind="rpm"
    fi
    url="$(release_asset_url "$suffix")"
    [ -n "$url" ] || return 1
    phase "$(t using_asset)"
    # apt only accepts local files whose name ends in .deb / .ddeb / .changes.
    tmp="$(mktemp "/tmp/meshloom.XXXXXX${pkg_ext}")"
    # The download and the validation below must stay between the mktemp and the
    # apt/dnf call: handing over a freshly created (empty) tempfile is what
    # produced "could not locate member control.tar" in 4.1.1.
    if ! run_soft curl -fL --max-time 180 "$url" -o "$tmp" ||
        ! pkg_file_is_valid "$tmp" "$pkg_kind"; then
        log_note "release asset unusable: url=${url} bytes=$(file_size "$tmp") magic=$(file_magic_hex "$tmp" 8)"
        ui_warn "  $(t asset_bad)"
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG"
        rm -f "$tmp"
        return 1
    fi
    if [ "$PKG_MGR" = "apt" ]; then
        run_quiet as_root apt-get install -y "$tmp"
    else
        run_quiet as_root dnf install -y "$tmp"
    fi
    rm -f "$tmp"
    as_root mkdir -p /etc/meshloom
    write_meshloom_env /etc/meshloom/meshloom.env
    start_meshloom_unit
    persist_installer_state
    ensure_update_helper package
    phase_ok
}

ensure_clone() {
    if [ -n "$IN_CHECKOUT" ]; then
        INSTALL_DIR="$IN_CHECKOUT"
        return 0
    fi
    local default="${HOME}/meshloom" tag
    INSTALL_DIR="$(ui_ask "$(t prompt_dir)" "$default")"
    INSTALL_DIR="${INSTALL_DIR:-$default}"
    if [ -f "${INSTALL_DIR}/app/main.py" ]; then
        return 0
    fi
    ensure_cmd git "git"
    ensure_cmd curl "curl"
    tag="$(latest_release_tag || true)"
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if [ -n "$tag" ]; then
        run_quiet git clone --quiet --depth 1 --branch "$tag" "$GIT_URL" "$INSTALL_DIR"
    else
        run_quiet git clone --quiet --depth 1 "$GIT_URL" "$INSTALL_DIR"
    fi
}

run_service_from_source() {
    phase "$(t using_clone)"
    ensure_clone
    ensure_cmd python3 "python3"
    ensure_uv
    export MESHLOOM_NONINTERACTIVE=1
    if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
        export MESHLOOM_FRONTEND_MODE="build"
    else
        export MESHLOOM_FRONTEND_MODE="prebuilt"
    fi
    run_quiet bash "${INSTALL_DIR}/scripts/setup/install_service.sh"
    persist_installer_state
    phase_ok
}

print_done_native() {
    local ip
    ip="$(lan_ip)"
    printf '\n'
    if [ "$UPGRADE_KIND" = "upgrade" ]; then
        ui_ok "  $(t done_upgrade)"
    else
        ui_ok "  $(t done)"
    fi
    printf '  %s\n' "$(t open_at)"
    printf '    %s\n' "http://127.0.0.1:8000"
    if [ -n "$ip" ]; then
        printf '  %s\n' "$(t open_lan)"
        printf '    %s\n' "http://${ip}:8000"
    fi
    ui_dim "  $(t service_hint): $(priv systemctl status meshloom)"
}

install_native_service() {
    ensure_cmd curl "curl"
    confirm_install
    if [ "$PKG_MGR" = "apt" ] && http_ok "${PAGES_BASE}/apt/dists/stable/Release"; then
        install_from_pages
        print_done_native
        ui_dim "  $(t update_apt): $(priv apt upgrade)"
        return
    fi
    if [ "$PKG_MGR" = "dnf" ] && http_ok "${PAGES_BASE}/rpm/$(rpm_arch)/repodata/repomd.xml"; then
        install_from_pages
        print_done_native
        ui_dim "  $(t update_dnf): $(priv dnf upgrade)"
        return
    fi
    if [ "$PKG_MGR" = "apt" ] || [ "$PKG_MGR" = "dnf" ]; then
        if install_from_release_asset; then
            print_done_native
            if [ "$PKG_MGR" = "apt" ]; then ui_dim "  $(t update_apt): $(priv apt upgrade)"; else ui_dim "  $(t update_dnf): $(priv dnf upgrade)"; fi
            return
        fi
    fi
    run_service_from_source
    printf '\n'
    if [ "$UPGRADE_KIND" = "upgrade" ]; then
        ui_ok "  $(t done_upgrade)"
    else
        ui_ok "  $(t done)"
    fi
    ui_dim "  $(t update_git)"
}

yaml_quote() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '"%s"' "$value"
}

write_docker_compose() {
    local dir="$1"
    local tag
    tag="$(latest_release_tag || true)"
    mkdir -p "${dir}/data"
    {
        echo "# Generated by Meshloom install.sh"
        echo "services:"
        echo "  meshloom:"
        echo "    image: ${GHCR_IMAGE}:latest"
        echo "    ports:"
        echo "      - \"8000:8000\""
        echo "    volumes:"
        echo "      - ./data:/app/data"
        if [ "$TRANSPORT" = "serial" ] || [ "$TRANSPORT" = "serial-auto" ]; then
            local host_dev="${SERIAL_PORT:-/dev/ttyACM0}"
            if [ "$TRANSPORT" = "serial-auto" ]; then
                if [ -e /dev/ttyACM0 ]; then
                    host_dev="/dev/ttyACM0"
                elif [ -e /dev/ttyUSB0 ]; then
                    host_dev="/dev/ttyUSB0"
                fi
            fi
            echo "    devices:"
            echo "      - ${host_dev}:/dev/meshcore-radio"
        fi
        echo "    environment:"
        echo "      MESHCORE_DATABASE_PATH: $(yaml_quote "data/meshcore.db")"
        echo "      MESHLOOM_INSTALL_KIND: compose"
        echo "      MESHLOOM_UPDATE_HELPER: compose"
        echo "      MESHLOOM_UPDATE_JOB_PATH: /app/data/update-job.json"
        echo "    restart: unless-stopped"
    } >"${dir}/docker-compose.yml"
    if [ -n "$tag" ]; then
        rewrite_compose_image_tag "${dir}/docker-compose.yml" "$tag"
    fi
}

install_docker_stack() {
    ensure_docker
    detect_docker
    while [ "$TRANSPORT" != "tcp" ] && ! docker_allows_usb; do
        ui_err "$(t docker_usb_needs_root)"
        choose_transport
    done
    local default="${IN_CHECKOUT:-${HOME}/meshloom}" dc
    INSTALL_DIR="$(ui_ask "$(t prompt_dir)" "$default")"
    INSTALL_DIR="${INSTALL_DIR:-$default}"
    confirm_install
    mkdir -p "$INSTALL_DIR"
    write_docker_compose "$INSTALL_DIR"
    ui_dim "  $(t wrote_config) ${INSTALL_DIR}/docker-compose.yml"
    dc="$(compose_cmd)"
    if ui_yesno "$(t q_start_now)" y; then
        phase "$(t working)"
        (
            cd "$INSTALL_DIR"
            run_quiet as_root $dc pull
            run_quiet as_root $dc up -d
        )
        phase_ok
    fi
    persist_installer_state
    ensure_update_helper compose "$INSTALL_DIR"
    printf '\n'
    if [ "$UPGRADE_KIND" = "upgrade" ]; then
        ui_ok "  $(t done_upgrade)"
    else
        ui_ok "  $(t done)"
    fi
    printf '  %s\n    %s\n' "$(t open_at)" "http://127.0.0.1:8000"
    ui_dim "  $(t update_docker): $(priv "$dc pull") && $(priv "$dc up -d")"
}

show_browser_only() {
    STEP_TOTAL=2
    ui_screen "$(t step_browser)" 2
    ui_wrap 2 70 "$(t browser_body)"
    printf '\n'
    ui_dim "  $(t browser_hint)"
    printf '\n  http://<ip>:8000\n'
}

# ── main ──────────────────────────────────────────────────────────────────────

ui_init
choose_language
detect_os
detect_docker
detect_checkout
choose_install_mode

if [ "$INSTALL_MODE" = "browser" ]; then
    show_browser_only
    exit 0
fi

if [ "$INSTALL_MODE" = "docker" ]; then
    STEP_TOTAL=3
    INSTALL_STEP=3
    choose_transport
else
    STEP_TOTAL=2
    INSTALL_STEP=2
    TRANSPORT="ui"
fi

if [ "$INSTALL_MODE" = "service" ]; then
    install_native_service
else
    install_docker_stack
fi
