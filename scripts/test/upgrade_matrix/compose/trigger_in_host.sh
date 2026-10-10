#!/bin/sh
# Runs inside e2ec-host: trigger the compose helper as the container would.
# $1 = keep -> keep the cooldown stamp (to test the cooldown).
[ "${1:-}" = keep ] || rm -f /var/lib/meshloom-compose-update/last-start
cd /root/meshloom/data || exit 1
rm -f request-update
echo 1 >request-update
sleep 6
cat ../update-status/status.json
grep MESHLOOM_IMAGE ../.env
