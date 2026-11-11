#!/bin/sh
set -e

OFFICIAL_URL="https://twinrocket.github.io/meshloom"
KEYRING=/usr/share/keyrings/meshloom-archive-keyring.gpg
KEYRING_ASC=/usr/share/keyrings/meshloom-archive-keyring.asc

if command -v systemd-sysusers >/dev/null 2>&1; then
    systemd-sysusers meshloom.conf >/dev/null 2>&1 || systemd-sysusers /usr/lib/sysusers.d/meshloom.conf || true
fi
if command -v systemd-tmpfiles >/dev/null 2>&1; then
    systemd-tmpfiles --create /usr/lib/tmpfiles.d/meshloom.conf || true
fi

if command -v usermod >/dev/null 2>&1 && getent passwd meshloom >/dev/null 2>&1; then
    if getent group dialout >/dev/null 2>&1; then
        usermod -aG dialout meshloom || true
    fi
    # BLE pairing (app/services/ble_pairing.py) calls BlueZ over D-Bus. The
    # BlueZ policies of Debian 12/13 and Fedora let any local user do that;
    # the group is kept for hardened policies that only allow it.
    if getent group bluetooth >/dev/null 2>&1; then
        usermod -aG bluetooth meshloom || true
    fi
fi

# /etc/meshloom holds files root reads (meshloom.env, installer.conf,
# compose-update.env). The app may read it, never write it.
mkdir -p /etc/meshloom
if getent group meshloom >/dev/null 2>&1; then
    chown root:meshloom /etc/meshloom
else
    chown root:root /etc/meshloom
fi
chmod 0750 /etc/meshloom

