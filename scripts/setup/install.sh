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
SERIAL_COMPOSE_HOST_PATH=""
DBUS_SOCKET=""
SERIAL_FOUND_HOST_PATHS=()
SERIAL_FOUND_LABELS=()
SERIAL_FOUND_DISPLAYS=()
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
        en:q_radio) echo "Several serial devices were found. Which one should Docker map?" ;;
        fr:q_radio) echo "Plusieurs ports série ont été trouvés. Lequel Docker doit-il mapper ?" ;;
        en:opt_serial_auto) echo "USB cable, detected automatically" ;;
        fr:opt_serial_auto) echo "Câble USB, détection automatique" ;;
        en:opt_serial_auto_desc) echo "The radio is plugged into this machine and is the only serial device." ;;
        fr:opt_serial_auto_desc) echo "La radio est branchée sur cette machine et c'est le seul port série." ;;
        en:opt_serial) echo "USB cable, chosen manually" ;;
        fr:opt_serial) echo "Câble USB, choix manuel" ;;
        en:opt_serial_desc) echo "Use this when several serial devices are plugged in." ;;
        fr:opt_serial_desc) echo "À utiliser si plusieurs ports série sont branchés." ;;
        en:opt_no_map) echo "No USB mapping" ;;
        fr:opt_no_map) echo "Pas de mapping USB" ;;
        en:opt_no_map_desc) echo "The container will not receive a serial device. Configure the radio in the web interface after install." ;;
        fr:opt_no_map_desc) echo "Le conteneur n'aura pas de port série. Configurez la radio dans l'interface après l'installation." ;;
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
        en:recap_usb) echo "USB $1" ;;
        fr:recap_usb) echo "USB $1" ;;
        en:recap_dbus) echo "Bluetooth" ;;
        fr:recap_dbus) echo "Bluetooth" ;;
        en:recap_dbus_mapped) echo "Host D-Bus socket mapped" ;;
        fr:recap_dbus_mapped) echo "Socket D-Bus hôte mappé" ;;
        en:serial_path_unusable) echo "That serial path cannot be written into Docker Compose (it contains ':'). No USB device will be mapped." ;;
        fr:serial_path_unusable) echo "Ce port série ne peut pas être écrit dans Docker Compose (il contient ':'). Aucun périphérique USB ne sera mappé." ;;
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
        en:asset_bad) echo "The release package address is not the expected one. Nothing was installed." ;;
        fr:asset_bad) echo "L'adresse du paquet publié n'est pas celle attendue. Rien n'a été installé." ;;
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
        en:docker_usb_needs_root) echo "Sharing a USB radio needs Docker running as root on Linux. The compose file will not map a serial device." ;;
        fr:docker_usb_needs_root) echo "Le partage d'une radio USB nécessite Docker en mode root sur Linux. Le fichier Compose ne mappera pas de port série." ;;
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
        en:key_missing) echo "This installer does not carry the Meshloom release key, so it cannot check what it installs. Download the installer from https://get.meshloom.app again." ;;
        fr:key_missing) echo "Cet installeur ne contient pas la clé de publication Meshloom : il ne peut pas vérifier ce qu'il installe. Téléchargez de nouveau l'installeur depuis https://get.meshloom.app." ;;
        en:key_bad) echo "The release key embedded in this installer does not match its pinned fingerprint. Installation stopped." ;;
        fr:key_bad) echo "La clé de publication embarquée ne correspond pas à son empreinte. Installation arrêtée." ;;
        en:sig_failed) echo "The release could not be verified against the Meshloom signing key. Nothing was installed." ;;
        fr:sig_failed) echo "La version publiée n'a pas pu être vérifiée avec la clé de signature Meshloom. Rien n'a été installé." ;;
        en:compose_pin_failed) echo "Could not pin a signed Meshloom image. Nothing was changed in the running stack." ;;
        fr:compose_pin_failed) echo "Impossible d'épingler une image Meshloom signée. La pile en service n'a pas été modifiée." ;;
        en:compose_secure) echo "Installing the secure update helper" ;;
        fr:compose_secure) echo "Installation de l'assistant de mise à jour sécurisé" ;;
        en:compose_backup) echo "The previous docker-compose.yml was kept as docker-compose.yml.bak-<date>." ;;
        fr:compose_backup) echo "L'ancien docker-compose.yml a été conservé en docker-compose.yml.bak-<date>." ;;
        en:compose_dir_unsafe) echo "In-app updates need a folder path made of letters, digits, '.', '_', '-' and '/'. Update this stack by hand." ;;
        fr:compose_dir_unsafe) echo "Les mises à jour depuis l'application exigent un chemin de dossier fait de lettres, chiffres, '.', '_', '-' et '/'. Mettez cette pile à jour à la main." ;;
        en:compose_found) echo "Existing Meshloom stack found in" ;;
        fr:compose_found) echo "Pile Meshloom existante trouvée dans" ;;
        en:compose_custom) echo "This docker-compose.yml was edited after the installer wrote it (- expected, + yours):" ;;
        fr:compose_custom) echo "Ce docker-compose.yml a été modifié après l'installeur (- attendu, + le vôtre) :" ;;
        en:compose_custom_stop) echo "Nothing was changed. Either apply by hand: image: \${MESHLOOM_IMAGE}, the ./update-status:/app/update-status:ro volume and MESHLOOM_UPDATE_STATUS_PATH; or re-run with MESHLOOM_COMPOSE_OVERWRITE=1 to regenerate it (a .bak copy is kept)." ;;
        fr:compose_custom_stop) echo "Rien n'a été modifié. Soit vous appliquez à la main : image: \${MESHLOOM_IMAGE}, le volume ./update-status:/app/update-status:ro et MESHLOOM_UPDATE_STATUS_PATH ; soit vous relancez avec MESHLOOM_COMPOSE_OVERWRITE=1 pour le régénérer (une copie .bak est gardée)." ;;
        en:update_docker_managed) echo "Updates: Settings → Updates in Meshloom installs signed releases." ;;
        fr:update_docker_managed) echo "Mises à jour : Réglages → Mises à jour dans Meshloom installe les versions signées." ;;
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
    local dest="$1" lang="$2" version="${3:-}" compose_dir="${4:-}"
    {
        echo "lang=$lang"
        if [ -n "$version" ]; then
            echo "version=$version"
        fi
        if [ -n "$compose_dir" ]; then
            echo "compose_dir=$compose_dir"
        fi
    } >"$dest"
}

save_user_installer_conf() {
    local dest prev_version="" prev_dir=""
    dest="$(user_installer_conf)"
    mkdir -p "$(dirname "$dest")"
    prev_version="$(conf_get "$dest" version || true)"
    prev_dir="$(conf_get "$dest" compose_dir || true)"
    write_installer_conf "$dest" "$ML_LANG" "${TARGET_VERSION:-$prev_version}" "${COMPOSE_DIR_SAVED:-$prev_dir}"
}

