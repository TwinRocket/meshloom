#!/bin/sh
set -e
# RPM runs this after the old package's %preun. 4.12.x preremove disabled
# meshloom on upgrade; bring it back when this node already had data.
if [ ! -d /run/systemd/system ] || ! command -v systemctl >/dev/null 2>&1; then
    exit 0
fi
systemctl daemon-reload || true
if systemctl is-enabled meshloom >/dev/null 2>&1 || [ -f /var/lib/meshloom/meshcore.db ]; then
    systemctl enable meshloom || true
    systemctl start meshloom || true
fi
