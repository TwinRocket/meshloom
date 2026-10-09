#!/bin/sh
# Meshloom container entrypoint.
#
# Default: run the command unchanged, as root, exactly like images up to 4.17.
# Opt-in: MESHLOOM_RUN_AS_USER=<uid> (the installer uses 10001) drops to that
# uid with setpriv. Before dropping, /app/data is handed to that uid and the
# groups of every mapped serial device are added. If a mapped serial device is
# still not usable by that uid, Meshloom keeps running as root with a warning:
# a radio that works beats a stricter sandbox that cannot reach it.
#
# The Home Assistant add-on replaces CMD; with the variable unset this script
# only execs it.
set -eu

R=
if [ "${MESHLOOM_HELPER_TESTING:-}" = 1 ]; then
    R="${MESHLOOM_HELPER_ROOT:-}"
fi

warn() {
    echo "meshloom entrypoint: $*" >&2
}

target="${MESHLOOM_RUN_AS_USER:-}"
if [ -z "$target" ]; then
    exec "$@"
fi
if [ "$(id -u)" != 0 ]; then
    # Already started as non-root (compose user:), nothing to drop.
    exec "$@"
fi
case "$target" in
    '' | *[!0-9]*)
        warn "MESHLOOM_RUN_AS_USER must be a numeric uid; staying root"
        exec "$@"
        ;;
esac
if [ "$target" -eq 0 ] || [ "$target" -gt 4294967294 ]; then
    exec "$@"
fi

data_dir="$R/app/data"
mkdir -p "$data_dir"
# Only what is not already ours; -h never follows a symlink out of the volume.
find "$data_dir" \( ! -user "$target" -o ! -group "$target" \) -exec chown -h "$target:$target" {} + 2>/dev/null || true

devices=
gids=
for dev in "$R"/dev/meshcore-radio "$R"/dev/ttyUSB* "$R"/dev/ttyACM* "$R"/dev/ttyAMA* "$R"/dev/rfcomm*; do
    [ -c "$dev" ] || continue
    devices="$devices $dev"
    gid=$(stat -L -c %g "$dev")
    case ",$gids," in
        *",$gid,"*) ;;
        *) gids="${gids:+$gids,}$gid" ;;
    esac
done

if [ -n "$gids" ]; then
    groups_opt="--groups=$gids"
else
    groups_opt="--clear-groups"
fi

for dev in $devices; do
    if ! setpriv --reuid="$target" --regid="$target" "$groups_opt" -- test -r "$dev" -a -w "$dev"; then
        warn "uid $target cannot open $dev; running as root instead (unset MESHLOOM_RUN_AS_USER to silence this)"
        exec "$@"
    fi
done

home="$R/tmp/meshloom-home"
mkdir -p "$home"
chown -h "$target:$target" "$home" 2>/dev/null || true
export HOME="$home"
# The venv belongs to root and is complete in the image: never re-sync it.
export UV_NO_SYNC=1
export UV_CACHE_DIR="$home/.cache/uv"

exec setpriv --reuid="$target" --regid="$target" "$groups_opt" --inh-caps=-all -- "$@"
