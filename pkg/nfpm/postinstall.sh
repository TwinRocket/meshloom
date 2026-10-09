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

if command -v systemctl >/dev/null 2>&1; then
    # Works offline too (image builds, chroots): enable only writes the
    # wants/ symlink. Before 4.18 this ran only with systemd up, so baked
    # Raspberry Pi images never watched for update requests.
    systemctl enable meshloom-update.path || true
fi

if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
    # PathChanged= is edge-triggered: a leftover request file cannot start an
    # apply, so the watcher can start right away.
    systemctl stop meshloom-update.path || true
    systemctl start meshloom-update.path || true
    # Deb: $1=configure $2=old-version. RPM %post: $1>=2 on upgrade.
    # Fresh install leaves meshloom off so the operator can set the radio first.
    if { [ "$1" = "configure" ] && [ -n "$2" ]; } || [ "$1" = "2" ] || [ "$1" = "upgrade" ]; then
        systemctl enable meshloom || true
        # In-app 4.13.4 POST is blocked on systemctl start. SIGTERM lets uvicorn
        # finish that request as job.failed + "See systemctl status…".
        # kill -s SIGKILL is immediate and does not depend on a drop-in reload.
        # https://www.freedesktop.org/software/systemd/man/latest/systemctl.html
        systemctl kill --kill-whom=all -s SIGKILL meshloom.service || true
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

echo "==> Choose USB, TCP, or Bluetooth in the web UI (Settings > Radio)."
echo "==> Start Meshloom with: sudo systemctl enable --now meshloom"
echo "==> UI: http://localhost:8000"
