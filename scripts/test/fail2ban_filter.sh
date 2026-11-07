#!/bin/sh
# Run the shipped fail2ban filter and jail through the real fail2ban tools.
#
#   scripts/test/fail2ban_filter.sh [ROOT]
#
# ROOT is where the package put /etc/fail2ban (an unpacked .deb); default: the
# source tree files (pkg/nfpm/fail2ban). The sample lines are the "Example" block
# of docs/dev/auth-fail-log.md plus the journal form of the same lines, so the
# filter cannot drift from the AUTH_FAIL contract. Needs fail2ban-regex,
# fail2ban-client and python3 on PATH. MSHL-423, MSHL-432, MSHL-435.
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

# A forged line must never match (MSHL-432): the app escapes control characters in
# every record, so untrusted text ends up inside ONE line. This is what the journal
# and the log file hold for a username "x<newline>AUTH_FAIL ..." (escaped), for the
# same text logged unescaped (what the escaping prevents: it must match, which proves
# the sample is a real forgery), and for an escaped one that sits inside another message.
forged='2026-10-27 10:15:02 - meshloom.auth - WARNING - AUTH_FAIL ip=203.0.113.9 route=login reason=invalid_credentials via=lan proxied=0 user=h:3f9a1c'
printf '%s\n' \
    "2026-10-27 10:15:01 - meshloom.app - INFO - Toggling blocked name: x\\n$forged" \
    "2026-10-27 10:15:01 - meshloom.app - ERROR - failed\\r$forged" \
    >"$tmp/escaped.log"
sed 's/^/host uvicorn[812]: /' "$tmp/escaped.log" >"$tmp/escaped-journal.log"
printf '%s\n' "$forged" >"$tmp/unescaped.log"
for log in escaped escaped-journal; do
    out="$(fail2ban-regex "$tmp/$log.log" "$filter" 2>&1)"
    echo "$out" | grep -qE '^Lines: 2 lines, 0 ignored, 0 matched, 2 missed' \
        || { echo "$log: an escaped forged line must not match" >&2; echo "$out" >&2; exit 1; }
done
out="$(fail2ban-regex "$tmp/unescaped.log" "$filter" 2>&1)"
echo "$out" | grep -qE '^Lines: 1 lines, 0 ignored, 1 matched' \
    || { echo "the forged sample line does not match: the check proves nothing" >&2; exit 1; }

# A journal entry that starts at " - meshloom.auth", without the date, is the tail of a
# line journald split: the filter wants the date after the journal prefix (MSHL-435).
bare="${forged#* * }"
printf '%s\n' "host uvicorn[812]: $bare" "host uvicorn[812]:    $bare" >"$tmp/undated-journal.log"
out="$(fail2ban-regex "$tmp/undated-journal.log" "$filter" 2>&1)"
echo "$out" | grep -qE '^Lines: 2 lines, 0 ignored, 0 matched, 2 missed' \
    || { echo "an undated journal entry must not match" >&2; echo "$out" >&2; exit 1; }

# A line long enough for journald (LineMax, 48 KiB) or Docker (16 KiB) to split, with
# the forged line aligned on the split: rendered by the app's own formatter
# (app/log_redaction.py, stdlib only), it is capped and stays one entry; rendered
# with the cap lifted, its second entry is the forged line (positive control).
python3 -I - "$here/app/log_redaction.py" "$tmp" "$forged" <<'PY'
import importlib.util, logging, sys
spec = importlib.util.spec_from_file_location("log_redaction", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
tmp, forged = sys.argv[2], sys.argv[3]
fmt = mod.RedactingFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s", "%Y-%m-%d %H:%M:%S")
)
def render(name):
    record = logging.LogRecord("meshloom.app", logging.INFO, "x", 1, "Toggling blocked name: %s", (name,), None)
    return fmt.format(record)
def entries(text, line_max):
    for line in text.split("\n"):
        raw = line.encode()
        for start in range(0, max(len(raw), 1), line_max):
            yield "host uvicorn[812]: " + raw[start : start + line_max].decode("utf-8", "replace")
capped, uncapped = [], []
for line_max in (48 * 1024, 16 * 1024):
    name = "A" * (line_max - len(render("").encode())) + forged
    capped += entries(render(name), line_max)
    cap, mod.MAX_LINE_BYTES = mod.MAX_LINE_BYTES, 10**9
    uncapped += entries(render(name), line_max)
    mod.MAX_LINE_BYTES = cap
for name, lines in (("capped", capped), ("uncapped", uncapped)):
    with open(f"{tmp}/{name}.log", "w") as out:
        out.write("\n".join(lines) + "\n")
PY
out="$(fail2ban-regex "$tmp/capped.log" "$filter" 2>&1)"
echo "$out" | grep -qE '^Lines: 2 lines, 0 ignored, 0 matched, 2 missed' \
    || { echo "a capped long line must stay one entry and not match" >&2; echo "$out" >&2; exit 1; }
out="$(fail2ban-regex "$tmp/uncapped.log" "$filter" 2>&1)"
echo "$out" | grep -qE '^Lines: 4 lines, 0 ignored, 2 matched, 2 missed' \
    || { echo "the uncapped split does not match: the check proves nothing" >&2; echo "$out" >&2; exit 1; }

# The jail: disabled, and the whole configuration parses with the filter in place.
grep -qE '^enabled *= *false$' "$jail" || { echo "jail is not shipped disabled" >&2; exit 1; }
cfg="$tmp/fail2ban"
mkdir -p "$cfg"
cp -r /etc/fail2ban/. "$cfg/"
cp "$filter" "$cfg/filter.d/meshloom.conf"
cp "$jail" "$cfg/jail.d/meshloom.conf"
fail2ban-client -c "$cfg" -t >/dev/null
echo "fail2ban filter and jail OK"