# Up to 4.17 the meshloom user owned /etc/meshloom and could leave anything
# there for root to read or follow (a symlink to /etc/shadow, a forged
# compose-update.env). Now that it can no longer add entries, move every
# symlink, non-root entry and hard-linked file to a root-only quarantine
# before any root code reads the directory again.
quarantine=
for entry in /etc/meshloom/* /etc/meshloom/.[!.]* /etc/meshloom/..?*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    if [ -L "$entry" ] || [ "$(stat -c %u "$entry")" != 0 ] \
        || { [ -f "$entry" ] && [ "$(stat -c %h "$entry")" != 1 ]; }; then
        if [ -z "$quarantine" ]; then
            mkdir -p -m 0700 /var/lib/meshloom-quarantine
            quarantine=$(mktemp -d /var/lib/meshloom-quarantine/etc.XXXXXX)
        fi
        mv -f "$entry" "$quarantine/"
        echo "==> Moved untrusted $entry to $quarantine (not owned by root, or a link)."
    fi
done
if [ ! -f /etc/meshloom/meshloom.env ]; then
    # meshloom.service needs its EnvironmentFile; recreate the packaged defaults.
    printf '%s\n' "MESHCORE_DATABASE_PATH=/var/lib/meshloom/meshcore.db" \
        "MESHLOOM_INSTALL_KIND=package" >/etc/meshloom/meshloom.env
    chmod 0640 /etc/meshloom/meshloom.env
fi

# Root-owned status directory for the update helper; the app only reads it.
if [ -L /var/lib/meshloom-update ]; then
    rm -f /var/lib/meshloom-update
fi
mkdir -p /var/lib/meshloom-update
chown root:root /var/lib/meshloom-update
chmod 0755 /var/lib/meshloom-update

# Up to 4.17 the helper wrote this file and read its target back as root.
# Nothing reads it any more; remove the leftover (rm never follows a symlink).
rm -f /var/lib/meshloom/update-job.json

# Move the official package source to signature checking. Only a source that
# points at the official repository is touched; a mirror is left alone and the
# update helper will refuse it until it is signed.
migrate_apt_source() {
    list=/etc/apt/sources.list.d/meshloom.list
    [ -f "$list" ] && [ -f "$KEYRING" ] || return 0
    grep -q "^[[:space:]]*deb[[:space:]].*${OFFICIAL_URL}/apt[[:space:]]" "$list" || return 0
    if grep -q "signed-by=${KEYRING}" "$list" && ! grep -q 'trusted=yes' "$list"; then
        return 0
    fi
    tmp=$(mktemp /etc/apt/sources.list.d/.meshloom.XXXXXX)
    printf '%s\n' "deb [signed-by=${KEYRING}] ${OFFICIAL_URL}/apt stable main" >"$tmp"
    chmod 0644 "$tmp"
    mv -f "$tmp" "$list"
    echo "==> Meshloom apt source now checks the repository signature."
}

migrate_dnf_source() {
    repo=/etc/yum.repos.d/meshloom.repo
    [ -f "$repo" ] && [ -f "$KEYRING_ASC" ] || return 0
    grep -q "^baseurl=${OFFICIAL_URL}/rpm/" "$repo" || return 0
    if grep -q '^gpgcheck=1' "$repo" && grep -q '^repo_gpgcheck=1' "$repo" \
        && grep -q "^gpgkey=file://${KEYRING_ASC}" "$repo"; then
        return 0
    fi
    tmp=$(mktemp /etc/yum.repos.d/.meshloom.XXXXXX)
    cat >"$tmp" <<EOF
[meshloom]
name=Meshloom
baseurl=${OFFICIAL_URL}/rpm/\$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=file://${KEYRING_ASC}
EOF
    chmod 0644 "$tmp"
    mv -f "$tmp" "$repo"
    echo "==> Meshloom dnf repository now checks package and metadata signatures."
}

migrate_apt_source
migrate_dnf_source

# Kiosk image flashed before 5.0.0: the image, not the deb, installed the
# autostart entry and the setup helper, so they identify it. The launcher is not
# a signal here: the deb ships it, a plain install has it too. The marker is
# read by the app (app/services/kiosk.py); never created on a plain install.
mark_kiosk_host() {
    [ -f /etc/xdg/autostart/meshloom-kiosk.desktop ] \
        || [ -f /usr/lib/meshloom/meshloom-kiosk-setup ] || return 0
    if [ -e /var/lib/meshloom/kiosk ] && [ ! -L /var/lib/meshloom/kiosk ]; then
        return 0
    fi
    mkdir -p /var/lib/meshloom || return 0
    # The meshloom user owns this directory and could plant a link at this name:
    # root never writes through it. Built in the root-owned directory, then renamed
    # over whatever is there (a rename replaces a link, it does not follow it).
    marker=$(mktemp /var/lib/meshloom-update/kiosk.XXXXXX) || return 0
    chmod 0644 "$marker" || true
    mv -fT "$marker" /var/lib/meshloom/kiosk || { rm -f "$marker"; return 0; }
    echo "==> Kiosk image detected: marked this host as a Meshloom kiosk."
}
mark_kiosk_host || true

# Up to 4.18 meshloom.service reset this directory to 0755 at every start, with a
# 0644 database inside. Closing the directory shuts out other accounts now; the
# server tightens the database files itself at its next start, as meshloom. Root
# does not walk this tree: the meshloom user could plant links in it.
state_dir=/var/lib/meshloom
if [ -d "$state_dir" ] && [ ! -L "$state_dir" ]; then
    chmod 0750 "$state_dir" || true
fi

# Kiosk device token (app/services/kiosk.py, docs/dev/auth.md "Kiosk device
# token"): the server writes /var/lib/meshloom-kiosk/kiosk.token (0640) and the
# desktop session user, uid 1000 as in meshloom-kiosk-setup, reads it through
# the meshloom-kiosk group. setgid directory: the file gets the group without
# meshloom being a member. Only on a kiosk host; the server refuses the
# directory unless meshloom owns it and others get nothing.
provision_kiosk_token_dir() {
    [ -e /var/lib/meshloom/kiosk ] || return 0
    getent passwd meshloom >/dev/null 2>&1 || return 0
    if ! getent group meshloom-kiosk >/dev/null 2>&1; then
        groupadd --system meshloom-kiosk || return 0
    fi
    dir=/var/lib/meshloom-kiosk
    if [ -L "$dir" ] || { [ -e "$dir" ] && [ ! -d "$dir" ]; }; then
        rm -f "$dir"
    fi
    mkdir -p "$dir"
    chown meshloom:meshloom-kiosk "$dir"
    chmod 2750 "$dir"
    # Upgrade of an image flashed before 5.0.0: its own meshloom-kiosk-setup
    # (installed by the image) does not know the group. Effective at the next
    # login; the launcher also tries `sg` meanwhile.
    session_user="$(getent passwd 1000 | cut -d: -f1 || true)"
    if [ -n "$session_user" ] && command -v usermod >/dev/null 2>&1; then
        usermod -aG meshloom-kiosk "$session_user" || true
    fi
}
provision_kiosk_token_dir || true

if command -v systemctl >/dev/null 2>&1; then
    # Works offline too (image builds, chroots): enable only writes the
    # wants/ symlink. Before 4.18 this ran only with systemd up, so baked
    # Raspberry Pi images never watched for update requests.
    systemctl enable meshloom-update.path || true
fi

# Deb: $1=configure $2=old-version. RPM %post: $1>=2 on upgrade.
is_upgrade() {
    { [ "$1" = "configure" ] && [ -n "$2" ]; } || [ "$1" = "2" ] || [ "$1" = "upgrade" ]
}

# True while an in-app update from 4.12.0 to 4.13.4 is running: that server's
# request waits on a blocking `systemctl start meshloom-update.service`, which
# itself waits for this script (4.13.5 and later pass --no-block, so they never
# match). pgrep comes with procps; without it, /proc is read directly.
old_server_waits_on_update() {
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -x -f 'systemctl start meshloom-update.service' >/dev/null 2>&1
        return
    fi
    for cmdline in /proc/[0-9]*/cmdline; do
        [ "$(tr '\0' ' ' <"$cmdline" 2>/dev/null)" = "systemctl start meshloom-update.service " ] &&
            return 0
    done
    return 1
}

