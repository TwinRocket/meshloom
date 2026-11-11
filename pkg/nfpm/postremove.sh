#!/bin/sh
set -e
if [ -d /run/systemd/system ] && command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
fi
# Debian postrm: $1 is remove | purge | upgrade | failed-upgrade | abort-* | disappear
# (a purge runs after a remove, which already said it). RPM %postun: $1 is 0 on
# erase, 1+ when another version remains (upgrade). Silent during an upgrade.
case "${1:-}" in
    remove | 0)
        echo "==> /var/lib/meshloom and /etc/meshloom were left in place."
        ;;
esac
