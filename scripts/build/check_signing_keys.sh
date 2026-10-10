#!/usr/bin/env bash
# Validate the release signing trust anchor in pkg/keys/.
#
# Strict (default): fails while pkg/keys holds placeholders, or when the
# FINGERPRINT / keyring / armored key / CI secret disagree. Release jobs use this.
# --allow-placeholder: placeholders are reported and tolerated (pull requests);
# a real key is still fully validated.
#
# Usage: scripts/build/check_signing_keys.sh [--allow-placeholder]
#                                            [--secret-gnupghome DIR]
#   --secret-gnupghome DIR  GNUPGHOME holding the imported CI secret key; its
#                           primary fingerprint must equal FINGERPRINT and it
#                           must hold a usable signing subkey secret.
# Env: MESHLOOM_KEYS_DIR overrides the pkg/keys location (used by the tests).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
KEYS_DIR="${MESHLOOM_KEYS_DIR:-$SCRIPT_DIR/../../pkg/keys}"
ALLOW_PLACEHOLDER=0
SECRET_HOME=""

while [ $# -gt 0 ]; do
    case "$1" in
        --allow-placeholder) ALLOW_PLACEHOLDER=1; shift ;;
        --secret-gnupghome) SECRET_HOME="${2:-}"; shift 2 ;;
        -h | --help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; exit 2 ;;
    esac
done

die() { echo "check_signing_keys: $*" >&2; exit 1; }
# GitHub annotates ::error:: lines when run in Actions; harmless elsewhere.
fail() { [ -z "${GITHUB_ACTIONS:-}" ] || echo "::error::$*"; die "$*"; }

GPG_BIN="gpg"
command -v gpg >/dev/null 2>&1 || die "gpg is required"

FPR_FILE="$KEYS_DIR/FINGERPRINT"
GPG_FILE="$KEYS_DIR/meshloom-archive-keyring.gpg"
ASC_FILE="$KEYS_DIR/meshloom.asc"
for f in "$FPR_FILE" "$GPG_FILE" "$ASC_FILE"; do
    [ -f "$f" ] || fail "missing $f (see pkg/keys/README.md)"
done

FPR="$(tr -d ' \t\r\n' <"$FPR_FILE")"
if ! [[ $FPR =~ ^[0-9A-F]{40}$ ]] || grep -q 'MESHLOOM-KEY-PLACEHOLDER' "$GPG_FILE" "$ASC_FILE"; then
    if [ "$ALLOW_PLACEHOLDER" -eq 1 ]; then
        echo "check_signing_keys: pkg/keys holds PLACEHOLDERS (tolerated here; release jobs refuse them)."
        exit 0
    fi
    fail "pkg/keys still holds placeholders: the release signing key has not been created/committed (pkg/keys/README.md)"
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
chmod 700 "$WORK"
export GNUPGHOME="$WORK"

# Primary fingerprints (and signing-subkey validity) of a key file.
primaries() { $GPG_BIN --batch --show-keys --with-colons "$1" 2>/dev/null |
    awk -F: '/^pub:/ {want=1; next} /^fpr:/ && want {print $10; want=0}'; }

primaries "$GPG_FILE" | grep -qx "$FPR" || fail "FINGERPRINT $FPR not found in meshloom-archive-keyring.gpg"
primaries "$ASC_FILE" | grep -qx "$FPR" || fail "FINGERPRINT $FPR not found in meshloom.asc"

# Keyring and armored file must describe the same set of primary keys.
[ "$(primaries "$GPG_FILE" | sort)" = "$(primaries "$ASC_FILE" | sort)" ] ||
    fail "meshloom-archive-keyring.gpg and meshloom.asc contain different keys"

# The primary of FINGERPRINT must be valid (not expired/revoked) and own a valid
# signing subkey. Colon fields: 2 validity (e=expired r=revoked), 12 capabilities.
NOW="$(date +%s)"
$GPG_BIN --batch --show-keys --with-colons "$GPG_FILE" 2>/dev/null | awk -F: -v fpr="$FPR" -v now="$NOW" '
    /^pub:/ { inkey = 0; pubv = $2; pubexp = $7; next_is_pub = 1; next }
    /^fpr:/ && next_is_pub { inkey = ($10 == fpr); next_is_pub = 0
        if (inkey && (pubv == "r" || pubv == "e" || (pubexp != "" && pubexp + 0 < now))) bad = "primary key revoked or expired"
        next }
    /^sub:/ {
        if (inkey && $12 ~ /s/ && $2 != "r" && $2 != "e" && !($7 != "" && $7 + 0 < now)) found = 1
        next }
    END { if (bad != "") { print bad > "/dev/stderr"; exit 1 }
          if (!found) { print "no valid signing subkey for the primary key" > "/dev/stderr"; exit 1 } }
' || fail "key $FPR is not usable for signing (expired/revoked or no signing subkey)"

if [ -n "$SECRET_HOME" ]; then
    # The CI secret must be the private half of the committed public key.
    SEC="$(GNUPGHOME="$SECRET_HOME" $GPG_BIN --batch --list-secret-keys --with-colons 2>/dev/null |
        awk -F: '/^sec[#:]/ {want=1; next} /^fpr:/ && want {print $10; want=0}')"
    printf '%s\n' "$SEC" | grep -qx "$FPR" ||
        fail "CI secret key fingerprint ($(echo "$SEC" | tr '\n' ' ')) does not match pkg/keys/FINGERPRINT ($FPR)"
    GNUPGHOME="$SECRET_HOME" $GPG_BIN --batch --list-secret-keys --with-colons 2>/dev/null |
        awk -F: '/^ssb:/ && $12 ~ /s/ && $2 != "r" && $2 != "e" {ok=1} END {exit !ok}' ||
        fail "CI secret holds no usable signing subkey"
fi

echo "check_signing_keys: OK (fingerprint $FPR)"
