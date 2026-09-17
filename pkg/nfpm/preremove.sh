#!/bin/sh
set -e
# Debian prerm: $1 is remove | upgrade | deconfigure
# RPM %preun: $1 is 0 on erase, 1+ when another instance remains (upgrade)
case "$1" in
    upgrade | 1 | deconfigure)
        exit 0
        ;;
esac
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    systemctl disable --now meshloom 2>/dev/null || true
fi
