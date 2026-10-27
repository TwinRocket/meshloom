#!/bin/sh
# Run the shipped fail2ban filter and jail through the real fail2ban tools.
#
#   scripts/test/fail2ban_filter.sh [ROOT]
#
# ROOT is where the package put /etc/fail2ban (an unpacked .deb); default: the
# source tree files (pkg/nfpm/fail2ban). The sample lines are the "Example" block
# of docs/dev/auth-fail-log.md plus the journal form of the same lines, so the
# filter cannot drift from the AUTH_FAIL contract. Needs fail2ban-regex and
# fail2ban-client on PATH. MSHL-423.
set -eu

here="$(cd "$(dirname "$0")/../.." && pwd)"
root="${1:-}"
if [ -n "$root" ]; then
    filter="$root/etc/fail2ban/filter.d/meshloom.conf"
    jail="$root/etc/fail2ban/jail.d/meshloom.conf"
else
    filter="$here/pkg/nfpm/fail2ban/meshloom-filter.conf"
    jail="$here/pkg/nfpm/fail2ban/meshloom-jail.conf"
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# The three lines of the contract's Example block (the only ones starting with a date).
grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8} - meshloom\.auth - WARNING - AUTH_FAIL ' \
    "$here/docs/dev/auth-fail-log.md" >"$tmp/file.log"
[ "$(wc -l <"$tmp/file.log")" -ge 3 ] || { echo "contract examples not found" >&2; exit 1; }
# What the systemd backend hands to the filter: "<host> <ident>[<pid>]: <message>".
sed 's/^/host uvicorn[812]: /' "$tmp/file.log" >"$tmp/journal.log"

proxied="$(grep -c 'proxied=1' "$tmp/file.log")"
total="$(wc -l <"$tmp/file.log")"
want=$((total - proxied))
if [ "$proxied" -lt 1 ] || [ "$want" -lt 1 ]; then
    echo "contract examples lack a proxied=1 or a proxied=0 line" >&2
    exit 1
fi

for log in file journal; do
    out="$(fail2ban-regex "$tmp/$log.log" "$filter" 2>&1)"
    echo "$out" | grep -E '^Lines:'
    echo "$out" | grep -qE "^Lines: $total lines, 0 ignored, $want matched, $proxied missed" \
        || { echo "$log form: expected $want matched and $proxied missed" >&2; echo "$out" >&2; exit 1; }
done

# The jail: disabled, and the whole configuration parses with the filter in place.
grep -qE '^enabled *= *false$' "$jail" || { echo "jail is not shipped disabled" >&2; exit 1; }
cfg="$tmp/fail2ban"
mkdir -p "$cfg"
cp -r /etc/fail2ban/. "$cfg/"
cp "$filter" "$cfg/filter.d/meshloom.conf"
cp "$jail" "$cfg/jail.d/meshloom.conf"
fail2ban-client -c "$cfg" -t >/dev/null
echo "fail2ban filter and jail OK"