# /etc/meshloom holds files root reads. Up to 4.17 the package let the
# meshloom user own it, so before root writes there: make it root's again and
# quarantine links and entries root does not own (never follow or reuse them).
secure_etc_meshloom() {
    local entry q=""
    if [ -L /etc/meshloom ]; then
        as_root rm -f /etc/meshloom
    fi
    as_root mkdir -p /etc/meshloom
    as_root chown root /etc/meshloom
    as_root chmod go-w /etc/meshloom
    for entry in $(as_root find /etc/meshloom -mindepth 1 -maxdepth 1 \
        \( -type l -o ! -user root -o \( -type f -links +1 \) \) -print 2>/dev/null); do
        if [ -z "$q" ]; then
            as_root mkdir -p -m 0700 /var/lib/meshloom-quarantine
            q="$(as_root mktemp -d /var/lib/meshloom-quarantine/etc.XXXXXX)"
        fi
        as_root mv -f "$entry" "$q/"
        ui_warn "  Moved untrusted ${entry} to ${q}"
    done
}

# Root write into /etc/meshloom: replace the entry, never write through it.
install_etc_file() {
    local src="$1" dest="$2" mode="$3"
    as_root rm -f "$dest"
    as_root install -m "$mode" "$src" "$dest"
}

save_system_installer_conf() {
    local dest tmp prev_version="" prev_dir=""
    dest="$(system_installer_conf)"
    tmp="$(mktemp /tmp/meshloom-installer.XXXXXX)"
    if [ -f "$dest" ]; then
        prev_version="$(conf_get "$dest" version || true)"
        prev_dir="$(conf_get "$dest" compose_dir || true)"
    fi
    write_installer_conf "$tmp" "$ML_LANG" "${TARGET_VERSION:-$prev_version}" "${COMPOSE_DIR_SAVED:-$prev_dir}"
    secure_etc_meshloom
    install_etc_file "$tmp" "$dest" 0644
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
    if [ -z "$v" ] || [ "${v#\$}" != "$v" ]; then
        # 4.18+: the image is pinned in .env next to the compose file.
        v="$(sed -n 's/^MESHLOOM_IMAGE=.*meshloom:\([^[:space:]"]*\).*/\1/p' "$(dirname "$file")/.env" 2>/dev/null | head -n 1)"
    fi
    v="${v%%@*}"
    v="$(normalize_version "$v")"
    if [ -z "$v" ] || [ "$v" = "latest" ]; then
        return 1
    fi
    printf '%s' "$v"
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
    wd="$(conf_get "$(system_installer_conf)" compose_dir || true)"
    if [ -n "$wd" ] && v="$(compose_image_version "${wd}/docker-compose.yml")"; then
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
        # 32-bit Raspberry Pi OS on a Pi 2, 3 or 4. Not armv6l: a Pi 1 and the
        # original Zero are ARMv6, and Raspberry Pi OS calls that armhf too, but
        # the package carries an interpreter built for ARMv7. Installing it there
        # would succeed and then die on an illegal instruction, which is a worse
        # answer than sending it to the source install.
        armv7l | armhf) echo "armhf" ;;
        *) echo "unknown" ;;
    esac
}

# Whether the published apt repository carries packages for this machine.
#
# The Release file lists what it holds. Reading it rather than hard-coding the
# list means the day armhf packages are published, this opens on its own.
pages_apt_has_host_arch() {
    local arch line
    arch="$(host_arch)"
    [ "$arch" != "unknown" ] || return 1
    line="$(curl -fsSL --max-time 8 "${PAGES_BASE}/apt/dists/stable/Release" 2>/dev/null |
        grep -i '^Architectures:' || true)"
    [ -n "$line" ] || return 1
    case " ${line#*:} " in
        *" $arch "*) return 0 ;;
        *) return 1 ;;
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

detect_dbus_socket() {
    # Only the socket, never a directory: a missing path must not be created.
    local candidate
    local candidates=("$@")
    if [ "${#candidates[@]}" -eq 0 ]; then
        candidates=(/run/dbus/system_bus_socket /var/run/dbus/system_bus_socket)
    fi
    DBUS_SOCKET=""
    for candidate in "${candidates[@]}"; do
        if [ -S "$candidate" ]; then
            DBUS_SOCKET="$candidate"
            return 0
        fi
    done
    return 1
}

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

set_serial_mapping() {
    local selected="$1"
    local resolved=""

    SERIAL_PORT="$selected"
    TRANSPORT="serial"
    if [[ "$selected" != *:* ]]; then
        SERIAL_COMPOSE_HOST_PATH="$selected"
        return 0
    fi

    resolved="$(readlink -f "$selected" 2>/dev/null || true)"
    if [ -n "$resolved" ] && [[ "$resolved" != *:* ]]; then
        SERIAL_COMPOSE_HOST_PATH="$resolved"
        return 0
    fi

    TRANSPORT="ui"
    SERIAL_PORT=""
    SERIAL_COMPOSE_HOST_PATH=""
    return 1
}

choose_serial_among_found() {
    local n=1 choice i idx_none=""
    ui_screen "$(t step_radio)" 2
    printf '  %s\n\n' "$(t q_radio)"
    for i in "${!SERIAL_FOUND_HOST_PATHS[@]}"; do
        ui_option "$n" "${SERIAL_FOUND_LABELS[$i]}" "${SERIAL_FOUND_DISPLAYS[$i]}"
        n=$((n + 1))
    done
    ui_option "$n" "$(t opt_no_map)" "$(t opt_no_map_desc)"
    idx_none="$n"
    choice="$(ui_menu "$n" 1)"
    if [ "$choice" = "$idx_none" ]; then
        TRANSPORT="ui"
        SERIAL_PORT=""
        SERIAL_COMPOSE_HOST_PATH=""
        return 0
    fi
    if ! set_serial_mapping "${SERIAL_FOUND_HOST_PATHS[$((choice - 1))]}"; then
        ui_warn "$(t serial_path_unusable)"
    fi
}

prepare_docker_mappings() {
    # Rootful Linux Docker only: USB devices: and the host D-Bus socket.
    # 0 serial devices → no USB map. 1 → map it. 2+ → ask.
    # TCP and radio settings stay in the web UI.
    TRANSPORT="ui"
    SERIAL_PORT=""
    SERIAL_COMPOSE_HOST_PATH=""
    DBUS_SOCKET=""
    STEP_TOTAL=2
    INSTALL_STEP=2

    if ! docker_allows_usb; then
        return 0
    fi

    detect_dbus_socket || true
    find_serial_devices
    local count="${#SERIAL_FOUND_HOST_PATHS[@]}"
    if [ "$count" -eq 0 ]; then
        return 0
    fi
    if [ "$count" -eq 1 ]; then
        if ! set_serial_mapping "${SERIAL_FOUND_HOST_PATHS[0]}"; then
            ui_warn "$(t serial_path_unusable)"
        fi
        return 0
    fi
    STEP_TOTAL=3
    INSTALL_STEP=3
    choose_serial_among_found
}

recap_mode_label() {
    case "$INSTALL_MODE" in
        service) t opt_service ;;
        docker) t opt_docker ;;
        *) t opt_browser ;;
    esac
}

