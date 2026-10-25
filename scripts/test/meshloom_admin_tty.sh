#!/bin/sh
# The test commands are single-quoted on purpose: script(1) expands them.
# shellcheck disable=SC2016
#
# meshloom-admin must never hand root's controlling terminal to the `meshloom`
# child, whatever is piped or redirected: with it, the child could push input
# into root's terminal (TIOCSTI) through an inherited tty fd or /dev/tty.
#
# Runs the real wrapper (pkg/nfpm/meshloom-admin) with the real runuser, as
# root, against a fake install whose "python" is a probe. Each case runs under
# script(1), so the caller has a controlling terminal like an operator in a
# shell. It also checks that exit codes, stdin piping and stderr separation are
# kept. Needs Linux, root, util-linux (runuser, script), python3 and a
# `meshloom` user. CI: .github/workflows/nfpm.yml, after the package check.
#
# Usage: sudo scripts/test/meshloom_admin_tty.sh
set -eu

here=$(cd "$(dirname "$0")" && pwd)
wrapper="$here/../../pkg/nfpm/meshloom-admin"
python3=$(command -v python3)

[ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
id meshloom >/dev/null 2>&1 || { echo "needs a meshloom user" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/opt/.venv/bin"

# The probe says whether it shares the caller's controlling terminal
# (ROOT_CTTY), which of fds 0-2 are ttys and what it read, then exits 7.
cat >"$work/probe.py" <<'EOF'
import os, sys
ctty = open("/proc/self/stat").read().rsplit(")", 1)[1].split()[4]
shared = ctty != "0" and ctty == os.environ.get("ROOT_CTTY")
line = sys.stdin.readline().strip() if "--read" in sys.argv else "-"
ttys = "".join(str(int(os.isatty(f))) for f in (0, 1, 2))
out = sys.stderr if "--err" in sys.argv else sys.stdout
print(f"probe shared={shared} ttys={ttys} read={line}", file=out)
sys.exit(7)
EOF
printf '#!/bin/sh\nexec %s %s "$@"\n' "$python3" "$work/probe.py" >"$work/opt/.venv/bin/python"
chmod -R a+rX "$work"
chmod 755 "$work/opt/.venv/bin/python"

export MESHLOOM_PACKAGE_ROOT="$work/opt" MESHLOOM_ENV_FILE="$work/none.env"
fail=0

# check <label> <command> [<expected substring>...]: the command runs in a new
# terminal, $W is the wrapper, $T a scratch file. The output must hold every
# expected substring; with --no-probe, it must not show the probe at all.
check() {
    label=$1 cmd=$2
    shift 2
    out=$(SHELL=/bin/sh script -qec \
        "ROOT_CTTY=\$(cut -d' ' -f7 /proc/\$\$/stat); export ROOT_CTTY; W='sh $wrapper'; T='$work/t'; $cmd" \
        /dev/null </dev/null | tr -d '\r')
    for want in "$@"; do
        case "$want:$out" in
            --no-probe:*probe*) echo "FAIL $label: unexpected output: $out"; fail=1; return ;;
            --no-probe:*) ;;
            *:*"$want"*) ;;
            *) echo "FAIL $label: missing '$want' in: $out"; fail=1; return ;;
        esac
    done
    echo "ok   $label"
}

# Fully interactive: the child gets a pty of its own.
check "interactive" '$W x; echo rc=$?' "shared=False ttys=111" "rc=7"
# Partly redirected: the tty fds left are not the child's controlling terminal.
check "stdout piped" '$W x | cat' "shared=False ttys=101"
check "stdin piped" 'echo hello | $W x --read; echo rc=$?' "shared=False ttys=011 read=hello" "rc=7"
check "stderr redirected" '$W x 2>/dev/null; echo rc=$?' "shared=False ttys=110" "rc=7"
check "stderr kept off stdout" '$W x --err 2>/dev/null; echo rc=$?' "rc=7" --no-probe
check "stdout and stderr piped" '$W x --err 2>&1 | cat' "shared=False ttys=100"
# Nothing on a tty: the child still must not reach root's terminal via /dev/tty.
check "all redirected" '$W x --err </dev/null >/dev/null 2>"$T"; echo rc=$?; cat "$T"' \
    "shared=False ttys=000" "rc=7"

exit "$fail"