# Stops the old server before the new one starts.
# - In-app update from 4.12.0 to 4.13.4: SIGKILL right away. meshloom.service has
#   the default KillMode=control-group, so a SIGTERM also reaches the waiting
#   systemctl client: it exits with an error, the server records the update as
#   failed and sends "failed" to the open page before it exits, although the old
#   helper then finishes the upgrade. SIGKILL gives it no chance to answer.
# - Otherwise: SIGTERM, so the server closes the radio and the database itself (a
#   normal stop takes well under a second), then SIGKILL after 20 s. Polled here
#   rather than through TimeoutStopSec=, which would need a drop-in and a reload.
#   is-active says "deactivating" while it stops.
stop_old_server() {
    if old_server_waits_on_update; then
        systemctl kill --kill-whom=all -s SIGKILL meshloom.service || true
        return 0
    fi
    systemctl stop --no-block meshloom.service || true
    i=0
    while [ "$i" -lt 20 ]; do
        case "$(systemctl is-active meshloom.service 2>/dev/null || true)" in
            active | deactivating | reloading) ;;
            *) return 0 ;;
        esac
        i=$((i + 1))
        sleep 1
    done
    systemctl kill --kill-whom=all -s SIGKILL meshloom.service || true
}

if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
    # PathChanged= is edge-triggered: a leftover request file cannot start an
    # apply, so the watcher can start right away.
    systemctl stop meshloom-update.path || true
    systemctl start meshloom-update.path || true
    # Fresh install leaves meshloom off so the operator can set the radio first.
    if is_upgrade "$@"; then
        systemctl enable meshloom || true
        stop_old_server
        systemctl reset-failed meshloom.service || true
        systemctl start meshloom || true
        i=0
        while [ "$i" -lt 15 ]; do
            systemctl is-active --quiet meshloom && break
            i=$((i + 1))
            sleep 1
        done
        if ! systemctl is-active --quiet meshloom; then
            systemctl start meshloom || true
        fi
    fi
fi

# An upgrade keeps the radio and starts the service: nothing to do then.
if ! is_upgrade "$@"; then
    echo "==> Choose USB, TCP, or Bluetooth in the web UI (Settings > Radio)."
    if ! { command -v systemctl >/dev/null 2>&1 && systemctl is-enabled --quiet meshloom 2>/dev/null; }; then
        echo "==> Start Meshloom with: sudo systemctl enable --now meshloom"
    fi
fi
echo "==> UI: http://localhost:8000"