recap_radio_label() {
    if [ -n "$SERIAL_COMPOSE_HOST_PATH" ]; then
        t recap_usb "$SERIAL_COMPOSE_HOST_PATH"
        return 0
    fi
    t recap_radio_ui
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
    if [ "$INSTALL_MODE" = "docker" ] && [ -n "$DBUS_SOCKET" ]; then
        printf '    %s    %s\n' "$(t recap_dbus)" "$(t recap_dbus_mapped)"
    fi
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

# ── release key and signed sources ────────────────────────────────────────────
#
# The release public key is embedded below and pinned by fingerprint. It is
# never downloaded: a key fetched from the same place as the packages would
# prove nothing. There is no unsigned fallback anywhere in this installer.

KEYRING_PATH=/usr/share/keyrings/meshloom-archive-keyring.gpg
KEYRING_ASC_PATH=/usr/share/keyrings/meshloom-archive-keyring.asc
RELEASE_KEY_DIR=""
VERSION_RE='^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'

# >>> embed: pkg/keys (generated by scripts/setup/sync_installer_embeds.py; do not edit)
MESHLOOM_KEY_FINGERPRINT=''
MESHLOOM_KEYRING_SHA256=''
MESHLOOM_KEY_ASC=''
# <<< embed: pkg/keys

# >>> embed: pkg/nfpm/meshloom.pref (generated by scripts/setup/sync_installer_embeds.py; do not edit)
_embed_apt_pin() {
    cat <<'MESHLOOM_EMBED_EOF'
# Installed by the meshloom package. Only the official Meshloom repository
# (twinrocket.github.io, signature-checked through signed-by=) may provide
# the meshloom package, and that repository may not replace anything else.
# Pin by origin host, not by Release "Origin:", which any source can claim.
Package: meshloom
Pin: origin "twinrocket.github.io"
Pin-Priority: 990

Package: meshloom
Pin: release *
Pin-Priority: -1

Package: *
Pin: origin "twinrocket.github.io"
Pin-Priority: -1
MESHLOOM_EMBED_EOF
}
# <<< embed: pkg/nfpm/meshloom.pref

release_key_embedded() {
    printf '%s\n' "$MESHLOOM_KEY_FINGERPRINT" | grep -Eq '^[0-9A-F]{40}$' &&
        [ -n "$MESHLOOM_KEY_ASC" ] && [ -n "$MESHLOOM_KEYRING_SHA256" ]
}

# ASCII armor -> binary packets, with sed/base64 only (gpg may be absent).
dearmor_key() {
    sed -n '/^-----BEGIN PGP PUBLIC KEY BLOCK-----$/,/^-----END PGP PUBLIC KEY BLOCK-----$/p' |
        sed '1d;$d' |
        awk 'body && $0 !~ /^=/ { print } /^$/ { body = 1 }' |
        base64 -d
}

sha256_of() {
    sha256sum "$1" | awk '{print $1}'
}

# Decode the embedded key into a private temp dir and check it twice: the
# SHA-256 of the keyring, and (when gpg exists) the primary fingerprint.
prepare_release_key() {
    local dir fpr
    [ -z "$RELEASE_KEY_DIR" ] || return 0
    if ! release_key_embedded; then
        ui_err "$(t key_missing)"
        exit 1
    fi
    dir="$(mktemp -d /tmp/meshloom-key.XXXXXX)"
    printf '%s' "$MESHLOOM_KEY_ASC" >"$dir/meshloom.asc"
    if ! dearmor_key <"$dir/meshloom.asc" >"$dir/meshloom.gpg" 2>/dev/null ||
        [ "$(sha256_of "$dir/meshloom.gpg")" != "$MESHLOOM_KEYRING_SHA256" ]; then
        rm -rf "$dir"
        ui_err "$(t key_bad)"
        exit 1
    fi
    if command -v gpg >/dev/null 2>&1; then
        fpr="$(GNUPGHOME="$dir" gpg --batch --with-colons --show-keys "$dir/meshloom.gpg" 2>/dev/null |
            awk -F: '/^fpr:/ {print $10; exit}')"
        if [ "$fpr" != "$MESHLOOM_KEY_FINGERPRINT" ]; then
            rm -rf "$dir"
            ui_err "$(t key_bad)"
            exit 1
        fi
    fi
    RELEASE_KEY_DIR="$dir"
}

install_release_key() {
    prepare_release_key
    as_root install -D -m 0644 "$RELEASE_KEY_DIR/meshloom.gpg" "$KEYRING_PATH"
    as_root install -D -m 0644 "$RELEASE_KEY_DIR/meshloom.asc" "$KEYRING_ASC_PATH"
}

ensure_gpgv() {
    if [ "$PKG_MGR" = "dnf" ]; then
        ensure_cmd gpgv "gnupg2"
    else
        ensure_cmd gpgv "gpgv"
    fi
}

# Signed apt/dnf source for the Meshloom repository, plus the apt pin. Always
# written, even after a release-asset install, so later updates stay signed.
add_signed_repo() {
    local tmp
    install_release_key
    if [ "$PKG_MGR" = "apt" ]; then
        tmp="$(mktemp /tmp/meshloom-pref.XXXXXX)"
        _embed_apt_pin >"$tmp"
        as_root install -D -m 0644 "$tmp" /etc/apt/preferences.d/meshloom.pref
        rm -f "$tmp"
        as_root mkdir -p /etc/apt/sources.list.d
        echo "deb [signed-by=${KEYRING_PATH}] ${PAGES_BASE}/apt stable main" |
            as_root tee /etc/apt/sources.list.d/meshloom.list >/dev/null
        as_root chmod 0644 /etc/apt/sources.list.d/meshloom.list
    else
        as_root mkdir -p /etc/yum.repos.d
        as_root tee /etc/yum.repos.d/meshloom.repo >/dev/null <<EOF
[meshloom]
name=Meshloom
baseurl=${PAGES_BASE}/rpm/\$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=file://${KEYRING_ASC_PATH}
EOF
        as_root chmod 0644 /etc/yum.repos.d/meshloom.repo
    fi
}

# Idempotent: re-running the installer (upgrade or reinstall) restores a
# missing packaged helper from the signed repository. The package is the only
# source of the package-mode helper; the installer never writes its own copy.
_package_update_helper_present() {
    [ -x /usr/lib/meshloom/apply-update ] \
        && [ -f /usr/lib/systemd/system/meshloom-update.service ] \
        && [ -f /usr/lib/systemd/system/meshloom-update.path ]
}

_env_ensure_key() {
    local dest="$1" key="$2" value="$3"
    if as_root grep -qE "^${key}=" "$dest"; then
        return 0
    fi
    printf '%s=%s\n' "$key" "$value" | as_root tee -a "$dest" >/dev/null
}

_env_set_key() {
    local dest="$1" key="$2" value="$3"
    as_root sed -i "/^${key}=/d" "$dest"
    printf '%s=%s\n' "$key" "$value" | as_root tee -a "$dest" >/dev/null
}

