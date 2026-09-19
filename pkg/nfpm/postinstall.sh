#!/bin/sh
set -e

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

if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    # PathExists is level-triggered: a leftover request-update would start
    # meshloom-update.service immediately, then again when the oneshot exits.
    rm -f /var/lib/meshloom/request-update
    if command -v chown >/dev/null 2>&1 && getent passwd meshloom >/dev/null 2>&1; then
        chown meshloom:meshloom /var/lib/meshloom 2>/dev/null || true
        chmod 0750 /var/lib/meshloom 2>/dev/null || true
    fi
    systemctl daemon-reload || true
    # After rm, PathExists is false: --now starts the watcher, not a second apply.
    systemctl enable --now meshloom-update.path || true
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
