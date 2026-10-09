#!/usr/bin/env bash
# Upgrade smoke test, package mode (apt): 4.17.0 as installed today -> N.
#
# Manual gate from the remediation plan ("Matrice de tests d'upgrade"); not run
# in CI. Needs Docker able to run a privileged systemd container, and a .deb of
# release N built with the real release key (scripts/build/build_nfpm_packages.sh).
#
#   scripts/test/upgrade_matrix/package_apt.sh dist/meshloom_4.18.0-1_amd64.deb [debian:12|ubuntu:24.04]
#
# What it checks:
#   1. 4.17.0 installed from the published repo with the source today's
#      installer writes ([trusted=yes]) and the path unit enabled.
#   2. Hostile files planted where 4.17 let the app write (symlinked
#      update-job.json, JSON with a newline in "target").
#   3. N installed over it the way the 4.17 helper does it (apt-get install).
#   4. After N's postinstall: source rewritten to signed-by=, apt pin present,
#      /etc/meshloom root:meshloom 0750, update-job.json gone, path unit enabled
#      and edge-triggered, symlink target untouched.
#   5. An in-app style trigger (touch as meshloom) runs the N helper, which
#      publishes /var/lib/meshloom-update/status.json and leaves app files alone.
set -euo pipefail

DEB="${1:?usage: $0 <meshloom_N.deb> [base image]}"
BASE="${2:-debian:12}"
NAME="meshloom-upgrade-$$"
PAGES="https://twinrocket.github.io/meshloom"

[ -f "$DEB" ] || { echo "no such file: $DEB" >&2; exit 1; }

cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

image="meshloom-upgrade-base:${BASE//[:\/]/-}"
docker build -q -t "$image" - <<DOCKERFILE >/dev/null
FROM ${BASE}
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      systemd systemd-sysv dbus ca-certificates curl gpgv && rm -rf /var/lib/apt/lists/*
CMD ["/sbin/init"]
DOCKERFILE

docker run -d --name "$NAME" --privileged --cgroupns=host \
    -v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock "$image" >/dev/null
docker cp "$DEB" "$NAME:/tmp/meshloom-new.deb"
run_in() { docker exec "$NAME" bash -euo pipefail -c "$1"; }

for _ in $(seq 30); do run_in 'systemctl is-system-running --wait >/dev/null 2>&1 || [ "$(systemctl is-system-running)" = degraded ]' && break; sleep 1; done

echo "== 1. install 4.17.0 the way the 4.17 installer does"
run_in "echo 'deb [trusted=yes] ${PAGES}/apt stable main' >/etc/apt/sources.list.d/meshloom.list
    apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q meshloom=4.17.0-1
    systemctl enable --now meshloom meshloom-update.path"

echo "== 2. plant hostile files"
run_in "echo precious >/root/precious
    ln -sf /root/precious /var/lib/meshloom/update-job.json
    chown -h meshloom:meshloom /var/lib/meshloom/update-job.json"

echo "== 3. upgrade to N as the 4.17 helper would"
run_in "DEBIAN_FRONTEND=noninteractive apt-get install -y -q /tmp/meshloom-new.deb"

echo "== 4. check the migration"
run_in "grep -q 'signed-by=/usr/share/keyrings/meshloom-archive-keyring.gpg' /etc/apt/sources.list.d/meshloom.list
    ! grep -q trusted=yes /etc/apt/sources.list.d/meshloom.list
    grep -q 'Pin: release o=Meshloom' /etc/apt/preferences.d/meshloom.pref
    [ \"\$(stat -c '%U:%G %a' /etc/meshloom)\" = 'root:meshloom 750' ]
    [ ! -e /var/lib/meshloom/update-job.json ] && [ ! -L /var/lib/meshloom/update-job.json ]
    [ \"\$(cat /root/precious)\" = precious ]
    systemctl is-enabled meshloom-update.path
    ! grep -q PathExists /usr/lib/systemd/system/meshloom-update.path
    systemctl is-active meshloom
    apt-get update -q"

echo "== 5. trigger an update as the app would"
run_in "printf '{\"state\":\"applying\",\"target\":\"1.0\\nimage: evil\"}' >/var/lib/meshloom/update-job.json
    chown meshloom:meshloom /var/lib/meshloom/update-job.json
    su -s /bin/sh meshloom -c 'echo 1 >/var/lib/meshloom/request-update'
    for _ in \$(seq 120); do
        grep -qE '\"state\":\"(succeeded|failed)\"' /var/lib/meshloom-update/status.json 2>/dev/null && break
        sleep 1
    done
    cat /var/lib/meshloom-update/status.json
    grep -q '\"state\":\"succeeded\"' /var/lib/meshloom-update/status.json
    [ \"\$(stat -c %U /var/lib/meshloom-update/status.json)\" = root ]
    grep -q evil /var/lib/meshloom/update-job.json
    [ -e /var/lib/meshloom/request-update ]"

echo "OK: ${BASE} 4.17.0 -> $(basename "$DEB")"