_env_drop_key() {
    as_root sed -i "/^${2}=/d" "$1"
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

ensure_update_helper() {
    _package_update_helper_present || _restore_package_update_helper
    if [ -d /run/systemd/system ]; then
        as_root systemctl daemon-reload || true
        if _package_update_helper_present; then
            as_root systemctl enable --now meshloom-update.path || true
        fi
    fi
}

# ── compose update helper ─────────────────────────────────────────────────────

# >>> embed: scripts/setup/helpers/compose-update (generated by scripts/setup/sync_installer_embeds.py; do not edit)
_embed_compose_update() {
    cat <<'MESHLOOM_EMBED_EOF'
#!/bin/sh
# Host-side helper for installer-managed Docker Compose (meshloom-compose-update).
#
# Security model (see app/AGENTS.md, "Updates"):
#   - The container only *triggers* this unit (PathChanged= on
#     <dir>/data/request-update). Nothing under data/ is ever read here.
#   - The target is the latest GitHub release, resolved from the
#     releases/latest redirect. Its image digest comes from OCI-DIGESTS, signed
#     with the Meshloom release key (gpgv). Downgrades are refused.
#   - The image is pinned by digest in <dir>/.env (MESHLOOM_IMAGE), rewritten
#     with mktemp + mv. docker-compose.yml is never edited.
#   - Progress goes to <dir>/update-status/status.json (root 0755 directory,
#     mounted read-only in the container).
#
# Usage: compose-update             apply the latest release (systemd unit)
#        compose-update --bootstrap  write .env for a fresh install, no pull/up
set -eu

R=
if [ "${MESHLOOM_HELPER_TESTING:-}" = 1 ]; then
    R="${MESHLOOM_HELPER_ROOT:?MESHLOOM_HELPER_ROOT is required in testing mode}"
fi

REPO_URL="https://github.com/TwinRocket/meshloom"
IMAGE_REPO="ghcr.io/twinrocket/meshloom"
KEYRING="$R/usr/share/keyrings/meshloom-archive-keyring.gpg"
STATE_DIR="$R/var/lib/meshloom-compose-update"
STAMP_PATH="$STATE_DIR/last-start"
RUN_DIR="${RUNTIME_DIRECTORY:-$R/run/meshloom-compose-update}"
COOLDOWN_SECONDS=120
VERSION_RE='^[0-9]{1,6}\.[0-9]{1,6}\.[0-9]{1,6}$'
DIGEST_RE='^sha256:[0-9a-f]{64}$'
DIR_RE='^/[A-Za-z0-9._/-]+$'

MODE=apply
if [ "${1:-}" = "--bootstrap" ]; then
    MODE=bootstrap
fi

STATE=applying
PHASE=preparing
PERCENT=
ERROR=
STARTED_AT=$(date +%s)
VERSION=
FINISHED=0
STATUS_READY=0
WORK=

log() {
    echo "meshloom compose-update: $*" >&2
}

json_escape() {
    printf '%s' "$1" | tr -d '\000-\037' | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'
}

is_root_owned_dir() {
    [ -d "$1" ] && [ ! -L "$1" ] || return 1
    [ -n "$R" ] || [ "$(stat -c %u "$1")" = 0 ]
}

write_status() {
    [ "$STATUS_READY" -eq 1 ] || return 0
    if [ -n "$PERCENT" ]; then percent_json=$PERCENT; else percent_json=null; fi
    if [ -n "$ERROR" ]; then error_json="\"$(json_escape "$ERROR")\""; else error_json=null; fi
    if [ -n "$VERSION" ]; then version_json="\"$VERSION\""; else version_json=null; fi
    now=$(date +%s)
    tmp=$(mktemp "$STATUS_DIR/.status.XXXXXX")
    printf '{"schema":1,"state":"%s","phase":"%s","percent":%s,"error":%s,"started_at":%s,"updated_at":%s,"version":%s}\n' \
        "$STATE" "$PHASE" "$percent_json" "$error_json" "$STARTED_AT" "$now" "$version_json" >"$tmp"
    chmod 0644 "$tmp"
    mv -f "$tmp" "$STATUS_DIR/status.json"
}

fail() {
    FINISHED=1
    STATE=failed
    ERROR=$1
    write_status
    log "failed: $ERROR"
    exit 1
}

on_exit() {
    rc=$?
    if [ -n "$WORK" ]; then rm -rf "$WORK"; fi
    if [ "$FINISHED" -eq 0 ]; then
        FINISHED=1
        STATE=failed
        [ -n "$ERROR" ] || ERROR="update failed (exit $rc)"
        write_status || true
    fi
}

# version_gt A B: true when A > B (both already match VERSION_RE).
version_gt() {
    a1=${1%%.*}; rest=${1#*.}; a2=${rest%%.*}; a3=${rest#*.}
    b1=${2%%.*}; rest=${2#*.}; b2=${rest%%.*}; b3=${rest#*.}
    [ "$a1" -ne "$b1" ] && { [ "$a1" -gt "$b1" ]; return; }
    [ "$a2" -ne "$b2" ] && { [ "$a2" -gt "$b2" ]; return; }
    [ "$a3" -gt "$b3" ]
}

# Latest release tag from the releases/latest redirect. The Location header
# must be exactly <repo>/releases/tag/X.Y.Z; anything else is refused.
resolve_latest() {
    location=$(curl -fsS --proto '=https' --max-time 20 -o /dev/null -w '%{redirect_url}' \
        "${REPO_URL}/releases/latest") || return 1
    tag=${location#"${REPO_URL}/releases/tag/"}
    [ "$tag" != "$location" ] || return 1
    case "$tag" in
        '' | *[!0-9.]*) return 1 ;;
    esac
    printf '%s\n' "$tag" | grep -Eq "$VERSION_RE" || return 1
    printf '%s' "$tag"
}

# Digest of the OCI index for $1, from the release's OCI-DIGESTS file signed
# with the Meshloom release key. The file holds exactly one line:
#   ghcr.io/twinrocket/meshloom:X.Y.Z sha256:<64 lowercase hex>
signed_digest() {
    tag=$1
    curl -fsSL --proto '=https' --max-time 60 -o "$WORK/OCI-DIGESTS" \
        "${REPO_URL}/releases/download/${tag}/OCI-DIGESTS" || return 2
    curl -fsSL --proto '=https' --max-time 60 -o "$WORK/OCI-DIGESTS.asc" \
        "${REPO_URL}/releases/download/${tag}/OCI-DIGESTS.asc" || return 2
    gpgv --keyring "$KEYRING" "$WORK/OCI-DIGESTS.asc" "$WORK/OCI-DIGESTS" >/dev/null 2>&1 || return 3
    [ "$(wc -l <"$WORK/OCI-DIGESTS" | tr -d ' ')" = 1 ] || return 4
    escaped=$(printf '%s' "$tag" | sed 's/\./\\./g')
    line=$(grep -E "^ghcr\.io/twinrocket/meshloom:${escaped} sha256:[0-9a-f]{64}\$" "$WORK/OCI-DIGESTS" || true)
    [ -n "$line" ] || return 4
    digest=${line##* }
    printf '%s\n' "$digest" | grep -Eq "$DIGEST_RE" || return 4
    printf '%s' "$digest"
}

# Version currently pinned in .env, or empty when there is no valid pin.
current_version() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n 's/^MESHLOOM_IMAGE=//p' "$ENV_FILE" | head -n 1 |
        sed -n "s|^ghcr\.io/twinrocket/meshloom:\([0-9]*\.[0-9]*\.[0-9]*\)@sha256:[0-9a-f]\{64\}\$|\1|p"
}

write_env() {
    image="${IMAGE_REPO}:$1@$2"
    tmp=$(mktemp "$COMPOSE_DIR/.env.XXXXXX")
    {
        echo "# Managed by meshloom-compose-update. MESHLOOM_IMAGE is pinned by digest."
        echo "MESHLOOM_IMAGE=${image}"
        if [ -f "$ENV_FILE" ]; then
            grep -v -e '^MESHLOOM_IMAGE=' -e '^# Managed by meshloom-compose-update' "$ENV_FILE" || true
        fi
    } >"$tmp"
    chmod 0644 "$tmp"
    mv -f "$tmp" "$ENV_FILE"
}

trap on_exit EXIT
trap 'on_exit; exit 130' INT
trap 'on_exit; exit 143' TERM

if [ -z "$R" ] && [ "$(id -u)" -ne 0 ]; then
    log "must run as root"
    exit 1
fi
umask 022

# Only the directory comes from /etc/meshloom/compose-update.env (root-owned,
# passed by systemd EnvironmentFile=). It is validated, never sourced.
COMPOSE_DIR="${MESHLOOM_COMPOSE_DIR:-}"
if ! printf '%s\n' "$COMPOSE_DIR" | grep -Eq "$DIR_RE" || [ "$(printf '%s\n' "$COMPOSE_DIR" | wc -l)" -ne 1 ]; then
    log "invalid MESHLOOM_COMPOSE_DIR"
    exit 1
fi
case "/$COMPOSE_DIR/" in
    */../* | */./*)
        log "invalid MESHLOOM_COMPOSE_DIR"
        exit 1
        ;;
esac
COMPOSE_DIR="$R$COMPOSE_DIR"
if [ ! -d "$COMPOSE_DIR" ] || [ -L "$COMPOSE_DIR" ]; then
    log "compose directory missing: $COMPOSE_DIR"
    exit 1
fi
ENV_FILE="$COMPOSE_DIR/.env"
STATUS_DIR="$COMPOSE_DIR/update-status"
if [ -L "$STATUS_DIR" ]; then
    log "refusing symlinked $STATUS_DIR"
    exit 1
fi
mkdir -p "$STATUS_DIR"
chmod 0755 "$STATUS_DIR"
if ! is_root_owned_dir "$STATUS_DIR"; then
    log "$STATUS_DIR must be a root-owned directory"
    exit 1
fi
STATUS_READY=1

for d in "$STATE_DIR" "$RUN_DIR"; do
    if [ -L "$d" ]; then fail "unsafe helper directory"; fi
    mkdir -p "$d"
    chmod 0700 "$d"
done
WORK=$(mktemp -d "$RUN_DIR/work.XXXXXX")

if [ "$MODE" = apply ]; then
    now=$(date +%s)
    last=$(sed -n '1{/^[0-9][0-9]*$/p;}' "$STAMP_PATH" 2>/dev/null || true)
    if [ -n "$last" ] && [ "$now" -ge "$last" ] && [ $((now - last)) -lt "$COOLDOWN_SECONDS" ]; then
        FINISHED=1
        STATE=cooldown
        ERROR="update requested too soon; try again in $((COOLDOWN_SECONDS - (now - last))) s"
        VERSION=$(current_version)
        write_status
        log "$ERROR"
        exit 0
    fi
    printf '%s\n' "$now" >"$STAMP_PATH"
fi

CURRENT=$(current_version)
VERSION=$CURRENT
write_status

[ -s "$KEYRING" ] || fail "release key missing ($KEYRING); re-run the installer"
command -v gpgv >/dev/null 2>&1 || fail "gpgv is not installed"

TARGET=$(resolve_latest) || fail "could not resolve the latest release"
rc=0
DIGEST=$(signed_digest "$TARGET") || rc=$?
case "$rc" in
    0) ;;
    2) fail "could not download OCI-DIGESTS for $TARGET" ;;
    3) fail "OCI-DIGESTS signature check failed for $TARGET" ;;
    *) fail "no signed image digest for $TARGET" ;;
esac

if [ -n "$CURRENT" ]; then
    if version_gt "$CURRENT" "$TARGET"; then
        fail "refusing downgrade from $CURRENT to $TARGET"
    fi
    if [ "$CURRENT" = "$TARGET" ] && grep -qx "MESHLOOM_IMAGE=${IMAGE_REPO}:${TARGET}@${DIGEST}" "$ENV_FILE"; then
        if [ "$MODE" = apply ]; then
            # Already pinned: still make sure that image is the one running.
            :
        else
            FINISHED=1
            STATE=succeeded
            PHASE=done
            PERCENT=100
            write_status
            exit 0
        fi
    fi
elif [ "$MODE" = apply ]; then
    fail "no valid MESHLOOM_IMAGE pin in $ENV_FILE; re-run the installer"
fi

write_env "$TARGET" "$DIGEST"
log "pinned ${IMAGE_REPO}:${TARGET}@${DIGEST}"

if [ "$MODE" = bootstrap ]; then
    FINISHED=1
    STATE=succeeded
    PHASE=done
    PERCENT=100
    VERSION=$TARGET
    write_status
    exit 0
fi

cd "$COMPOSE_DIR"
PHASE=downloading
write_status
docker compose pull || fail "docker compose pull failed"
PHASE=restarting
PERCENT=90
write_status
docker compose up -d || fail "docker compose up -d failed"
FINISHED=1
STATE=succeeded
PHASE=done
PERCENT=100
VERSION=$TARGET
write_status
log "running ${TARGET}"
MESHLOOM_EMBED_EOF
}
# <<< embed: scripts/setup/helpers/compose-update

# >>> embed: scripts/setup/helpers/meshloom-compose-update.service.in (generated by scripts/setup/sync_installer_embeds.py; do not edit)
_embed_compose_service() {
    cat <<'MESHLOOM_EMBED_EOF'
[Unit]
Description=Meshloom Docker Compose update
Documentation=https://github.com/TwinRocket/meshloom
After=docker.service network-online.target
Requires=docker.service
Wants=network-online.target
# No systemd start limit: hitting it fails the .path unit for good, and
# in-app updates would stop until reboot. The helper's 120 s cooldown is
# the rate limit.
StartLimitIntervalSec=0

[Service]
Type=oneshot
# Holds MESHLOOM_COMPOSE_DIR only. Root-owned; the helper validates it.
EnvironmentFile=/etc/meshloom/compose-update.env
Environment=DOCKER_CONFIG=/run/meshloom-compose-update/docker
ExecStart=/usr/lib/meshloom/compose-update
TimeoutStartSec=30min
StateDirectory=meshloom-compose-update
StateDirectoryMode=0700
RuntimeDirectory=meshloom-compose-update
RuntimeDirectoryMode=0700
UMask=0022
PrivateTmp=yes
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@COMPOSE_DIR@
MESHLOOM_EMBED_EOF
}
# <<< embed: scripts/setup/helpers/meshloom-compose-update.service.in

# >>> embed: scripts/setup/helpers/meshloom-compose-update.path.in (generated by scripts/setup/sync_installer_embeds.py; do not edit)
_embed_compose_path() {
    cat <<'MESHLOOM_EMBED_EOF'
[Unit]
Description=Watch Meshloom compose update request
Documentation=https://github.com/TwinRocket/meshloom

[Path]
# Edge-triggered only. The helper never reads anything under data/.
PathChanged=@COMPOSE_DIR@/data/request-update
Unit=meshloom-compose-update.service

[Install]
WantedBy=multi-user.target
MESHLOOM_EMBED_EOF
}
# <<< embed: scripts/setup/helpers/meshloom-compose-update.path.in

# The helper runs as root on the host, so it only exists where it can do its
# job safely: Linux, rootful Docker (the root daemon is the one it drives),
# systemd running, and a compose path systemd and the helper both accept.
compose_helper_possible() {
    [ "$OS_FAMILY" = "linux" ] && [ "$DOCKER_KIND" = "linux-rootful" ] &&
        [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1 &&
        compose_dir_is_safe "$1"
}

compose_dir_is_safe() {
    printf '%s\n' "$1" | grep -Eq '^/[A-Za-z0-9._/-]+$' || return 1
    case "/$1/" in
        */../* | */./*) return 1 ;;
    esac
    return 0
}

_stop_compose_update_helper() {
    as_root systemctl disable --now meshloom-compose-update.path >/dev/null 2>&1 || true
    as_root systemctl stop meshloom-compose-update.service >/dev/null 2>&1 || true
}

# Writes the helper, its root-only config and units, the root-owned status
# directory, and pins the image (.env) through the helper's own signature
# checks. The path unit is enabled by enable_compose_update_helper once the
# stack runs.
install_compose_update_helper() {
    local dir="$1" tmp
    prepare_release_key
    ensure_gpgv
    install_release_key
    _stop_compose_update_helper
    tmp="$(mktemp -d /tmp/meshloom-helper.XXXXXX)"
    _embed_compose_update >"$tmp/compose-update"
    _embed_compose_service | sed "s|@COMPOSE_DIR@|${dir}|g" >"$tmp/meshloom-compose-update.service"
    _embed_compose_path | sed "s|@COMPOSE_DIR@|${dir}|g" >"$tmp/meshloom-compose-update.path"
    printf 'MESHLOOM_COMPOSE_DIR=%s\n' "$dir" >"$tmp/compose-update.env"
    secure_etc_meshloom
    as_root install -D -m 0755 "$tmp/compose-update" /usr/lib/meshloom/compose-update
    install_etc_file "$tmp/compose-update.env" /etc/meshloom/compose-update.env 0644
    as_root install -D -m 0644 "$tmp/meshloom-compose-update.service" \
        /etc/systemd/system/meshloom-compose-update.service
    as_root install -D -m 0644 "$tmp/meshloom-compose-update.path" \
        /etc/systemd/system/meshloom-compose-update.path
    rm -rf "$tmp"
    as_root install -d -m 0755 -o root -g root "${dir}/update-status"
    mkdir -p "${dir}/data"
    # Up to 4.17 the root helper read its target back from this file.
    as_root rm -f "${dir}/data/update-job.json"
    as_root systemctl daemon-reload || true
    if ! run_soft as_root env MESHLOOM_COMPOSE_DIR="$dir" /usr/lib/meshloom/compose-update --bootstrap; then
        ui_err "$(t compose_pin_failed)"
        tail -n 20 "$INSTALL_LOG" >&2 || true
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        exit 1
    fi
}

enable_compose_update_helper() {
    as_root systemctl enable --now meshloom-compose-update.path || true
}

write_meshloom_env() {
    local dest="$1" tmp
    secure_etc_meshloom
    if [ ! -f "$dest" ]; then
        tmp="$(mktemp /tmp/meshloom-env.XXXXXX)"
        {
            echo "# Generated by Meshloom install.sh"
            echo "# Radio transport is configured in the web UI (app_settings), not here."
            echo "MESHCORE_DATABASE_PATH=/var/lib/meshloom/meshcore.db"
            echo "MESHLOOM_INSTALL_KIND=package"
        } >"$tmp"
        install_etc_file "$tmp" "$dest" 0640
        rm -f "$tmp"
    fi
    # Do not clobber a packaged env. Only fill missing keys the one-liner owns.
    _env_ensure_key "$dest" MESHCORE_DATABASE_PATH /var/lib/meshloom/meshcore.db
    _env_ensure_key "$dest" MESHLOOM_INSTALL_KIND package
    # In-app updates need this machine's architecture in the signed repository.
    if [ "${REPO_HAS_ARCH:-1}" = 1 ]; then
        _env_drop_key "$dest" MESHLOOM_UPDATE_HELPER
    else
        _env_set_key "$dest" MESHLOOM_UPDATE_HELPER none
    fi
    as_root chmod 640 "$dest" || true
}

start_meshloom_unit() {
    as_root systemctl daemon-reload
    as_root systemctl enable meshloom
    as_root systemctl restart meshloom
}

install_from_pages() {
    phase "$(t using_repo)"
    add_signed_repo
    if [ "$PKG_MGR" = "apt" ]; then
        run_quiet as_root apt-get update
        run_quiet as_root apt-get install -y meshloom
    else
        run_quiet as_root dnf install -y meshloom
    fi
    REPO_HAS_ARCH=1
    write_meshloom_env /etc/meshloom/meshloom.env
    start_meshloom_unit
    persist_installer_state
    ensure_update_helper
    phase_ok
}

# Signed release manifest: SHA256SUMS checked with gpgv against the embedded key.
# Prints nothing and fails on any problem; never falls back to unsigned.
fetch_signed_manifest() {
    local tag="$1" dir="$2" base="https://github.com/${REPO}/releases/download/${tag}"
    run_soft curl -fsSL --proto '=https' --max-time 60 "${base}/SHA256SUMS" -o "${dir}/SHA256SUMS" || return 1
    run_soft curl -fsSL --proto '=https' --max-time 60 "${base}/SHA256SUMS.asc" -o "${dir}/SHA256SUMS.asc" || return 1
    run_soft gpgv --keyring "${RELEASE_KEY_DIR}/meshloom.gpg" "${dir}/SHA256SUMS.asc" "${dir}/SHA256SUMS"
}

install_from_release_asset() {
    local arch suffix url tmp tmpdir pkg_ext pkg_kind tag name expected
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
    # From here on there is a package for this machine: any failure is an
    # error, never a silent switch to another install method.
    case "$url" in
        "https://github.com/${REPO}/releases/download/"*) ;;
        *)
            ui_err "$(t asset_bad)"
            exit 1
            ;;
    esac
    tag="${url#"https://github.com/${REPO}/releases/download/"}"
    name="${tag#*/}"
    tag="${tag%%/*}"
    case "$tag" in
        '' | *[!0-9.]*) tag="" ;;
    esac
    case "$name" in
        *[!A-Za-z0-9._+~-]*) name="" ;;
    esac
    if ! printf '%s\n' "$tag" | grep -Eq "$VERSION_RE" ||
        ! printf '%s\n' "$name" | grep -Eq '^meshloom[A-Za-z0-9._+~-]*$'; then
        ui_err "$(t asset_bad)"
        exit 1
    fi
    phase "$(t using_asset)"
    ensure_gpgv
    prepare_release_key
    tmpdir="$(mktemp -d /tmp/meshloom-release.XXXXXX)"
    if ! fetch_signed_manifest "$tag" "$tmpdir"; then
        ui_err "$(t sig_failed)"
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        rm -rf "$tmpdir"
        exit 1
    fi
    expected="$(awk -v f="$name" '$2 == f || $2 == "*" f { print $1 }' "${tmpdir}/SHA256SUMS" | head -n 1)"
    # apt only accepts local files whose name ends in .deb / .ddeb / .changes.
    tmp="${tmpdir}/meshloom${pkg_ext}"
    if ! run_soft curl -fL --proto '=https' --max-time 180 "$url" -o "$tmp" ||
        ! pkg_file_is_valid "$tmp" "$pkg_kind" ||
        ! printf '%s\n' "$expected" | grep -Eq '^[0-9a-f]{64}$' ||
        [ "$(sha256_of "$tmp")" != "$expected" ]; then
        log_note "release asset rejected: url=${url} bytes=$(file_size "$tmp") expected=${expected}"
        ui_err "$(t sig_failed)"
        printf '  %s: %s\n' "$(t log_at)" "$INSTALL_LOG" >&2
        rm -rf "$tmpdir"
        exit 1
    fi
    if [ "$PKG_MGR" = "apt" ]; then
        run_quiet as_root apt-get install -y "$tmp"
    else
        run_quiet as_root dnf install -y "$tmp"
    fi
    rm -rf "$tmpdir"
    # Always add the signed repository so later updates come from it. When it
    # does not carry this architecture yet, in-app updates stay off.
    add_signed_repo
    REPO_HAS_ARCH=0
    if [ "$PKG_MGR" = "apt" ] && pages_apt_has_host_arch; then
        REPO_HAS_ARCH=1
    elif [ "$PKG_MGR" = "dnf" ] && http_ok "${PAGES_BASE}/rpm/$(rpm_arch)/repodata/repomd.xml"; then
        REPO_HAS_ARCH=1
    fi
    write_meshloom_env /etc/meshloom/meshloom.env
    start_meshloom_unit
    persist_installer_state
    ensure_update_helper
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
    # The architecture check is what keeps a 32-bit Raspberry Pi out of a
    # repository built for amd64 and arm64. Without it the installer added the
    # repository, apt found no candidate, and the source install below, which
    # works there, was never reached.
    if [ "$PKG_MGR" = "apt" ] && pages_apt_has_host_arch; then
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

# $1 = dir, $2 = 1 when the root update helper manages this stack.
# The image is never written here: compose reads MESHLOOM_IMAGE from .env,
# which the helper pins by digest. Updates never edit this file.
write_docker_compose() {
    local dir="$1" managed="${2:-0}"
    mkdir -p "${dir}/data"
    {
        echo "# Generated by Meshloom install.sh. Re-run the installer to regenerate it."
        echo "# The image comes from .env (MESHLOOM_IMAGE), pinned by digest."
        echo "services:"
        echo "  meshloom:"
        echo "    image: \${MESHLOOM_IMAGE:?run the Meshloom installer to pin the image in .env}"
        echo "    ports:"
        echo "      - \"8000:8000\""
        echo "    volumes:"
        echo "      - ./data:/app/data"
        if [ "$managed" = 1 ]; then
            echo "      # Written by the root update helper; read-only for the container."
            echo "      - ./update-status:/app/update-status:ro"
        fi
        if [ -n "$DBUS_SOCKET" ]; then
            echo "      # Host D-Bus socket (BlueZ). Extra caps such as NET_ADMIN may still be needed for BLE."
            echo "      - ${DBUS_SOCKET}:/run/dbus/system_bus_socket:ro"
        fi
        if [ -n "$SERIAL_COMPOSE_HOST_PATH" ]; then
            echo "    devices:"
            echo "      - ${SERIAL_COMPOSE_HOST_PATH}:/dev/meshcore-radio"
        fi
        echo "    environment:"
        echo "      MESHCORE_DATABASE_PATH: $(yaml_quote "data/meshcore.db")"
        echo "      MESHLOOM_INSTALL_KIND: compose"
        if [ "$managed" = 1 ]; then
            echo "      MESHLOOM_UPDATE_HELPER: compose"
            echo "      MESHLOOM_UPDATE_JOB_PATH: /app/data/update-job.json"
            echo "      MESHLOOM_UPDATE_STATUS_PATH: /app/update-status/status.json"
        fi
        if [ "${RUN_AS_USER:-}" = 1 ]; then
            echo "      # Runs Meshloom as uid 10001 instead of root. Remove this line for"
            echo "      # Bluetooth, or if the radio stops answering."
            echo "      MESHLOOM_RUN_AS_USER: \"10001\""
        fi
        echo "    restart: unless-stopped"
    } >"${dir}/docker-compose.yml"
}

# Stacks without the root helper (Docker Desktop, rootless Docker) follow
# :latest, so the manual "pull && up -d" recipe keeps upgrading them.
write_unmanaged_env() {
    local dir="$1"
    if [ -f "${dir}/.env" ]; then
        grep -v '^MESHLOOM_IMAGE=' "${dir}/.env" >"${dir}/.env.new" || true
    else
        : >"${dir}/.env.new"
    fi
    { echo "MESHLOOM_IMAGE=${GHCR_IMAGE}:latest"; cat "${dir}/.env.new"; } >"${dir}/.env"
    rm -f "${dir}/.env.new"
}

# Where an existing installer-managed stack lives. 4.17 never saved it, so
# also ask the old helper's config and a running Meshloom container.
saved_compose_dir() {
    local dir=""
    dir="$(conf_get "$(system_installer_conf)" compose_dir 2>/dev/null ||
        conf_get "$(user_installer_conf)" compose_dir 2>/dev/null || true)"
    if [ -z "$dir" ] && [ -r /etc/meshloom/compose-update.env ]; then
        dir="$(sed -n 's/^MESHLOOM_COMPOSE_DIR=//p' /etc/meshloom/compose-update.env | head -n 1)"
    fi
    if [ -z "$dir" ] && command -v docker >/dev/null 2>&1; then
        dir="$({ docker ps -a --filter label=com.docker.compose.service=meshloom \
            --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null ||
            command sudo -n docker ps -a --filter label=com.docker.compose.service=meshloom \
                --format '{{.Label "com.docker.compose.project.working_dir"}}' 2>/dev/null; } |
            grep -m 1 '^/' || true)"
    fi
    if [ -n "$dir" ] && [ -f "${dir}/docker-compose.yml" ]; then
        printf '%s' "$dir"
    fi
}

