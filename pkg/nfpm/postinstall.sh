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
    systemctl daemon-reload || true
    # Deb: $1=configure $2=old-version. RPM %post: $1>=2 on upgrade.
    # Fresh install leaves the unit off so the operator can set the radio first.
    if { [ "$1" = "configure" ] && [ -n "$2" ]; } || [ "$1" = "2" ] || [ "$1" = "upgrade" ]; then
        systemctl enable meshloom || true
        systemctl restart meshloom || systemctl start meshloom || true
    fi
fi

echo "==> Choose USB, TCP, or Bluetooth in the web UI (Settings > Radio)."
echo "==> Start Meshloom with: sudo systemctl enable --now meshloom"
echo "==> UI: http://localhost:8000"