# Radio mappings of an existing installer-written compose file.
read_compose_mappings() {
    local file="$1"
    SERIAL_COMPOSE_HOST_PATH="$(sed -n 's|^      - \(.*\):/dev/meshcore-radio$|\1|p' "$file" | head -n 1)"
    DBUS_SOCKET="$(sed -n 's|^      - \(.*\):/run/dbus/system_bus_socket:ro$|\1|p' "$file" | head -n 1)"
    if [ -n "$SERIAL_COMPOSE_HOST_PATH" ]; then
        TRANSPORT="serial"
        SERIAL_PORT="$SERIAL_COMPOSE_HOST_PATH"
    fi
}

# The parts every installer version writes, without the ones that change
# between versions (header comments, image, update helper and uid lines).
normalize_generated_compose() {
    sed -e '/^#/d' \
        -e 's|^    image: .*$|    image: <image>|' \
        -e '/MESHLOOM_UPDATE_HELPER:/d' -e '/MESHLOOM_UPDATE_JOB_PATH:/d' \
        -e '/MESHLOOM_UPDATE_STATUS_PATH:/d' -e '/MESHLOOM_RUN_AS_USER:/d' \
        -e '/update-status:\/app\/update-status/d' \
        -e '/^      # Written by the root update helper/d' \
        -e '/^      # Runs Meshloom as uid 10001/d' -e '/^      # Bluetooth, or if the radio stops/d' \
        "$1"
}

# True when an existing compose file is exactly what an installer wrote, so
# regenerating it loses nothing. Otherwise print the difference and stop.
compose_is_installer_generated() {
    local file="$1" expected tmpdir
    tmpdir="$(mktemp -d /tmp/meshloom-compose.XXXXXX)"
    (
        RUN_AS_USER=0
        write_docker_compose "$tmpdir" 0
    )
    normalize_generated_compose "$tmpdir/docker-compose.yml" >"$tmpdir/expected"
    normalize_generated_compose "$file" >"$tmpdir/current"
    if cmp -s "$tmpdir/expected" "$tmpdir/current"; then
        rm -rf "$tmpdir"
        return 0
    fi
    ui_warn "  $(t compose_custom)"
    diff -u "$tmpdir/expected" "$tmpdir/current" | sed 's/^/    /' >&2 || true
    rm -rf "$tmpdir"
    return 1
}

install_docker_stack() {
    local default dc existing=0 managed=0 saved
    ensure_docker
    detect_docker
    prepare_docker_mappings
    if [ -n "$SERIAL_COMPOSE_HOST_PATH" ] && ! docker_allows_usb; then
        ui_err "$(t docker_usb_needs_root)"
        TRANSPORT="ui"
        SERIAL_PORT=""
        SERIAL_COMPOSE_HOST_PATH=""
    fi
    saved="$(saved_compose_dir)"
    default="${saved:-${IN_CHECKOUT:-${HOME}/meshloom}}"
    if [ -n "$saved" ]; then
        ui_dim "  $(t compose_found) ${saved}"
    fi
    INSTALL_DIR="$(ui_ask "$(t prompt_dir)" "$default")"
    INSTALL_DIR="${INSTALL_DIR:-$default}"
    case "$INSTALL_DIR" in
        /*) ;;
        *) INSTALL_DIR="$(pwd)/${INSTALL_DIR}" ;;
    esac
    INSTALL_DIR="${INSTALL_DIR%/}"
    COMPOSE_DIR_SAVED="$INSTALL_DIR"
    if [ -f "${INSTALL_DIR}/docker-compose.yml" ]; then
        existing=1
        # Keep the stack's own radio mappings, and never overwrite edits.
        read_compose_mappings "${INSTALL_DIR}/docker-compose.yml"
        if ! compose_is_installer_generated "${INSTALL_DIR}/docker-compose.yml" &&
            [ "${MESHLOOM_COMPOSE_OVERWRITE:-}" != 1 ]; then
            ui_err "$(t compose_custom_stop)"
            exit 1
        fi
    fi
    confirm_install
    mkdir -p "$INSTALL_DIR"
    if compose_helper_possible "$INSTALL_DIR"; then
        managed=1
    elif [ "$OS_FAMILY" = "linux" ] && [ "$DOCKER_KIND" = "linux-rootful" ] &&
        ! compose_dir_is_safe "$INSTALL_DIR"; then
        ui_warn "  $(t compose_dir_unsafe)"
    fi
    # Non-root container: opt-in, new serial installs only, never on upgrade.
    RUN_AS_USER=0
    if [ "$existing" = 0 ] && [ -n "$SERIAL_COMPOSE_HOST_PATH" ]; then
        RUN_AS_USER=1
    fi
    if [ "$existing" = 1 ]; then
        cp -p "${INSTALL_DIR}/docker-compose.yml" \
            "${INSTALL_DIR}/docker-compose.yml.bak-$(date +%Y%m%d-%H%M%S)"
        ui_dim "  $(t compose_backup)"
    fi
    if [ "$managed" = 1 ]; then
        phase "$(t compose_secure)"
        install_compose_update_helper "$INSTALL_DIR"
        phase_ok
    else
        write_unmanaged_env "$INSTALL_DIR"
    fi
    write_docker_compose "$INSTALL_DIR" "$managed"
    ui_dim "  $(t wrote_config) ${INSTALL_DIR}/docker-compose.yml"
    dc="$(compose_cmd)"
    # An existing stack restarts right away so the app and the helper match.
    if [ "$existing" = 1 ] || ui_yesno "$(t q_start_now)" y; then
        phase "$(t working)"
        (
            cd "$INSTALL_DIR"
            run_quiet as_root $dc pull
            run_quiet as_root $dc up -d
        )
        phase_ok
    fi
    persist_installer_state
    if [ "$managed" = 1 ]; then
        enable_compose_update_helper
    fi
    printf '\n'
    if [ "$UPGRADE_KIND" = "upgrade" ]; then
        ui_ok "  $(t done_upgrade)"
    else
        ui_ok "  $(t done)"
    fi
    printf '  %s\n    %s\n' "$(t open_at)" "http://127.0.0.1:8000"
    if [ "$managed" = 1 ]; then
        ui_dim "  $(t update_docker_managed)"
    else
        ui_dim "  $(t update_docker): $(priv "$dc pull") && $(priv "$dc up -d")"
    fi
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

if [ "$INSTALL_MODE" = "service" ]; then
    STEP_TOTAL=2
    INSTALL_STEP=2
    TRANSPORT="ui"
    install_native_service
else
    install_docker_stack
fi
